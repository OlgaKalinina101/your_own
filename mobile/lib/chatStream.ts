/**
 * The chat stream as a lifecycle, not just a sequence of frames.
 *
 * `chatSse.ts` answers "what is this frame". This answers the question one level
 * up, and it is the one a phone actually needs: **did the reply finish, or did
 * the connection stop?**
 *
 * Both clients used to be unable to tell. The read loop ended on
 * `reader.read()` returning `done`, and that happens for two entirely different
 * reasons — the server said `data: [DONE]`, or the socket went away in a tunnel.
 * The first is an answer; the second is half an answer that then gets stored,
 * shown and remembered as if it were whole.
 *
 * The distinction is free, because the backend already makes it: `api/chat.py`
 * terminates with `_SSE_DONE` on both of its own paths — after a successful save
 * (line 1330) and after an exception (line 1355). So "the terminator arrived" is
 * an observable fact, and the four outcomes below are all the states there are.
 *
 * Pinned by the `streams` section of `contracts/chat-sse.json`, which the
 * desktop reads too.
 */

import { parseChatSseEvent, splitSseBuffer, type ChatSseEvent } from "@/lib/chatSse";

export type StreamOutcome =
  /** `data: [DONE]` arrived. The reply is whole. */
  | "done"
  /** The stream ended without it. Whatever was read is a fragment. */
  | "truncated"
  /** The caller aborted — a person pressing stop, or leaving the screen. */
  | "aborted"
  /** The read threw: connection lost, server gone. */
  | "failed";

/**
 * Everything except the terminator.
 *
 * `done` never reaches the handler — it is this module's answer, not an event
 * to render — and saying so in the type means the caller does not have to write
 * a branch for a case that cannot happen.
 */
export type ChatStreamEvent = Exclude<ChatSseEvent, { type: "done" }>;

export interface StreamResult {
  outcome: StreamOutcome;
  /** Present only for `failed`, for whoever turns it into a sentence. */
  error?: unknown;
}

/**
 * The part of a `ReadableStreamDefaultReader` this needs.
 *
 * Narrow on purpose: the whole point of this module is that it can be driven by
 * an array of byte chunks in a test, with no network and no React Native.
 */
export interface ChunkReader {
  read(): Promise<{ done: boolean; value?: Uint8Array }>;
}

/**
 * Read to the end, handing every event to `onEvent`, and say how it ended.
 *
 * Chunks are bytes, never strings: `expo/fetch` and the desktop's `fetch` both
 * yield `Uint8Array`, and the decoder is fed with `{stream: true}` so a
 * character split across two chunks survives. Cyrillic makes that a two-byte
 * question rather than a theoretical one, and `contracts/chat-sse.json` has the
 * fixture.
 *
 * Anything left in the buffer when the stream ends is an incomplete frame and is
 * dropped: a frame without its blank line never happened.
 */
export async function consumeChatStream(
  reader: ChunkReader,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<StreamResult> {
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value === undefined) continue;

      buffer += decoder.decode(value, { stream: true });

      const { events, remainder } = splitSseBuffer(buffer);
      buffer = remainder;

      for (const raw of events) {
        const event = parseChatSseEvent(raw);
        if (!event) continue;
        // The terminator is the answer to this function's question, so it is
        // returned rather than handed on. Nothing behind it is read: the server
        // has stopped talking about this reply.
        if (event.type === "done") return { outcome: "done" };
        onEvent(event);
      }
    }
  } catch (error) {
    // An abort is not a failure — it is the only outcome someone chose.
    if (error instanceof Error && error.name === "AbortError") {
      return { outcome: "aborted" };
    }
    return { outcome: "failed", error };
  }

  return { outcome: "truncated" };
}
