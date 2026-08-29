"use client";

/**
 * Everything the chat screen does that is not layout.
 *
 * Counterpart to `mobile/lib/useChatController.ts`, which this is shaped after,
 * with two differences that are real rather than drift:
 *
 *  - messages live in `ChatSessionContext`, not in the hook, because the desktop
 *    keeps a conversation alive while you walk to /dashboard and back;
 *  - the DOM stays outside. Scrolling a message pane, focusing a composer and
 *    resetting a file input are the page's business, so they arrive as the
 *    callbacks in `ChatControllerHooks` instead of refs the hook reaches into.
 *
 * The parsing this used to do inline now lives in `lib/chatSse.ts`, and the
 * attachment arithmetic in `lib/chatAttachments.ts`; both are tested. What is
 * left here is orchestration, which is the part that needs a running app to
 * judge — so it is kept thin and boring on purpose.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch, apiGet } from "@/lib/api";
import { apiErrorFrom, describeApiError } from "@/lib/apiError";
import { fitWithinCap, readPreview, removeAt } from "@/lib/chatAttachments";
import { consumeChatStream } from "@/lib/chatStream";
import { latestCursor, mergeMessages } from "@/lib/mergeMessages";
import { subscribeToChanges } from "@/lib/changeFeed";
import { useChatSession } from "@/context/ChatSessionContext";
import type { HistoryPair, HistoryResponse, Message, Settings } from "@/lib/types";

const HISTORY_BATCH_SIZE = 25;

/** The placeholder a shimmer renders from, removed once the image lands. */
const GENERATING_IMAGE_RE = /\[GENERATE_IMAGE:[^\]]*\]/g;

export const DEFAULT_MODEL = "~anthropic/claude-fable-latest";

/**
 * Models that accept image attachments.
 *
 * Must match VISION_MODELS in infrastructure/llm/client.py: a model missing
 * here has its attach button disabled, one missing there has the photograph
 * dropped on the way out. Both are silent, which is why tests/test_models.py
 * reads this list out of the source.
 */
export const VISION_MODELS = new Set([
  "~anthropic/claude-fable-latest",
  "~moonshotai/kimi-latest",
  "~google/gemini-pro-latest",
  "openai/gpt-chat-latest",
]);

export interface ChatControllerHooks {
  /** After each streamed flush, so the page can follow the text if it wants. */
  onStreamFlush?: () => void;
  /** Before older messages are prepended — snapshot the scroll position here. */
  onBeforePrepend?: () => void;
  /** After a history page lands. `prepended` distinguishes the two cases. */
  onHistoryLoaded?: (prepended: boolean) => void;
  /** After a send settles, however it settled. */
  onSendSettled?: () => void;
}

function makeMessageId(prefix: string): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function pairToMessages(pair: HistoryPair): Message[] {
  const createdAt = pair.pair_created_at ?? pair.created_at ?? undefined;
  const output: Message[] = [];
  // The backend sends user_image_urls and the phone reads it; the desktop did
  // not, so a message made only of images vanished on reload and images on a
  // text message were dropped. Both conditions matter: the guard was on
  // user_text alone.
  const userImageUrls = pair.user_image_urls?.length ? pair.user_image_urls : undefined;
  if (pair.user_text || userImageUrls) {
    output.push({
      id: `${pair.pair_id}-user`,
      role: "user",
      content: pair.user_text,
      imageUrl: userImageUrls?.[0],
      imageUrls: userImageUrls,
      pairId: pair.pair_id,
      createdAt,
    });
  }
  if (pair.assistant_text) {
    output.push({
      id: `${pair.pair_id}-assistant`,
      role: "assistant",
      content: pair.assistant_text,
      pairId: pair.pair_id,
      createdAt,
    });
  }
  return output;
}

