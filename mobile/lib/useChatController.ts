import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as Clipboard from "expo-clipboard";
import * as ImagePicker from "expo-image-picker";

import { apiFetch, apiFetchStreaming, deleteChatPair, getBackendUrl, loadSettings, loadWorkbenchLatest } from "@/lib/api";
import { parseChatSseEvent, splitSseBuffer } from "@/lib/chatSse";
import { loadSoundVolume, soundEngine } from "@/lib/soundEngine";
import type { DraftAttachment, HistoryPair, Message } from "@/lib/types";

const HISTORY_BATCH = 25;
const MAX_IMAGES = 4;

const VISION_MODELS = new Set([
  "anthropic/claude-opus-4.6",
  "openai/gpt-5.1",
  "openai/gpt-5.4",
]);

function makeId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function pairToMessages(pair: HistoryPair, baseUrl: string): Message[] {
  const normalizedBase = baseUrl.replace(/\/$/, "");
  const userImageUrls = pair.user_image_urls?.length
    ? pair.user_image_urls
        .map((uri) => (uri.startsWith("http") ? uri : `${normalizedBase}${uri}`))
        .filter(Boolean)
    : undefined;

  const ts = pair.pair_created_at ?? pair.created_at ?? undefined;
  const out: Message[] = [];
  if (pair.user_text || pair.user_image_urls?.length) {
    out.push({
      id: `${pair.pair_id}-user`,
      role: "user",
      content: pair.user_text ?? "",
      pairId: pair.pair_id,
      imageUrls: userImageUrls,
      createdAt: ts,
    });
  }
  if (pair.assistant_text) {
    out.push({
      id: `${pair.pair_id}-assistant`,
      role: "assistant",
      content: pair.assistant_text,
      pairId: pair.pair_id,
      createdAt: ts,
    });
  }
  return out;
}

