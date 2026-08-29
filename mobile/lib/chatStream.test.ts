/**
 * The stream lifecycle against the shared contract.
 *
 * Same file as the desktop's test reads. The `streams` section is byte-level on
 * purpose: the two things it pins — a terminator that never came, and a
 * character cut in half by a chunk boundary — are both invisible once the bytes
 * have already been turned into a string, which is exactly why neither client
 * caught them.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { consumeChatStream, type ChunkReader } from "./chatStream";
import type { ChatSseEvent } from "./chatSse";

interface StreamFixture {
  name: string;
  chunks: string[];
  then: "close" | "throw" | "abort";
  outcome: string;
  events: ChatSseEvent[];
}

const contract = JSON.parse(
  readFileSync(join(__dirname, "..", "..", "contracts", "chat-sse.json"), "utf-8"),
) as { streams: StreamFixture[] };

function bytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i += 1) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

function abortError(): Error {
  const error = new Error("Aborted");
  error.name = "AbortError";
  return error;
}

/** A reader over fixed chunks that ends the way the fixture says. */
function readerOver(chunks: string[], then: StreamFixture["then"]): ChunkReader {
  let index = 0;
  return {
    async read() {
      if (index < chunks.length) {
        return { done: false, value: bytes(chunks[index++]) };
      }
      if (then === "throw") throw new Error("Network request failed");
      if (then === "abort") throw abortError();
      return { done: true };
    },
  };
}

describe("consumeChatStream — the shared contract", () => {
  for (const fixture of contract.streams) {
    it(fixture.name, async () => {
      const seen: ChatSseEvent[] = [];
      const result = await consumeChatStream(
        readerOver(fixture.chunks, fixture.then),
        (event) => seen.push(event),
      );
      expect(result.outcome).toBe(fixture.outcome);
      expect(seen).toEqual(fixture.events);
    });
  }
});

describe("consumeChatStream — the distinction the fixtures exist for", () => {
  const reply = "event: pair_id\ndata: {\"pair_id\": \"p1\"}\n\ndata: Половина ответа\n\n";
  const encode = (text: string) => new TextEncoder().encode(text);

  it("a finished stream and a cut one differ only by the terminator", async () => {
    const cut = await consumeChatStream(
      { read: makeReads([encode(reply)]) },
      () => {},
    );
    const finished = await consumeChatStream(
      { read: makeReads([encode(reply + "data: [DONE]\n\n")]) },
      () => {},
    );
    expect(cut.outcome).toBe("truncated");
    expect(finished.outcome).toBe("done");
  });

  it("carries the error out so the caller can name it", async () => {
    const boom = new Error("Network request failed");
    const result = await consumeChatStream(
      {
        read: async () => {
          throw boom;
        },
      },
      () => {},
    );
    expect(result).toEqual({ outcome: "failed", error: boom });
  });

  it("an incomplete trailing frame is dropped, not guessed at", async () => {
    const seen: ChatSseEvent[] = [];
    const result = await consumeChatStream(
      { read: makeReads([encode("data: whole\n\ndata: half")]) },
      (event) => seen.push(event),
    );
    expect(seen).toEqual([{ type: "text", chunk: "whole" }]);
    expect(result.outcome).toBe("truncated");
  });

  it("survives a stream delivered one byte at a time", async () => {
    const all = encode(reply + "data: [DONE]\n\n");
    const single = Array.from(all, (byte) => Uint8Array.of(byte));
    const seen: ChatSseEvent[] = [];
    const result = await consumeChatStream(
      { read: makeReads(single) },
      (event) => seen.push(event),
    );
    expect(result.outcome).toBe("done");
    expect(seen).toEqual([
      { type: "pair_id", pairId: "p1" },
      { type: "text", chunk: "Половина ответа" },
    ]);
  });

  function makeReads(chunks: Uint8Array[]): ChunkReader["read"] {
    let index = 0;
    return async () =>
      index < chunks.length ? { done: false, value: chunks[index++] } : { done: true };
  }
});