export function useChatController(hooks: ChatControllerHooks = {}) {
  const { messages, setMessages } = useChatSession();

  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [webSearch, setWebSearch] = useState(false);
  const [images, setImages] = useState<File[]>([]);
  const [imagePreviews, setImagePreviews] = useState<string[]>([]);
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [canAttach, setCanAttach] = useState(true);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [hasMoreHistory, setHasMoreHistory] = useState(true);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyReady, setHistoryReady] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  // Set from the first frame of the stream; the only handle for deleting a
  // pair the backend already half-saved when the stream fails.
  const activePairIdRef = useRef<string | null>(null);
  const chunkBufRef = useRef("");
  const rafRef = useRef<number | null>(null);

  // Hooks are inline closures at the call site, so they change every render;
  // holding them in a ref keeps callbacks below from re-creating endlessly.
  const hooksRef = useRef(hooks);
  hooksRef.current = hooks;

  // Guards the re-entrancy that `loadingHistory` state cannot: two scroll
  // events in one frame both read the old value and both fire a request.
  const loadingRef = useRef(false);

  const loadHistory = useCallback(
    async (before?: string | null, prepend = false) => {
      if (loadingRef.current) return;
      if (prepend && (!hasMoreHistory || !before)) return;

      loadingRef.current = true;
      if (prepend) hooksRef.current.onBeforePrepend?.();
      setLoadingHistory(true);
      try {
        const params = new URLSearchParams({
          account_id: "default",
          limit_pairs: String(HISTORY_BATCH_SIZE),
        });
        if (before) params.set("before", before);

        const payload = await apiGet<HistoryResponse>(
          `/api/chat/history?${params.toString()}`,
        );
        const loaded = payload.pairs.flatMap(pairToMessages);

        if (prepend) {
          setMessages((prev) => [...loaded, ...prev]);
        } else {
          // Was `prev.length === 0 ? loaded : prev` — the server's answer was
          // discarded whenever anything was cached, and the cache lives at the
          // app root for the life of the process. That is why nothing in the
          // interface could refresh the chat.
          setMessages((prev) => mergeMessages(prev, loaded));
        }

        setHistoryCursor(payload.next_before ?? null);
        setHasMoreHistory(Boolean(payload.has_more));
      } catch {
        // Leaving history empty is survivable; the composer still works and the
        // next scroll retries. Unlike the dashboard pages, there is nowhere
        // useful to put an error here that is not in the way of the chat.
      } finally {
        setHistoryReady(true);
        setLoadingHistory(false);
        loadingRef.current = false;
        hooksRef.current.onHistoryLoaded?.(prepend);
      }
    },
    [hasMoreHistory, setMessages],
  );

  const loadOlder = useCallback(() => {
    if (!hasMoreHistory || loadingRef.current || !historyReady) return;
    void loadHistory(historyCursor, true);
  }, [hasMoreHistory, historyCursor, historyReady, loadHistory]);

  // Model and the first page of history, once.
  useEffect(() => {
    apiGet<Settings>("/api/settings/raw")
      .then((data) => data.model || DEFAULT_MODEL)
      .catch(() => DEFAULT_MODEL)
      .then((chosen) => {
        setModel(chosen);
        setCanAttach(VISION_MODELS.has(chosen));
      });
    void loadHistory(null, false);
    // Deliberately once: loadHistory changes with hasMoreHistory, and this must
    // not re-run when it does.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
        limit_pairs: String(HISTORY_BATCH_SIZE),
        after: cursor,
      });
      const payload = await apiGet<HistoryResponse>(
        `/api/chat/history?${params.toString()}`,
      );
      if (payload.pairs.length === 0) return;
      setMessages((prev) => mergeMessages(prev, payload.pairs.flatMap(pairToMessages)));
    } catch {
      // Offline, or the token is stale. The next trigger tries again; a failed
      // background sync is not something to interrupt a conversation with.
    } finally {
      syncingRef.current = false;
    }
  }, [setMessages]);

  // Three triggers, one path. Coming back to the window covers switching
  // devices; the feed covers a message arriving while the window is open; and
  // both fall back on the other when one of them misses.
  useEffect(() => {
    const onFocus = () => {
      if (document.visibilityState === "visible") void syncNew();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    const feed = subscribeToChanges(() => void syncNew());
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
      feed.close();
    };
  }, [syncNew]);

  // ── Attachments ───────────────────────────────────────────────────────────

  const addImages = useCallback(
    async (selected: File[]) => {
      if (selected.length === 0) return;
      const { accepted } = fitWithinCap(images.length, selected);
      if (accepted.length === 0) return;
      try {
        const previews = await Promise.all(accepted.map(readPreview));
        setImages((prev) => [...prev, ...accepted]);
        setImagePreviews((prev) => [...prev, ...previews]);
      } catch {
        // Ignore preview generation failures for now.
      }
    },
    [images.length],
  );

  const removeImageAt = useCallback((index: number) => {
    setImages((prev) => removeAt(prev, index));
    setImagePreviews((prev) => removeAt(prev, index));
  }, []);

  const clearImages = useCallback(() => {
    setImages([]);
    setImagePreviews([]);
  }, []);

  // ── Sending ───────────────────────────────────────────────────────────────

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text && images.length === 0) return;
    if (streaming) return;

    const userMsg: Message = {
      id: makeMessageId("user"),
      role: "user",
      content: text,
      imageUrl: imagePreviews[0] ?? undefined,
      imageUrls: imagePreviews.length > 0 ? imagePreviews : undefined,
    };
    const nextMessages = [...messages, userMsg];
    const sentImages = images;

    setMessages([...nextMessages, { id: makeMessageId("assistant"), role: "assistant", content: "" }]);
    setInput("");
    clearImages();
    setStreaming(true);

    const replaceLast = (update: (last: Message) => Message) => {
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        updated[updated.length - 1] = { ...update(last), role: "assistant" };
        return updated;
      });
    };

    const flushChunkBuf = () => {
      rafRef.current = null;
      const buffered = chunkBufRef.current;
      if (!buffered) return;
      chunkBufRef.current = "";
      replaceLast((last) => ({ ...last, content: last.content + buffered }));
      hooksRef.current.onStreamFlush?.();
    };

    // Coalesces many tiny chunks into one render frame.
    const scheduleFlush = () => {
      if (rafRef.current === null) rafRef.current = requestAnimationFrame(flushChunkBuf);
    };

    // Flush pending text before a structural change, so order is preserved.
    const flushNow = () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      flushChunkBuf();
    };

    try {
      const body = new FormData();
      body.append(
        "messages",
        JSON.stringify(nextMessages.map((m) => ({ role: m.role, content: m.content }))),
      );
      body.append("web_search", String(webSearch));
      body.append("account_id", "default");
      for (const image of sentImages) body.append("images", image);

      abortRef.current = new AbortController();
      const response = await apiFetch("/api/chat", {
        method: "POST",
        body,
        signal: abortRef.current.signal,
      });

      // The server often knows exactly what is wrong — a 503 carries a hint
      // saying the backend is up and Postgres is not, which is the opposite of
      // what the generic message asks.
      if (!response.ok) throw await apiErrorFrom(response);
      if (!response.body) throw new Error("The server sent no body");

      // The stream's lifecycle lives in lib/chatStream.ts. Its outcome is the
      // point of this whole call: `done` means the terminator arrived and the
      // reply is whole; anything else means it is not — a distinction the loop
      // that used to be here could not make, so half a reply was stored, shown
      // and remembered as if it were the answer.
      const result = await consumeChatStream(response.body.getReader(), (event) => {
        switch (event.type) {
          case "text":
            chunkBufRef.current += event.chunk;
            scheduleFlush();
            break;

          case "pair_id":
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
            break;

          case "memory":
            flushNow();
            replaceLast((last) => ({ ...last, chromaFacts: event.chromaFacts }));
            break;

          case "rewrite":
            flushNow();
            replaceLast((last) => ({ ...last, content: event.text }));
            break;

          case "error":
            flushNow();
            replaceLast((last) => ({
              ...last,
              content:
                last.content.trimEnd() +
                (event.message
                  ? `\n\n[ответ оборван: ${event.message}]`
                  : "\n\n[ответ оборван]"),
            }));
            break;

          case "image_start":
            flushNow();
            replaceLast((last) => ({
              ...last,
              content: `${last.content.trimEnd()}\n[GENERATE_IMAGE: ${event.prompt}]`,
            }));
            break;

          case "image_cancel":
            flushNow();
            replaceLast((last) => ({
              ...last,
              content: last.content.replace(GENERATING_IMAGE_RE, "").trimEnd(),
            }));
            break;

          case "image_ready":
            flushNow();
            replaceLast((last) => {
              if (last.content.includes(`[GENERATED_IMAGE: ${event.path}`)) return last;
              const cleaned = last.content.replace(GENERATING_IMAGE_RE, "").trimEnd();
              const marker = `[GENERATED_IMAGE: ${event.path} | ${event.model} | ${event.prompt}]`;
              return { ...last, content: `${cleaned}\n${marker}` };
            });
            break;

          case "skip":
            // A frame no screen renders yet, or one from a newer server.
            // Never shown: putting it in the bubble is what used to happen.
            break;
        }
      });

      flushNow();

      if (result.outcome !== "done") {
        // The reply stopped short. What is on screen is what the backend also
        // kept: `_save_partial` (api/chat.py) stores the streamed text when the
        // client hangs up, and the user's own row was written before the stream
        // even opened. So the honest move is to keep it and say why it is short.
        //
        // This used to send DELETE /api/chat/pair here — a race against that
        // very save, which could land after the delete and leave an orphaned
        // half-reply that the change feed then put back on screen a moment
        // later. Not deleting removes the race rather than fixing it.
        replaceLast((last) => ({
          ...last,
          interrupted: result.outcome === "aborted" ? "stopped" : "connection",
        }));
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      // Only reachable before the stream opened: a refused connection, a bad
      // status, a body that never came. Nothing streamed, so the bubble carries
      // the reason instead of a reply.
      replaceLast((last) => ({ ...last, content: `[${describeApiError(err)}]` }));
      // Give the message back. It was cleared optimistically on send, and
      // losing what someone typed because the network blinked is not a
      // trade-off — it is just a loss.
      if (text) setInput((current) => current || text);
    } finally {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      chunkBufRef.current = "";
      activePairIdRef.current = null;
      setStreaming(false);
      abortRef.current = null;
      hooksRef.current.onSendSettled?.();
    }
  }, [clearImages, imagePreviews, images, input, messages, setMessages, streaming, webSearch]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setStreaming(false);
  }, []);

  return {
    // state
    messages,
    input,
    streaming,
    webSearch,
    images,
    imagePreviews,
    model,
    canAttach,
    hasMoreHistory,
    loadingHistory,
    historyReady,
    // actions
    setInput,
    setWebSearch,
    addImages,
    removeImageAt,
    clearImages,
    loadOlder,
    send,
    stop,
  };
}

export type ChatController = ReturnType<typeof useChatController>;
