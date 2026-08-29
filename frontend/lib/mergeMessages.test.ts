import { describe, expect, it } from "vitest";

import { latestCursor, mergeMessages } from "./mergeMessages";
import type { Message } from "./types";

const msg = (over: Partial<Message> & { id: string }): Message => ({
  role: "user",
  content: "",
  ...over,
});

describe("mergeMessages", () => {
  it("leaves the list alone when the server has nothing new", () => {
    const current = [msg({ id: "a", pairId: "p1" })];
    expect(mergeMessages(current, [])).toBe(current);
  });

  it("appends a pair that arrived from the other device", () => {
    // The whole complaint: chat on the phone, come back here, see it.
    const current = [msg({ id: "p1-user", pairId: "p1", content: "старое" })];
    const incoming = [
      msg({ id: "p2-user", pairId: "p2", content: "с телефона" }),
      msg({ id: "p2-assistant", pairId: "p2", role: "assistant", content: "ответ" }),
    ];
    const merged = mergeMessages(current, incoming);
    expect(merged.map((m) => m.content)).toEqual(["старое", "с телефона", "ответ"]);
  });

  it("does not duplicate a message this client sent itself", () => {
    // The optimistic copy carries a client-generated id but the same pair_id,
    // which is the only thing tying it to what the server stored.
    const current = [
      msg({ id: "user-uuid-local", pairId: "p1", content: "привет" }),
      msg({ id: "assistant-uuid-local", pairId: "p1", role: "assistant", content: "ответ" }),
    ];
    const incoming = [
      msg({ id: "p1-user", pairId: "p1", content: "привет", createdAt: "2026-08-30T10:00:00Z" }),
      msg({
        id: "p1-assistant",
        pairId: "p1",
        role: "assistant",
        content: "ответ",
        createdAt: "2026-08-30T10:00:01Z",
      }),
    ];
    const merged = mergeMessages(current, incoming);
    expect(merged).toHaveLength(2);
    expect(merged[0].id).toBe("p1-user");
    expect(merged[0].createdAt).toBe("2026-08-30T10:00:00Z");
  });

  it("keeps a message that is still being streamed", () => {
    // No pair_id yet, so nothing can supersede it — and dropping it would
    // erase a reply while the user is watching it arrive.
    const current = [
      msg({ id: "p1-user", pairId: "p1" }),
      msg({ id: "assistant-live", role: "assistant", content: "печатает…" }),
    ];
    const incoming = [msg({ id: "p2-user", pairId: "p2", content: "новое" })];
    const merged = mergeMessages(current, incoming);
    expect(merged.map((m) => m.id)).toEqual(["p1-user", "assistant-live", "p2-user"]);
  });

  it("lets the server correct what was shown", () => {
    // The stored reply can differ from the streamed one: a rewrite landed, or
    // the stream broke and the backend saved the clipped version.
    const current = [msg({ id: "p1-a", pairId: "p1", role: "assistant", content: "черновик" })];
    const incoming = [msg({ id: "p1-assistant", pairId: "p1", role: "assistant", content: "итог" })];
    expect(mergeMessages(current, incoming)[0].content).toBe("итог");
  });

  it("keeps recalled facts, which history does not carry", () => {
    const facts = [
      { id: "f1", text: "t", category: "c", impressive: 3, time_label: "вчера" },
    ];
    const current = [msg({ id: "p1-a", pairId: "p1", role: "assistant", chromaFacts: facts })];
    const incoming = [msg({ id: "p1-assistant", pairId: "p1", role: "assistant" })];
    expect(mergeMessages(current, incoming)[0].chromaFacts).toEqual(facts);
  });

  it("keeps the reason a reply was cut off", () => {
    // The server stores the clipped text and has no idea it is clipped, so a
    // plain replace would quietly turn "the tunnel ate this" into "he answered
    // in four words". Writing the marker into `content` instead of a field is
    // the version of this that loses: `content` is exactly what the server wins.
    const current = [
      msg({ id: "p1-a", pairId: "p1", role: "assistant", content: "Пол", interrupted: "connection" }),
    ];
    const incoming = [
      msg({ id: "p1-assistant", pairId: "p1", role: "assistant", content: "Пол" }),
    ];
    const merged = mergeMessages(current, incoming);
    expect(merged[0].interrupted).toBe("connection");
    expect(merged[0].id).toBe("p1-assistant");
  });

  it("does not invent a reason for a reply that arrived whole", () => {
    const current = [msg({ id: "p1-a", pairId: "p1", role: "assistant", content: "Полный ответ" })];
    const incoming = [
      msg({ id: "p1-assistant", pairId: "p1", role: "assistant", content: "Полный ответ" }),
    ];
    expect(mergeMessages(current, incoming)[0].interrupted).toBeUndefined();
  });

  it("matches on role as well as pair, so the two halves do not collide", () => {
    const current = [
      msg({ id: "p1-user", pairId: "p1", role: "user", content: "вопрос" }),
      msg({ id: "p1-assistant", pairId: "p1", role: "assistant", content: "ответ" }),
    ];
    const incoming = [
      msg({ id: "p1-user", pairId: "p1", role: "user", content: "вопрос!" }),
      msg({ id: "p1-assistant", pairId: "p1", role: "assistant", content: "ответ!" }),
    ];
    expect(mergeMessages(current, incoming).map((m) => m.content)).toEqual([
      "вопрос!",
      "ответ!",
    ]);
  });

  it("is idempotent — the same sync twice changes nothing", () => {
    const current = [msg({ id: "p1-user", pairId: "p1" })];
    const incoming = [msg({ id: "p2-user", pairId: "p2" })];
    const once = mergeMessages(current, incoming);
    expect(mergeMessages(once, incoming)).toEqual(once);
  });

  it("does not mutate what it was given", () => {
    const current = [msg({ id: "a", pairId: "p1" })];
    mergeMessages(current, [msg({ id: "b", pairId: "p2" })]);
    expect(current).toHaveLength(1);
  });
});

describe("latestCursor", () => {
  it("is the newest timestamp on screen", () => {
    expect(
      latestCursor([
        msg({ id: "a", createdAt: "2026-08-30T10:00:00Z" }),
        msg({ id: "b", createdAt: "2026-08-30T12:00:00Z" }),
        msg({ id: "c", createdAt: "2026-08-30T11:00:00Z" }),
      ]),
    ).toBe("2026-08-30T12:00:00Z");
  });

  it("ignores messages with no timestamp", () => {
    // A message being streamed has none; letting it win would produce null and
    // refetch the entire conversation.
    expect(
      latestCursor([
        msg({ id: "a", createdAt: "2026-08-30T10:00:00Z" }),
        msg({ id: "live" }),
      ]),
    ).toBe("2026-08-30T10:00:00Z");
  });

  it("is null for an empty or timestamp-less list, meaning 'send me the page'", () => {
    expect(latestCursor([])).toBeNull();
    expect(latestCursor([msg({ id: "live" })])).toBeNull();
  });
});
