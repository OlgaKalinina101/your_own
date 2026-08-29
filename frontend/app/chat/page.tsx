"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import MarkdownMessage from "@/components/chat/MarkdownMessage";

import { MAX_CHAT_IMAGES, imageFilesFromClipboard } from "@/lib/chatAttachments";
import { mediaUrl, useMediaSignature } from "@/lib/media";
import type { Message } from "@/lib/types";
import { useChatController } from "@/lib/useChatController";

/** How close to the bottom counts as "following along". */
const FOLLOW_THRESHOLD_PX = 120;
/** How far up before the jump-to-bottom button is worth showing. */
const SCROLL_BUTTON_THRESHOLD_PX = 180;
/** How close to the top triggers loading the previous page. */
const LOAD_OLDER_THRESHOLD_PX = 120;

function resolveAttachments(message: Message): string[] {
  const raw = message.imageUrls ?? (message.imageUrl ? [message.imageUrl] : []);
  return raw.map(mediaUrl).filter(Boolean);
}

export default function ChatPage() {
  const router = useRouter();

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const messagePaneRef = useRef<HTMLDivElement>(null);

  const [showScrollDown, setShowScrollDown] = useState(false);
  const [expandedMemories, setExpandedMemories] = useState<Record<string, boolean>>({});

  // Attachments restored from history are authenticated backend paths, and
  // resolveAttachments() cannot sign them until this resolves. Subscribing here
  // re-renders the list once it does.
  useMediaSignature();

  // Prepending older messages moves everything down; without restoring the
  // offset the view jumps to a different part of the conversation.
  const prependAnchorRef = useRef<{ height: number; top: number } | null>(null);
  // Tells the auto-scroll effect below to sit still for one render.
  const isPrependingRef = useRef(false);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    bottomRef.current?.scrollIntoView({ behavior });
  }, []);

  const scrollIfNearBottom = useCallback(() => {
    const el = messagePaneRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom < FOLLOW_THRESHOLD_PX) {
      bottomRef.current?.scrollIntoView({ behavior: "auto" });
    }
  }, []);

  const {
    messages,
    input,
    streaming,
    webSearch,
    images,
    imagePreviews,
    model,
    canAttach,
    loadingHistory,
    historyReady,
    setInput,
    setWebSearch,
    addImages,
    removeImageAt,
    loadOlder,
    send,
    stop,
  } = useChatController({
    onStreamFlush: scrollIfNearBottom,
    onBeforePrepend: () => {
      const el = messagePaneRef.current;
      prependAnchorRef.current = el
        ? { height: el.scrollHeight, top: el.scrollTop }
        : null;
      isPrependingRef.current = true;
    },
    onHistoryLoaded: (prepended) => {
      requestAnimationFrame(() => {
        const el = messagePaneRef.current;
        if (!el) return;
        if (prepended) {
          const anchor = prependAnchorRef.current;
          if (anchor) el.scrollTop = anchor.top + (el.scrollHeight - anchor.height);
          prependAnchorRef.current = null;
        } else {
          scrollToBottom("auto");
        }
      });
    },
    onSendSettled: () => inputRef.current?.focus(),
  });

  useEffect(() => {
    if (isPrependingRef.current) {
      isPrependingRef.current = false;
      return;
    }
    // While streaming, following the text is handled per-chunk by
    // scrollIfNearBottom; this is for the structural changes.
    if (!streaming && !showScrollDown) {
      scrollToBottom(historyReady ? "smooth" : "auto");
    }
  }, [messages, showScrollDown, historyReady, streaming, scrollToBottom]);

  // ── DOM events, kept here because the controller does not touch the DOM ───

  const handleImageSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(e.target.files ?? []);
    try {
      await addImages(selected);
    } finally {
      // So picking the same file twice in a row still fires a change event.
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handlePaste = async (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    if (!canAttach) return;
    const imageFiles = imageFilesFromClipboard(e.clipboardData.items);
    if (imageFiles.length === 0) return;
    e.preventDefault();
    await addImages(imageFiles);
  };

  const handleSend = () => {
    void send();
  };

  const handleStop = () => {
    stop();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const toggleMemory = (index: string) => {
    setExpandedMemories((prev) => ({ ...prev, [index]: !prev[index] }));
  };

  const handleMessagesScroll = () => {
    const container = messagePaneRef.current;
    if (!container) return;

    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight;
    setShowScrollDown(distanceFromBottom > SCROLL_BUTTON_THRESHOLD_PX);

    if (container.scrollTop < LOAD_OLDER_THRESHOLD_PX) loadOlder();
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
              {/* Live previews are data: URLs and pass through untouched; images
                  restored from history are backend paths and need a signature,
                  so an entry can be empty for a beat and is skipped. */}
              {resolveAttachments(msg).length > 0 && (
                <div className="flex max-w-[80%] gap-3 overflow-x-auto pb-1">
                  {resolveAttachments(msg).map((imageUrl, imageIndex) => (
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
