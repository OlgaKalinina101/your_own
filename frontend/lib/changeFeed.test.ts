/**
 * The seam between the two halves of the change channel.
 *
 * The frames below are the ones `api/events_api.py` actually emits — the same
 * strings its own tests assert on. If the server's framing and the client's
 * reading of it ever disagree, the symptom is silent: either a chat that never
 * updates, or one that refetches every twenty seconds forever.
 */
import { describe, expect, it } from "vitest";

import { isChangeFrame } from "./changeFeed";
import { splitSseBuffer } from "./chatSse";

// Exactly what the endpoint yields.
const CHANGE = 'event: pairs_changed\ndata: {"account_id": "default", "origin": "assistant"}';
const KEEPALIVE = ": keepalive";

describe("isChangeFrame", () => {
  it("treats a published event as a reason to sync", () => {
    expect(isChangeFrame(CHANGE)).toBe(true);
  });

  it("does not treat the keepalive as one", () => {
    // Otherwise this is a 20-second poll wearing an event channel's clothes.
    expect(isChangeFrame(KEEPALIVE)).toBe(false);
  });

  it("ignores an empty frame", () => {
    expect(isChangeFrame("")).toBe(false);
  });

  it("is not fooled by a comment that mentions data", () => {
    expect(isChangeFrame(": data: not really")).toBe(false);
  });
});

describe("a stream of frames as the reader sees it", () => {
  function syncsFrom(pieces: string[]): number {
    let buffer = "";
    let syncs = 0;
    for (const piece of pieces) {
      buffer += piece;
      const { events, remainder } = splitSseBuffer(buffer);
      buffer = remainder;
      for (const raw of events) if (isChangeFrame(raw)) syncs += 1;
    }
    return syncs;
  }

  it("syncs once per event, never for keepalives", () => {
    const stream =
      `${KEEPALIVE}\n\n` + `${CHANGE}\n\n` + `${KEEPALIVE}\n\n` + `${CHANGE}\n\n`;
    expect(syncsFrom([stream])).toBe(2);
  });

  it("reads the same when the frames arrive in arbitrary pieces", () => {
    const stream = `${KEEPALIVE}\n\n${CHANGE}\n\n`;
    expect(syncsFrom(stream.split(""))).toBe(1);
  });

  it("does not fire on a frame that is still half-arrived", () => {
    // The other order of this bug: syncing on a partial frame, then again when
    // it completes.
    expect(syncsFrom(["event: pairs_ch"])).toBe(0);
  });
});