export function useChatController() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [errorNotice, setErrorNotice] = useState<string | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [cursor, setCursor] = useState<string | null>(null);
  const [initialLoaded, setInitialLoaded] = useState(false);
  const [aiName, setAiName] = useState("CHAT");
  const [workbenchText, setWorkbenchText] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<DraftAttachment[]>([]);
  const [canAttach, setCanAttach] = useState(false);
  const [backendUrl, setBackendUrl] = useState("");

  const abortRef = useRef<AbortController | null>(null);
  const chunkBufRef = useRef("");
  const rafRef = useRef<ReturnType<typeof requestAnimationFrame> | null>(null);
  const activeAssistantIdRef = useRef<string | null>(null);
  const activePairIdRef = useRef<string | null>(null);
  const loadingHistoryRef = useRef(false);

  const reversedMessages = useMemo(() => [...messages].reverse(), [messages]);
  const readyAttachments = useMemo(
    () => attachments.filter((attachment) => attachment.status === "uploaded" && attachment.serverUrl),
    [attachments],
  );
  const hasUploadingAttachments = useMemo(
    () => attachments.some((attachment) => attachment.status === "uploading"),
    [attachments],
  );
  const canSend = useMemo(
    () => !hasUploadingAttachments && (Boolean(input.trim()) || readyAttachments.length > 0),
    [hasUploadingAttachments, input, readyAttachments.length],
  );

  const updateMessageById = useCallback((messageId: string, updater: (message: Message) => Message) => {
    setMessages((prev) =>
      prev.map((message) => (message.id === messageId ? updater(message) : message)),
    );
  }, []);

  const flushChunkBuf = useCallback(() => {
    rafRef.current = null;
    const assistantId = activeAssistantIdRef.current;
    const text = chunkBufRef.current;
    if (!assistantId || !text) return;
    chunkBufRef.current = "";
    updateMessageById(assistantId, (message) => ({ ...message, content: message.content + text }));
  }, [updateMessageById]);

  const scheduleFlush = useCallback(() => {
    if (rafRef.current === null) {
      rafRef.current = requestAnimationFrame(flushChunkBuf);
    }
  }, [flushChunkBuf]);

  const flushNow = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    flushChunkBuf();
  }, [flushChunkBuf]);

  const loadHistory = useCallback(async (before?: string | null) => {
    if (loadingHistoryRef.current) return;
    loadingHistoryRef.current = true;
    setLoadingHistory(true);
    try {
      const params = new URLSearchParams({ account_id: "default", limit_pairs: String(HISTORY_BATCH) });
      if (before) params.set("before", before);

      const response = await apiFetch(`/api/chat/history?${params}`);
      if (!response.ok) throw new Error(response.status === 401 ? "auth" : `${response.status}`);

      const baseUrl = (await getBackendUrl()).replace(/\/$/, "");
      const data = await response.json() as {
        pairs: HistoryPair[];
        next_before?: string | null;
        has_more: boolean;
      };
      const loaded = data.pairs.flatMap((pair) => pairToMessages(pair, baseUrl));

      setMessages((prev) => (before ? [...loaded, ...prev] : loaded));
      setCursor(data.next_before ?? null);
      setHasMore(Boolean(data.has_more));
      if (!before) setInitialLoaded(true);
    } catch (error) {
      console.warn("[chat] loadHistory error:", error);
      if (!before) setInitialLoaded(true);
    } finally {
      loadingHistoryRef.current = false;
      setLoadingHistory(false);
    }
  }, []);

  const refreshWorkbench = useCallback(() => {
    loadWorkbenchLatest()
      .then((result) => setWorkbenchText(result.text ?? null))
      .catch(() => {});
  }, []);

  useEffect(() => {
    getBackendUrl()
      .then((url) => setBackendUrl(url.replace(/\/$/, "")))
      .catch(() => {});
    void loadHistory(null);
    loadSettings()
      .then((settings) => {
        if (settings.ai_name) setAiName(settings.ai_name.toUpperCase());
        if (settings.model) setCanAttach(VISION_MODELS.has(settings.model));
      })
      .catch(() => {});
    refreshWorkbench();

    // Load sound engine with persisted volume
    loadSoundVolume()
      .then((vol) => soundEngine.load(vol))
      .catch(() => {});

    return () => {
      soundEngine.unload().catch(() => {});
    };
  }, [loadHistory]);

  const pickImages = useCallback(async () => {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== "granted") return;

    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      allowsMultipleSelection: true,
      quality: 0.85,
      selectionLimit: MAX_IMAGES - attachments.length,
    });

    if (result.canceled) return;

    const drafts: DraftAttachment[] = result.assets.map((asset, index) => ({
      id: makeId(`attachment-${index}`),
      localUri: asset.uri,
      mimeType: asset.mimeType ?? "image/jpeg",
      fileName: asset.fileName ?? `image_${index}.jpg`,
      status: "uploading",
    }));

    setAttachments((prev) => [...prev, ...drafts].slice(0, MAX_IMAGES));

    for (const draft of drafts) {
      const form = new FormData();
      form.append("image", {
        uri: draft.localUri,
        name: draft.fileName,
        type: draft.mimeType,
      } as any);

      try {
        const response = await apiFetch("/api/upload", { method: "POST", body: form });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json() as { url: string };
        setAttachments((prev) =>
          prev.map((attachment) =>
            attachment.id === draft.id
              ? { ...attachment, serverUrl: data.url, status: "uploaded" }
              : attachment,
          ),
        );
      } catch {
        setAttachments((prev) =>
          prev.map((attachment) =>
            attachment.id === draft.id ? { ...attachment, status: "failed" } : attachment,
          ),
        );
      }
    }
  }, [attachments.length]);

  const removeAttachment = useCallback((attachmentId: string) => {
    setAttachments((prev) => prev.filter((attachment) => attachment.id !== attachmentId));
  }, []);

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    const uploaded = attachments.filter((attachment) => attachment.status === "uploaded" && attachment.serverUrl);
    if ((!text && uploaded.length === 0) || streaming || hasUploadingAttachments) return;

    const userMessageId = makeId("user");
    const assistantMessageId = makeId("assistant");
    const serverUrls = uploaded.map((attachment) => attachment.serverUrl!);
    const resolvedBackendUrl = backendUrl || (await getBackendUrl()).replace(/\/$/, "");
    const fullImageUrls = serverUrls.map((uri) => (uri.startsWith("http") ? uri : `${resolvedBackendUrl}${uri}`));
    const now = new Date().toISOString();
    const userMessage: Message = {
      id: userMessageId,
      role: "user",
      content: text,
      imageUrls: fullImageUrls.length ? fullImageUrls : undefined,
      createdAt: now,
    };

    setMessages((prev) => [
      ...prev,
      userMessage,
      { id: assistantMessageId, role: "assistant", content: "" },
    ]);
    setInput("");
    setAttachments([]);
    setStreaming(true);
    activeAssistantIdRef.current = assistantMessageId;
    activePairIdRef.current = null;

    try {
      abortRef.current = new AbortController();
      const payloadMessages = [...messages, userMessage].map((message) => ({
        role: message.role,
        content: message.content,
      }));
      const params = new URLSearchParams();
      params.append("messages", JSON.stringify(payloadMessages));
      params.append("web_search", "false");
      params.append("account_id", "default");
      if (serverUrls.length) {
        params.append("image_urls", JSON.stringify(serverUrls));
      }

      const response = await apiFetchStreaming("/api/chat", {
        method: "POST",
        body: params.toString(),
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        signal: abortRef.current.signal,
      });

      if (!response.ok) {
        throw new Error(response.status === 401 ? "Auth failed — reconnect in Settings" : `HTTP ${response.status}`);
      }
      if (!response.body) {
        throw new Error("Streaming body is unavailable");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let sseBuffer = "";
      let streamDone = false;

      while (!streamDone) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = typeof value === "string" ? value : decoder.decode(value, { stream: true });
        sseBuffer += chunk;

        const { events, remainder } = splitSseBuffer(sseBuffer);
        sseBuffer = remainder;

        for (const rawEvent of events) {
          const event = parseChatSseEvent(rawEvent);
          if (!event) continue;

          if (event.type === "done") {
            soundEngine.endMessage();
            streamDone = true;
            break;
          }

          if (event.type === "pair_id") {
            activePairIdRef.current = event.pairId;
            continue;
          }

          if (event.type === "skip") continue;

          if (event.type === "rewrite") {
            flushNow();
            updateMessageById(assistantMessageId, (message) => ({ ...message, content: event.text }));
            continue;
          }

          if (event.type === "memory") {
            updateMessageById(assistantMessageId, (message) => ({
              ...message,
              chromaFacts: event.chromaFacts,
            }));
            continue;
          }

          if (event.type === "image_start") {
            flushNow();
            const shimmerCmd = `[GENERATE_IMAGE: ${event.prompt}]`;
            updateMessageById(assistantMessageId, (message) => ({
              ...message,
              content: message.content.trimEnd() + "\n" + shimmerCmd,
            }));
            continue;
          }

          if (event.type === "image_cancel") {
            flushNow();
            updateMessageById(assistantMessageId, (message) => ({
              ...message,
              content: message.content.replace(/\[GENERATE_IMAGE:[^\]]*\]/g, "").trimEnd(),
            }));
            continue;
          }

          if (event.type === "image_ready") {
            flushNow();
            const marker = `[GENERATED_IMAGE: ${event.path} | ${event.model} | ${event.prompt}]`;
            updateMessageById(assistantMessageId, (message) => {
              if (message.content.includes(`[GENERATED_IMAGE: ${event.path}`)) return message;
              const cleaned = message.content.replace(/\[GENERATE_IMAGE:[^\]]*\]/g, "");
              return { ...message, content: cleaned.trimEnd() + "\n" + marker };
            });
            continue;
          }

          chunkBufRef.current += event.chunk;
          soundEngine.feed(event.chunk);
          scheduleFlush();
        }
      }

      flushNow();
    } catch (error: unknown) {
      if (error instanceof Error && error.name === "AbortError") return;
      flushNow();

      // Remove both messages from UI
      setMessages((prev) =>
        prev.filter((m) => m.id !== userMessageId && m.id !== assistantMessageId),
      );

      // Try to delete from backend DB (best-effort, don't await result)
      // activePairIdRef is set from the first SSE event, so we always have the correct pair
      const pairId = activePairIdRef.current;
      if (pairId) {
        deleteChatPair(pairId).catch(() => {});
        activePairIdRef.current = null;
      }

      // Copy user text to clipboard so nothing is lost
      if (text) {
        Clipboard.setStringAsync(text).catch(() => {});
      }

      // Ambient error notice — auto-clears after 4s
      setErrorNotice("connection error — your message was copied");
      setTimeout(() => setErrorNotice(null), 4000);
    } finally {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      chunkBufRef.current = "";
      activeAssistantIdRef.current = null;
      activePairIdRef.current = null;
      setStreaming(false);
      abortRef.current = null;
    }
  }, [
    attachments,
    backendUrl,
    flushNow,
    hasUploadingAttachments,
    input,
    messages,
    scheduleFlush,
    streaming,
    updateMessageById,
  ]);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    setStreaming(false);
  }, []);

  const loadMore = useCallback(() => {
    if (hasMore && !loadingHistory) {
      void loadHistory(cursor);
    }
  }, [cursor, hasMore, loadHistory, loadingHistory]);

  return {
    aiName,
    attachments,
    backendUrl,
    canAttach,
    canSend,
    errorNotice,
    hasMore,
    initialLoaded,
    input,
    loadingHistory,
    messages,
    reversedMessages,
    streaming,
    workbenchText,
    refreshWorkbench,
    setInput,
    pickImages,
    removeAttachment,
    sendMessage,
    stopStreaming,
    loadMore,
  };
}
