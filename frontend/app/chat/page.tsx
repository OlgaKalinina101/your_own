"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useChatSession, Message, ChromaFact } from "@/context/ChatSessionContext";
import MarkdownMessage from "@/components/chat/MarkdownMessage";

import { apiGet, apiFetch } from "@/lib/api";
const HISTORY_BATCH_SIZE = 25;
const MAX_CHAT_IMAGES = 8;

// Vision-capable models (can accept image attachments)
const VISION_MODELS = new Set([
  "anthropic/claude-opus-4.6",
  "openai/gpt-5.1",
  "openai/gpt-5.4",
]);


function makeMessageId(prefix: string) {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

interface HistoryPair {
  pair_id: string;
  created_at?: string | null;
  pair_created_at?: string | null;
  user_text: string;
  assistant_text: string;
}

interface HistoryResponse {
  pairs: HistoryPair[];
  next_before?: string | null;
  has_more: boolean;
}

function pairToMessages(pair: HistoryPair): Message[] {
  const createdAt = pair.pair_created_at ?? pair.created_at ?? undefined;
  const output: Message[] = [];
  if (pair.user_text) {
    output.push({
      id: `${pair.pair_id}-user`,
      role: "user",
      content: pair.user_text,
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

async function getServerModel(): Promise<string> {
  try {
    const data = await apiGet<Record<string, unknown>>("/api/settings/raw");
    return (data.model as string) || "anthropic/claude-opus-4.6";
  } catch {
    return "anthropic/claude-opus-4.6";
  }
}

export default function ChatPage() {
  const router = useRouter();

  const { messages, setMessages }   = useChatSession();
  const [input, setInput]           = useState("");
  const [streaming, setStreaming]   = useState(false);
  const [webSearch, setWebSearch]   = useState(false);
  const [images, setImages]         = useState<File[]>([]);
  const [imagePreviews, setImagePreviews] = useState<string[]>([]);
  const [model, setModel]           = useState("anthropic/claude-opus-4.6");
  const [canAttach, setCanAttach]   = useState(true);
  const [expandedMemories, setExpandedMemories] = useState<Record<string, boolean>>({});
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [hasMoreHistory, setHasMoreHistory] = useState(true);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyReady, setHistoryReady] = useState(false);
  const [showScrollDown, setShowScrollDown] = useState(false);

  const bottomRef    = useRef<HTMLDivElement>(null);
  const inputRef     = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef     = useRef<AbortController | null>(null);
  const messagePaneRef = useRef<HTMLDivElement>(null);
  const isPrependingRef = useRef(false);

  // SSE text chunk buffer — accumulated between rAF flushes
  const chunkBufRef = useRef("");
  const rafRef      = useRef<number | null>(null);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    bottomRef.current?.scrollIntoView({ behavior });
  }, []);

  // Only auto-scroll during streaming if user is already near the bottom
  const scrollIfNearBottom = useCallback(() => {
    const el = messagePaneRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom < 120) {
      bottomRef.current?.scrollIntoView({ behavior: "auto" });
    }
  }, []);

  const loadHistory = async (before?: string | null, prepend = false) => {
    if (loadingHistory) return;
    if (prepend && (!hasMoreHistory || !before)) return;

    const container = messagePaneRef.current;
    const previousHeight = container?.scrollHeight ?? 0;
    const previousTop = container?.scrollTop ?? 0;

    setLoadingHistory(true);
    try {
      const params = new URLSearchParams({
        account_id: "default",
        limit_pairs: String(HISTORY_BATCH_SIZE),
      });
      if (before) params.set("before", before);

      const response = await apiFetch(`/api/chat/history?${params.toString()}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const payload = await response.json() as HistoryResponse;
      const loadedMessages = payload.pairs.flatMap(pairToMessages);

      if (prepend) {
        isPrependingRef.current = true;
        setMessages((prev) => [...loadedMessages, ...prev]);
      } else {
        setMessages((prev) => (prev.length === 0 ? loadedMessages : prev));
      }

      setHistoryCursor(payload.next_before ?? null);
      setHasMoreHistory(Boolean(payload.has_more));
      setHistoryReady(true);

      requestAnimationFrame(() => {
        const nextContainer = messagePaneRef.current;
        if (!nextContainer) return;
        if (prepend) {
          const delta = nextContainer.scrollHeight - previousHeight;
          nextContainer.scrollTop = previousTop + delta;
        } else {
          scrollToBottom("auto");
        }
      });
    } catch {
      setHistoryReady(true);
    } finally {
      setLoadingHistory(false);
    }
  };

  // Load model and persisted chat history on mount
  useEffect(() => {
    getServerModel().then((m) => {
      setModel(m);
      setCanAttach(VISION_MODELS.has(m));
    });
    void loadHistory(null, false);
  }, []);

  useEffect(() => {
    if (isPrependingRef.current) {
      isPrependingRef.current = false;
      return;
    }
    // During active streaming, scrolling is handled per-chunk in scrollIfNearBottom.
    // Only auto-scroll for non-streaming state changes (new user message, history load).
    if (!streaming && !showScrollDown) {
      scrollToBottom(historyReady ? "smooth" : "auto");
    }
  }, [messages, showScrollDown, historyReady, streaming, scrollToBottom]);

  const readPreview = (file: File) => new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("Failed to read image"));
    reader.readAsDataURL(file);
  });

  const addImages = async (selected: File[]) => {
    if (selected.length === 0) return;
    const remainingSlots = Math.max(0, MAX_CHAT_IMAGES - images.length);
    const filesToAdd = selected.slice(0, remainingSlots);
    if (filesToAdd.length === 0) {
      return;
    }

    try {
      const previews = await Promise.all(filesToAdd.map(readPreview));
      setImages((prev) => [...prev, ...filesToAdd]);
      setImagePreviews((prev) => [...prev, ...previews]);
    } catch {
      // Ignore preview generation failures for now.
    }
  };

  const handleImageSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(e.target.files ?? []);
    try {
      await addImages(selected);
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handlePaste = async (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    if (!canAttach) {
      return;
    }

    const imageFiles = Array.from(e.clipboardData.items)
      .filter((item) => item.type.startsWith("image/"))
      .map((item) => item.getAsFile())
      .filter((file): file is File => Boolean(file));

    if (imageFiles.length === 0) {
      return;
    }

    e.preventDefault();
    await addImages(imageFiles);
  };

  const removeImageAt = (index: number) => {
    setImages((prev) => prev.filter((_, currentIndex) => currentIndex !== index));
    setImagePreviews((prev) => prev.filter((_, currentIndex) => currentIndex !== index));
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const clearImages = () => {
    setImages([]);
    setImagePreviews([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text && images.length === 0) return;
    if (streaming) return;

    // Settings are now server-side — no need to send them per request

    // Optimistically add user message
    const userMsg: Message = {
      id: makeMessageId("user"),
      role: "user",
      content: text,
      imageUrl: imagePreviews[0] ?? undefined,
      imageUrls: imagePreviews.length > 0 ? imagePreviews : undefined,
    };
    const nextMessages = [...messages, userMsg];
    setMessages(nextMessages);
    setInput("");
    clearImages();
    setStreaming(true);

    // Placeholder for assistant response
    setMessages((prev) => [...prev, {
      id: makeMessageId("assistant"),
      role: "assistant",
      content: "",
    }]);

    try {
      const body = new FormData();
      body.append("messages", JSON.stringify(
        nextMessages.map((m) => ({ role: m.role, content: m.content }))
      ));
      body.append("web_search", String(webSearch));
      body.append("account_id", "default");

      for (const image of images) {
        body.append("images", image);
      }

      abortRef.current = new AbortController();

      const response = await apiFetch(`/api/chat`, {
        method: "POST",
        body,
        signal: abortRef.current.signal,
      });

      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

      // Flush buffered text into message state, then trigger a scroll check
      const flushChunkBuf = () => {
        rafRef.current = null;
        const text = chunkBufRef.current;
        if (!text) return;
        chunkBufRef.current = "";
        setMessages((prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          updated[updated.length - 1] = { ...last, role: "assistant", content: last.content + text };
          return updated;
        });
        scrollIfNearBottom();
      };

      // Schedule a rAF flush; coalesces many tiny chunks into one render frame
      const scheduleFlush = () => {
        if (rafRef.current === null) {
          rafRef.current = requestAnimationFrame(flushChunkBuf);
        }
      };

      // Flush any pending buffer before a structural state change (rewrite, image…)
      const flushNow = () => {
        if (rafRef.current !== null) {
          cancelAnimationFrame(rafRef.current);
          rafRef.current = null;
        }
        flushChunkBuf();
      };

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let sseBuffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        sseBuffer += decoder.decode(value, { stream: true });

        // SSE events are separated by double newline "\n\n"
        const events = sseBuffer.split("\n\n");
        // Keep the last incomplete event in the buffer
        sseBuffer = events.pop() ?? "";

        for (const event of events) {
          const eventType = event
            .split("\n")
            .find((l) => l.startsWith("event: "))
            ?.slice(7)
            .trim();

          // Collect all data: lines within the event and join with \n
          const dataLines = event
            .split("\n")
            .filter((l) => l.startsWith("data: "))
            .map((l) => l.slice(6));

          if (dataLines.length === 0) continue;

          const chunk = dataLines.join("\n");
          if (chunk === "[DONE]") break;

          if (eventType === "memory") {
            try {
              const payload = JSON.parse(chunk) as { chroma_facts?: ChromaFact[] };
              flushNow();
              setMessages((prev) => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                updated[updated.length - 1] = {
                  ...last,
                  role: "assistant",
                  chromaFacts: payload.chroma_facts ?? [],
                };
                return updated;
              });
            } catch {
              // ignore malformed metadata event
            }
            continue;
          }

          if (eventType === "rewrite") {
            try {
              const payload = JSON.parse(chunk);
              const newText = payload.text ?? "";
              flushNow();
              setMessages((prev) => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                updated[updated.length - 1] = {
                  ...last,
                  role: "assistant",
                  content: newText,
                };
                return updated;
              });
            } catch {
              // ignore malformed rewrite
            }
            continue;
          }

          if (
            eventType === "skill" ||
            eventType === "search_start" ||
            eventType === "search_results" ||
            eventType === "web_start" ||
            eventType === "web_done"
          ) {
            continue;
          }

          if (eventType === "image_start") {
            try {
              const { prompt: imgPrompt } = JSON.parse(chunk) as { prompt: string };
              const shimmerCmd = `[GENERATE_IMAGE: ${imgPrompt}]`;
              flushNow();
              setMessages((prev) => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                updated[updated.length - 1] = {
                  ...last,
                  role: "assistant",
                  content: last.content.trimEnd() + "\n" + shimmerCmd,
                };
                return updated;
              });
            } catch {
              // ignore
            }
            continue;
          }

          if (eventType === "image_ready") {
            try {
              const { path, model, prompt } = JSON.parse(chunk) as {
                path: string;
                model: string;
                prompt: string;
              };
              const marker = `[GENERATED_IMAGE: ${path} | ${model} | ${prompt}]`;
              flushNow();
              setMessages((prev) => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                if (last.content.includes(`[GENERATED_IMAGE: ${path}`)) {
                  return updated;
                }
                const cleaned = last.content.replace(
                  /\[GENERATE_IMAGE:[^\]]*\]/g,
                  "",
                );
                updated[updated.length - 1] = {
                  ...last,
                  role: "assistant",
                  content: cleaned.trimEnd() + "\n" + marker,
                };
                return updated;
              });
            } catch {
              // ignore malformed image_ready event
            }
            continue;
          }

          // Plain text chunk — accumulate and schedule a single rAF flush
          chunkBufRef.current += chunk;
          scheduleFlush();
        }
      }

      // Flush any remaining buffered text at end of stream
      flushNow();
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        updated[updated.length - 1] = {
          ...last,
          role: "assistant",
          content: "[connection error — is the backend running?]",
        };
        return updated;
      });
    } finally {
      // Cancel any pending rAF flush
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      chunkBufRef.current = "";
      setStreaming(false);
      abortRef.current = null;
      inputRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleStop = () => {
    abortRef.current?.abort();
    setStreaming(false);
  };

  const toggleMemory = (index: string) => {
    setExpandedMemories((prev) => ({
      ...prev,
      [index]: !prev[index],
    }));
  };

  const handleMessagesScroll = () => {
    const container = messagePaneRef.current;
    if (!container) return;

    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    setShowScrollDown(distanceFromBottom > 180);

    if (container.scrollTop < 120 && hasMoreHistory && !loadingHistory && historyReady) {
      void loadHistory(historyCursor, true);
    }
  };

  return (
    <div className="relative flex h-screen w-screen flex-col bg-black">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="flex shrink-0 items-center justify-between border-b border-white/10 px-8 py-4">
        <button
          onClick={() => router.push("/dashboard")}
          className="text-[0.68rem] tracking-[0.2em] uppercase text-white/50 transition-colors duration-300 hover:text-white/90"
        >
          ← dashboard
        </button>

        <div className="flex items-center gap-6">
          {/* Web search toggle */}
          <button
            onClick={() => setWebSearch((v) => !v)}
            title="Web search"
            className={`
              flex items-center gap-2
              text-[0.68rem] tracking-[0.16em] uppercase
              transition-colors duration-300
              ${webSearch ? "text-white" : "text-white/45 hover:text-white/75"}
            `}
          >
            <span
              className={`
                inline-block h-[6px] w-[6px] rounded-full
                transition-colors duration-300
                ${webSearch ? "bg-white/80" : "bg-white/20"}
              `}
            />
            web
          </button>

          {/* Model indicator */}
          <button
            onClick={() => router.push("/dashboard/settings")}
            className="text-[0.68rem] tracking-[0.12em] text-white/45 transition-colors duration-300 hover:text-white/80"
          >
            {model.split("/")[1] ?? model}
          </button>
        </div>
      </header>

      {/* ── Messages ───────────────────────────────────────────────────────── */}
      <div
        ref={messagePaneRef}
        onScroll={handleMessagesScroll}
        className="flex-1 overflow-y-auto px-8 py-8"
      >
        {loadingHistory && (
          <div className="mx-auto mb-6 flex max-w-2xl justify-center">
            <p className="text-[0.62rem] tracking-[0.18em] uppercase text-white/35">
              loading history
            </p>
          </div>
        )}

        {messages.length === 0 && historyReady && (
          <div className="flex h-full items-center justify-center">
            <p className="text-[0.8rem] tracking-[0.12em] uppercase text-white/40">
              start typing
            </p>
          </div>
        )}

        <div className="mx-auto flex max-w-2xl flex-col gap-8">
          {messages.map((msg, i) => (
            <div
              key={msg.id}
              className={`flex flex-col gap-2 ${msg.role === "user" ? "items-end" : "items-start"}`}
            >
              {(msg.imageUrls ?? (msg.imageUrl ? [msg.imageUrl] : [])).length > 0 && (
                <div className="flex max-w-[80%] gap-3 overflow-x-auto pb-1">
                  {(msg.imageUrls ?? (msg.imageUrl ? [msg.imageUrl] : [])).map((imageUrl, imageIndex) => (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      key={`${msg.id}-image-${imageIndex}`}
                      src={imageUrl}
                      alt={`attachment ${imageIndex + 1}`}
                      className="h-32 w-32 shrink-0 rounded-none border border-white/15 object-cover"
                    />
                  ))}
                </div>
              )}
              <MarkdownMessage
                content={msg.content}
                role={msg.role}
                showCursor={msg.role === "assistant" && streaming && i === messages.length - 1}
                isStreaming={msg.role === "assistant" && streaming && i === messages.length - 1}
              />
              {msg.role === "assistant" && (msg.chromaFacts?.length ?? 0) > 0 && (
                <>
                  <button
                    onClick={() => toggleMemory(msg.id)}
                    className="text-[0.65rem] tracking-[0.18em] uppercase text-white/45 transition-colors duration-300 hover:text-white/85"
                  >
                    {"<>"} memory {expandedMemories[msg.id] ? "hide" : "show"}
                  </button>
                  {expandedMemories[msg.id] && (
                    <div className="mt-2 flex w-full max-w-[88%] flex-col gap-3 border border-white/10 bg-white/[0.02] p-4">
                      {msg.chromaFacts?.map((fact, fi) => (
                        <div key={fact.id || fi} className="border border-white/10 p-3">
                          <div className="mb-1 flex items-center justify-between gap-4">
                            <span className="text-[0.62rem] tracking-[0.18em] uppercase text-white/45">
                              {fact.category || "memory"}
                            </span>
                            <div className="flex items-center gap-3 text-[0.62rem] tracking-[0.12em] text-white/35">
                              <span>{fact.time_label}</span>
                              {fact.impressive > 0 && (
                                <span>{"★".repeat(Math.min(fact.impressive, 4))}</span>
                              )}
                            </div>
                          </div>
                          <p className="text-[0.8rem] leading-relaxed text-white/75">
                            {fact.text}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </div>

      {showScrollDown && (
        <button
          onClick={() => scrollToBottom("smooth")}
          className="absolute bottom-28 right-8 border border-white/15 bg-black/70 px-3 py-2 text-[0.8rem] text-white/70 transition-colors duration-300 hover:border-white/35 hover:text-white"
          title="Scroll to latest"
        >
          ↓
        </button>
      )}

      {/* ── Input area ─────────────────────────────────────────────────────── */}
      <div className="shrink-0 border-t border-white/10 px-8 py-5">
        <div className="mx-auto flex max-w-2xl flex-col gap-3">

          {/* Image preview */}
          {imagePreviews.length > 0 && (
            <div className="flex items-center gap-3 overflow-x-auto pb-1">
              {imagePreviews.map((imagePreview, index) => (
                <div key={`preview-${index}`} className="relative shrink-0">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={imagePreview}
                    alt={`preview ${index + 1}`}
                    className="h-16 w-16 border border-white/15 object-cover"
                  />
                  <button
                    onClick={() => removeImageAt(index)}
                    className="absolute right-1 top-1 border border-black/40 bg-black/65 px-1.5 py-0.5 text-[0.52rem] tracking-widest uppercase text-white/70 transition-colors duration-200 hover:text-white"
                  >
                    x
                  </button>
                </div>
              ))}
              <span className="shrink-0 text-[0.62rem] tracking-[0.16em] uppercase text-white/35">
                {imagePreviews.length} / {MAX_CHAT_IMAGES} images
              </span>
            </div>
          )}

          <div className="flex items-end gap-4">
            {/* Attach image button (only for vision models) */}
            {canAttach && (
              <>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  multiple
                  className="hidden"
                  onChange={handleImageSelect}
                />
                <button
                  onClick={() => fileInputRef.current?.click()}
                  title="Attach image"
                  disabled={images.length >= MAX_CHAT_IMAGES}
                  className="mb-1 shrink-0 text-[0.68rem] tracking-widest uppercase text-white/45 transition-colors duration-300 hover:text-white/80"
                >
                  +img
                </button>
              </>
            )}

            {/* Text input */}
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              placeholder="..."
              rows={1}
              className="
                flex-1 resize-none bg-transparent
                border-b border-white/25
                py-2 text-[0.95rem] font-light leading-relaxed tracking-wide text-white
                placeholder:text-white/35
                outline-none
                transition-colors duration-300
                focus:border-white/60
                max-h-40 overflow-y-auto
              "
              style={{ scrollbarWidth: "none" }}
              onInput={(e) => {
                const t = e.currentTarget;
                t.style.height = "auto";
                t.style.height = `${t.scrollHeight}px`;
              }}
            />

            {/* Send / Stop */}
            {streaming ? (
              <button
                onClick={handleStop}
                className="mb-1 shrink-0 text-[0.68rem] tracking-widest uppercase text-white/55 transition-colors duration-300 hover:text-white/90"
              >
                stop
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!input.trim() && images.length === 0}
                className="mb-1 shrink-0 text-[0.68rem] tracking-widest uppercase text-white/55 transition-colors duration-300 hover:text-white/90 disabled:opacity-20 disabled:cursor-default"
              >
                send
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
