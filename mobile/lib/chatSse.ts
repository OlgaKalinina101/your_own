/**
 * The chat SSE stream, turned into events. Pure functions, no React Native.
 *
 * Kept identical to `frontend/lib/chatSse.ts` — same union, same order, same
 * branches — and both are pinned by the same fixtures in
 * `contracts/chat-sse.json`. They were two hand-written dispatchers once, and
 * they had drifted in four places; the fixtures exist so that the next drift
 * fails a test instead of reaching someone's screen.
 *
 * The rule that makes this safe to extend: **text chunks carry no `event:`
 * line** (`api/chat.py:_yield_chunk`), every named event has one. So a name
 * this client does not recognise is a new event, never text — it is skipped,
 * not rendered.
 */

import type { ChromaFact } from "@/lib/types";

export type ChatSseEvent =
  | { type: "done" }
  | { type: "pair_id"; pairId: string }
  | { type: "text"; chunk: string }
  | { type: "rewrite"; text: string }
  | { type: "memory"; chromaFacts: ChromaFact[] }
  | { type: "image_start"; prompt: string }
  | { type: "image_ready"; path: string; model: string; prompt: string }
  | { type: "image_cancel" }
  | { type: "error"; message: string }
  | { type: "skip"; event: string };

/**
 * Events the backend sends that no client renders yet.
 *
 * Listed rather than lumped in with the unknown ones so the distinction stays
 * visible: these are known gaps, not events from a newer server. (`skill` is in
 * here for history — nothing has ever emitted it.)
 */
const KNOWN_UNCONSUMED = new Set([
  "skill",
  "search_start",
  "search_results",
  "web_start",
  "web_results",
  "web_done",
  "image_urls",
]);

/** Split a read buffer into complete frames, keeping the incomplete tail. */
export function splitSseBuffer(buffer: string): { events: string[]; remainder: string } {
  const chunks = buffer.split("\n\n");
  return {
    events: chunks.slice(0, -1),
    remainder: chunks[chunks.length - 1] ?? "",
  };
}

function parseJson(payload: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(payload);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** One frame to one event, or `null` when the frame carries nothing to act on. */
export function parseChatSseEvent(rawEvent: string): ChatSseEvent | null {
  const lines = rawEvent.split("\n");

  const eventType = lines
    .find((line) => line.startsWith("event: "))
    ?.slice(7)
    .trim();

  const dataLines = lines
    .filter((line) => line.startsWith("data: "))
    .map((line) => line.slice(6));

  // A `: keepalive` comment, or a frame whose data never arrived. Not an event.
  if (dataLines.length === 0) return null;

  const payload = dataLines.join("\n");

  // No name — this is reply text. `[DONE]` is the one reserved value.
  if (!eventType) {
    return payload === "[DONE]" ? { type: "done" } : { type: "text", chunk: payload };
  }

  if (KNOWN_UNCONSUMED.has(eventType)) return { type: "skip", event: eventType };
  if (eventType === "image_cancel") return { type: "image_cancel" };

  const data = parseJson(payload);
  // Named, but the body did not parse. Showing it would put JSON in the bubble.
  if (!data) return { type: "skip", event: eventType };

  switch (eventType) {
    case "pair_id": {
      const pairId = asString(data.pair_id);
      // Used to fall through with no return when the id was missing, which
      // landed the frame in the reply as text.
      return pairId ? { type: "pair_id", pairId } : { type: "skip", event: eventType };
    }
    case "rewrite":
      return { type: "rewrite", text: asString(data.text) };
    case "memory":
      return {
        type: "memory",
        chromaFacts: Array.isArray(data.chroma_facts) ? (data.chroma_facts as ChromaFact[]) : [],
      };
    case "error":
      return { type: "error", message: asString(data.message) };
    case "image_start":
      return { type: "image_start", prompt: asString(data.prompt) };
    case "image_ready":
      return {
        type: "image_ready",
        path: asString(data.path),
        model: asString(data.model),
        prompt: asString(data.prompt),
      };
    default:
      // An event from a server newer than this client. Skipped on purpose.
      return { type: "skip", event: eventType };
  }
}
