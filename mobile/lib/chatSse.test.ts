/**
 * The parser against the shared contract.
 *
 * The same `contracts/chat-sse.json` the desktop's test reads. That is the
 * whole mechanism: these two codebases share no code, they drift without ever
 * producing a merge conflict, and by the time this file was written they had
 * drifted four times — an `error` frame printed as reply text here, images
 * dropped from history there. A case added to the fixtures now fails both
 * clients until both handle it.
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

  it("never turns a server error into reply text", () => {
    // This is the bug this file was written for: `event: error` had no branch
    // here and fell through to `text`, so a failed stream printed
    // {"message": …} into the reply bubble.
    const asText = drain([whole]).filter(
      (e): e is { type: "text"; chunk: string } => (e as { type: string }).type === "text",
    );
    for (const event of asText) expect(event.chunk).not.toContain("message");
  });

  it("never turns an id-less pair_id frame into reply text", () => {
    // It used to fall through with no return at all when pair_id was missing.
    const events = drain(['event: pair_id\ndata: {"pair_id": ""}\n\n']);
    expect(events).toEqual([{ type: "skip", event: "pair_id" }]);
  });
});
