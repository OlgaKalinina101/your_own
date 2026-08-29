/**
 * The parser against the shared contract.
 *
 * Cases come from `contracts/chat-sse.json`, which the phone's test reads too.
 * A frame added there fails both clients until both handle it — which is the
 * only mechanism these two codebases have for staying in step, since they share
 * no code and drift between them produces no merge conflict.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { parseChatSseEvent, splitSseBuffer } from "./chatSse";

type Fixture = { name: string; raw: string; expect: unknown };
type BufferFixture = { name: string; buffer: string; events: string[]; remainder: string };

const contract = JSON.parse(
  readFileSync(join(__dirname, "..", "..", "contracts", "chat-sse.json"), "utf-8"),
) as { frames: Fixture[]; buffering: BufferFixture[] };

describe("parseChatSseEvent — the shared contract", () => {
  for (const fixture of contract.frames) {
    it(fixture.name, () => {
      expect(parseChatSseEvent(fixture.raw)).toEqual(fixture.expect);
    });
  }

  it("covers every event the backend can send", () => {
    // Guards the fixtures themselves: a case removed here stops being checked
    // silently, and that is how the error branch went missing on the phone.
    const covered = new Set(
      contract.frames
        .map((f) => /^event: (.+)$/m.exec(f.raw)?.[1])
        .filter((name): name is string => Boolean(name)),
    );
    for (const name of [
      "pair_id",
      "rewrite",
      "memory",
      "error",
      "image_start",
      "image_ready",
      "image_cancel",
      "image_urls",
      "skill",
    ]) {
      expect(covered, `no fixture for event: ${name}`).toContain(name);
    }
  });
});

describe("splitSseBuffer — the shared contract", () => {
  for (const fixture of contract.buffering) {
    it(fixture.name, () => {
      const { events, remainder } = splitSseBuffer(fixture.buffer);
      expect(events).toEqual(fixture.events);
      expect(remainder).toBe(fixture.remainder);
    });
  }
});

describe("a stream read in arbitrary pieces", () => {
  // The reader hands over network-sized reads, not frames. Every boundary
  // between them is a place a frame can be cut in half.
  function drain(pieces: string[]) {
    const out: unknown[] = [];
    let buffer = "";
    for (const piece of pieces) {
      buffer += piece;
      const { events, remainder } = splitSseBuffer(buffer);
      buffer = remainder;
      for (const raw of events) {
        const parsed = parseChatSseEvent(raw);
        if (parsed) out.push(parsed);
      }
    }
    return out;
  }

  const whole =
    "event: pair_id\ndata: {\"pair_id\": \"p1\"}\n\n" +
    "data: Hel\n\n" +
    ": keepalive\n\n" +
    "data: lo\n\n" +
    "event: error\ndata: {\"message\": \"boom\"}\n\n" +
    "data: [DONE]\n\n";

  const expected = [
    { type: "pair_id", pairId: "p1" },
    { type: "text", chunk: "Hel" },
    { type: "text", chunk: "lo" },
    { type: "error", message: "boom" },
    { type: "done" },
  ];

  it("reads the same whole", () => {
    expect(drain([whole])).toEqual(expected);
  });

  it("reads the same one character at a time", () => {
    expect(drain(whole.split(""))).toEqual(expected);
  });

  it("reads the same when a frame is split across reads", () => {
    const cut = 40;
    expect(drain([whole.slice(0, cut), whole.slice(cut)])).toEqual(expected);
  });

  it("never turns a server error into reply text", () => {
    // The divergence this file exists to prevent: on the phone `event: error`
    // fell through to `text` and printed {"message": …} into the bubble.
    const events = drain([whole]);
    const asText = events.filter(
      (e): e is { type: "text"; chunk: string } =>
        (e as { type: string }).type === "text",
    );
    for (const event of asText) {
      expect(event.chunk).not.toContain("message");
    }
  });
});
