import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppState } from "react-native";
import * as ImagePicker from "expo-image-picker";

import { apiFetch, apiFetchStreaming, getBackendUrl, loadSettings, loadWorkbenchLatest } from "@/lib/api";
import { apiErrorFrom, describeApiError } from "@/lib/apiError";
import { subscribeToChanges } from "@/lib/changeFeed";
import { consumeChatStream } from "@/lib/chatStream";
import { latestCursor, mergeMessages } from "@/lib/mergeMessages";
import { soundEngine } from "@/lib/soundEngine";
import type { DraftAttachment, HistoryPair, Message } from "@/lib/types";

const HISTORY_BATCH = 25;
const MAX_IMAGES = 4;

const VISION_MODELS = new Set([
  "~anthropic/claude-fable-latest",
  "~moonshotai/kimi-latest",
  "~google/gemini-pro-latest",
  "openai/gpt-chat-latest",
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
  const [historyError, setHistoryError] = useState<string | null>(null);
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
      if (!response.ok) throw await apiErrorFrom(response);

      const baseUrl = (await getBackendUrl()).replace(/\/$/, "");
      const data = await response.json() as {
        pairs: HistoryPair[];
        next_before?: string | null;
        has_more: boolean;
      };
      const loaded = data.pairs.flatMap((pair) => pairToMessages(pair, baseUrl));

      // Merge rather than replace: a reply may be streaming into the list
      // right now, and the desktop does the same so both converge alike.
      setMessages((prev) => (before ? [...loaded, ...prev] : mergeMessages(prev, loaded)));
      setCursor(data.next_before ?? null);
      setHasMore(Boolean(data.has_more));
      if (!before) setInitialLoaded(true);
      if (!before) setHistoryError(null);
    } catch (error) {
      console.warn("[chat] loadHistory error:", error);
      // Only the first page can be mistaken for an empty conversation; a failed
      // "load older" leaves what is already on screen alone.
      if (!before) {
        setHistoryError(describeApiError(error));
        setInitialLoaded(true);
      }
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

  /**
   * Re-read what the server thinks the model and the name are.
   *
   * Read once at mount used to mean: switch to a vision model in Settings, come
   * back, and the attach button is still missing — the chat screen stays in the
   * stack while Settings sits on top of it, so nothing remounts and nothing
   * re-asked. `setCanAttach` is now unconditional, so switching *away* from a
   * vision model hides the button too.
   *
   * Called by whoever knows when the screen is being looked at: `useFocusEffect`
   * in app/chat.tsx, and the AppState listener below for a return from the
   * background. Not called here on mount — focus fires on mount, and doing both
   * is one wasted request every time the chat opens.
   */
  const refreshSettings = useCallback(() => {
    loadSettings()
      .then((settings) => {
        if (settings.ai_name) setAiName(settings.ai_name.toUpperCase());
        setCanAttach(settings.model ? VISION_MODELS.has(settings.model) : false);
      })
      .catch(() => {});
  }, []);

  // ── Staying in step with the server ───────────────────────────────────────

  // Read inside syncNew without making it depend on every keystroke of state.
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const syncingRef = useRef(false);

  /** Ask for anything stored since the newest thing on screen, and fold it in. */
  const syncNew = useCallback(async () => {
    if (syncingRef.current) return;
    const cursor = latestCursor(messagesRef.current);
    // Nothing timestamped yet means the first page never landed; that is
    // loadHistory's job, and doing it here would fight with it.
    if (!cursor) return;

    syncingRef.current = true;
    try {
      const params = new URLSearchParams({
        account_id: "default",
        limit_pairs: String(HISTORY_BATCH),
        after: cursor,
      });
      const response = await apiFetch(`/api/chat/history?${params}`);
      if (!response.ok) return;
      const baseUrl = (await getBackendUrl()).replace(/\/$/, "");
      const data = (await response.json()) as { pairs: HistoryPair[] };
      if (!data.pairs.length) return;
      const loaded = data.pairs.flatMap((pair) => pairToMessages(pair, baseUrl));
      setMessages((prev) => mergeMessages(prev, loaded));
    } catch {
      // Offline, or a stale token. The next trigger tries again; a failed
      // background sync is not something to interrupt a conversation with.
    } finally {
      syncingRef.current = false;
    }
  }, []);

  // Three triggers, one path. Returning to the app covers what happened while
  // it was in the background; the feed covers a message arriving while it is on
  // screen — which is what the assistant writing on its own schedule does, and
  // what a push notification cannot deliver to an app already open.
  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) => {
      if (state !== "active") return;
      void syncNew();
      refreshSettings();
    });
    const feed = subscribeToChanges(() => void syncNew());
    return () => {
      subscription.remove();
      feed.close();
    };
  }, [syncNew, refreshSettings]);

  useEffect(() => {
    getBackendUrl()
      .then((url) => setBackendUrl(url.replace(/\/$/, "")))
      .catch(() => {});
    void loadHistory(null);
    refreshWorkbench();

    // The engine reads its own persisted volume and keeps its assets for the
    // life of the process. Leaving the screen only silences the queue — it used
    // to unload the assets, and that raced the next mount's load().
    void soundEngine.prime();

    return () => {
      soundEngine.stop();
      // Deliberately no abort here. The screen unmounting means someone walked
      // away mid-reply; the request finishing is how the backend saves the whole
      // thing, and it is waiting in history when they come back. Aborting would
      // trade a complete reply for a clipped one to save a few seconds of socket.
    };
  }, [loadHistory, refreshWorkbench]);

  const pickImages = useCallback(async () => {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== "granted") return;

    const result = await ImagePicker.launchImageLibraryAsync({
      // `MediaTypeOptions` is @deprecated in the installed version.
      mediaTypes: ["images"],
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
      // Read through the ref, not the closure. With `messages` in this
      // callback's dependencies the whole of `sendMessage` was rebuilt on every
      // animation frame of a stream, and `ChatComposer` — which takes it as a
      // prop — re-rendered with it, TextInput and all, dozens of times a second.
      const payloadMessages = [...messagesRef.current, userMessage].map((message) => ({
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

      // The server often knows exactly what is wrong — a 503 carries a hint
      // saying the backend is up and Postgres is not, which is the opposite of
      // what a bare status suggests.
      if (!response.ok) throw await apiErrorFrom(response);
      if (!response.body) {
        throw new Error("Streaming body is unavailable");
      }

      // The stream's lifecycle lives in lib/chatStream.ts. Its outcome is the
      // point of this whole call: `done` means the terminator arrived and the
      // reply is whole; anything else means it is not — a distinction the loop
      // that used to be here could not make, so half a reply was stored, shown
      // and remembered as if it were the answer.
      const result = await consumeChatStream(response.body.getReader(), (event) => {
        if (event.type === "pair_id") {
          activePairIdRef.current = event.pairId;
          // Stamp the optimistic pair so a later sync recognises it as the
          // same thing the server stored, instead of showing it twice.
          setMessages((prev) => {
            const updated = [...prev];
            for (let i = updated.length - 1, tagged = 0; i >= 0 && tagged < 2; i -= 1) {
              if (updated[i].pairId) break;
              updated[i] = { ...updated[i], pairId: event.pairId };
              tagged += 1;
            }
            return updated;
          });
          return;
        }

        if (event.type === "skip") return;

        // Mirrors frontend/lib/useChatController.ts: the notice goes into the
        // bubble so the transcript records that the reply was cut off, not just
        // a toast that disappears. Until recently this frame reached the phone
        // as reply text and printed its JSON.
        if (event.type === "error") {
          flushNow();
          updateMessageById(assistantMessageId, (message) => ({
            ...message,
            content:
              message.content.trimEnd() +
              (event.message
                ? `\n\n[ответ оборван: ${event.message}]`
                : "\n\n[ответ оборван]"),
          }));
          return;
        }

        if (event.type === "rewrite") {
          flushNow();
          updateMessageById(assistantMessageId, (message) => ({ ...message, content: event.text }));
          return;
        }

        if (event.type === "memory") {
          updateMessageById(assistantMessageId, (message) => ({
            ...message,
            chromaFacts: event.chromaFacts,
          }));
          return;
        }

        if (event.type === "image_start") {
          flushNow();
          const shimmerCmd = `[GENERATE_IMAGE: ${event.prompt}]`;
          updateMessageById(assistantMessageId, (message) => ({
            ...message,
            content: message.content.trimEnd() + "\n" + shimmerCmd,
          }));
          return;
        }

        if (event.type === "image_cancel") {
          flushNow();
          updateMessageById(assistantMessageId, (message) => ({
            ...message,
            content: message.content.replace(/\[GENERATE_IMAGE:[^\]]*\]/g, "").trimEnd(),
          }));
          return;
        }

        if (event.type === "image_ready") {
          flushNow();
          const marker = `[GENERATED_IMAGE: ${event.path} | ${event.model} | ${event.prompt}]`;
          updateMessageById(assistantMessageId, (message) => {
            if (message.content.includes(`[GENERATED_IMAGE: ${event.path}`)) return message;
            const cleaned = message.content.replace(/\[GENERATE_IMAGE:[^\]]*\]/g, "");
            return { ...message, content: cleaned.trimEnd() + "\n" + marker };
          });
          return;
        }

        chunkBufRef.current += event.chunk;
        soundEngine.feed(event.chunk);
        scheduleFlush();
      });

      flushNow();

      if (result.outcome === "done") {
        soundEngine.endMessage();
        return;
      }

      // The reply stopped short. What is on screen is what the backend also
      // kept: `_save_partial` (api/chat.py) stores the streamed text when the
      // client hangs up, and the user's own row was written before the stream
      // even opened. So the honest move is to keep it and say why it is short.
      //
      // The old code deleted both messages and sent DELETE /api/chat/pair — a
      // race against that very save, which could land after the delete and
      // leave an orphaned half-reply that reappeared through the change feed a
      // moment later. Not deleting removes the race rather than fixing it.
      updateMessageById(assistantMessageId, (message) => ({
        ...message,
        interrupted: result.outcome === "aborted" ? "stopped" : "connection",
      }));
      if (result.outcome !== "aborted") {
        setErrorNotice("connection lost — the reply was kept");
        setTimeout(() => setErrorNotice(null), 4000);
      }
    } catch (error: unknown) {
      // Only reachable before the stream opened: a refused connection, a bad
      // status, a body that never came.
      //
      // One rule across both failure paths and both clients: nothing the person
      // can see is deleted, and the bubble says what happened. This used to
      // delete both messages and drop the text into the clipboard — the phone's
      // own invention, and the worse half of it, since the clipboard is a
      // shared thing to stomp on and the composer is not.
      if (error instanceof Error && error.name === "AbortError") return;
      flushNow();

      updateMessageById(assistantMessageId, (message) => ({
        ...message,
        content: `[${describeApiError(error)}]`,
      }));
      // Give the message back so retrying is one tap. It was cleared
      // optimistically on send.
      if (text) setInput((current) => current || text);
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
    scheduleFlush,
    streaming,
    updateMessageById,
  ]);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    setStreaming(false);
  }, []);

  /** After a failed first page: try again without leaving the screen. */
  const reloadHistory = useCallback(() => {
    void loadHistory(null);
  }, [loadHistory]);

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
    historyError,
    initialLoaded,
    input,
    loadingHistory,
    messages,
    reversedMessages,
    streaming,
    workbenchText,
    refreshSettings,
    refreshWorkbench,
    reloadHistory,
    setInput,
    pickImages,
    removeAttachment,
    sendMessage,
    stopStreaming,
    loadMore,
  };
}
