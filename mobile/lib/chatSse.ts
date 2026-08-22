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
  | { type: "skip" };

const SKIP_EVENTS = new Set([
  "skill",
  "search_start",
  "search_results",
  "web_start",
  "web_results",
  "web_done",
  "image_urls",
]);

export function splitSseBuffer(buffer: string): { events: string[]; remainder: string } {
  const chunks = buffer.split("\n\n");
  return {
    events: chunks.slice(0, -1),
    remainder: chunks[chunks.length - 1] ?? "",
  };
}

export function parseChatSseEvent(rawEvent: string): ChatSseEvent | null {
  const eventType = rawEvent
    .split("\n")
    .find((line) => line.startsWith("event: "))
    ?.slice(7)
    .trim();
  const dataLines = rawEvent
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map((line) => line.slice(6));

  if (!dataLines.length) return null;

  const payload = dataLines.join("\n");
  if (payload === "[DONE]") return { type: "done" };

  if (eventType === "pair_id") {
    try {
      const parsed = JSON.parse(payload) as { pair_id?: string };
      if (parsed.pair_id) return { type: "pair_id", pairId: parsed.pair_id };
    } catch {
      return null;
    }
  }

  if (eventType === "rewrite") {
    try {
      const parsed = JSON.parse(payload) as { text?: string };
      return { type: "rewrite", text: parsed.text ?? "" };
    } catch {
      return null;
    }
  }

  if (eventType === "memory") {
    try {
      const parsed = JSON.parse(payload) as { chroma_facts?: ChromaFact[] };
      return { type: "memory", chromaFacts: parsed.chroma_facts ?? [] };
    } catch {
      return null;
    }
  }

  if (eventType === "image_start") {
    try {
      const parsed = JSON.parse(payload) as { prompt?: string };
      return { type: "image_start", prompt: parsed.prompt ?? "" };
    } catch {
      return { type: "image_start", prompt: "" };
    }
  }

  if (eventType === "image_cancel") {
    return { type: "image_cancel" };
  }

  if (eventType === "image_ready") {
    try {
      const parsed = JSON.parse(payload) as { path?: string; model?: string; prompt?: string };
      return {
        type: "image_ready",
        path: parsed.path ?? "",
        model: parsed.model ?? "",
        prompt: parsed.prompt ?? "",
      };
    } catch {
      return { type: "skip" };
    }
  }

  if (eventType && SKIP_EVENTS.has(eventType)) {
    return { type: "skip" };
  }

  return { type: "text", chunk: payload };
}
