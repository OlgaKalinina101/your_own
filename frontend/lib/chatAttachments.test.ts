import { describe, expect, it } from "vitest";

import {
  MAX_CHAT_IMAGES,
  fitWithinCap,
  imageFilesFromClipboard,
  removeAt,
  type ClipboardImageItem,
} from "./chatAttachments";

describe("fitWithinCap", () => {
  it("takes everything when there is room", () => {
    expect(fitWithinCap(0, ["a", "b"], 8)).toEqual({ accepted: ["a", "b"], rejected: 0 });
  });

  it("fills exactly to the cap", () => {
    expect(fitWithinCap(6, ["a", "b"], 8)).toEqual({ accepted: ["a", "b"], rejected: 0 });
  });

  it("reports the overflow instead of losing it quietly", () => {
    // The page drops these without a word today; the count is here so it can stop.
    expect(fitWithinCap(7, ["a", "b", "c"], 8)).toEqual({ accepted: ["a"], rejected: 2 });
  });

  it("accepts nothing when already full", () => {
    expect(fitWithinCap(8, ["a"], 8)).toEqual({ accepted: [], rejected: 1 });
  });

  it("does not go negative when somehow over the cap", () => {
    expect(fitWithinCap(99, ["a"], 8)).toEqual({ accepted: [], rejected: 1 });
  });

  it("defaults to the shared cap", () => {
    expect(fitWithinCap(MAX_CHAT_IMAGES - 1, ["a", "b"])).toEqual({
      accepted: ["a"],
      rejected: 1,
    });
  });
});

describe("imageFilesFromClipboard", () => {
  const file = (name: string) => ({ name }) as unknown as File;
  const item = (type: string, produced: File | null): ClipboardImageItem => ({
    type,
    getAsFile: () => produced,
  });

  it("keeps images and drops everything else", () => {
    const png = file("a.png");
    const result = imageFilesFromClipboard([
      item("text/plain", file("ignored.txt")),
      item("image/png", png),
    ]);
    expect(result).toEqual([png]);
  });

  it("skips an image the clipboard refuses to hand over", () => {
    // Real case: an item announces image/png and getAsFile() returns null.
    expect(imageFilesFromClipboard([item("image/png", null)])).toEqual([]);
  });

  it("keeps the clipboard's order", () => {
    const a = file("a.png");
    const b = file("b.jpg");
    expect(
      imageFilesFromClipboard([
        item("image/png", a),
        item("text/html", file("x")),
        item("image/jpeg", b),
      ]),
    ).toEqual([a, b]);
  });

  it("returns nothing for a text-only paste", () => {
    expect(imageFilesFromClipboard([item("text/plain", null)])).toEqual([]);
  });
});

describe("removeAt", () => {
  it("drops the index and keeps the rest in order", () => {
    expect(removeAt(["a", "b", "c"], 1)).toEqual(["a", "c"]);
  });

  it("leaves the list alone for an index that is not there", () => {
    expect(removeAt(["a", "b"], 5)).toEqual(["a", "b"]);
  });

  it("does not mutate the input", () => {
    const original = ["a", "b"];
    removeAt(original, 0);
    expect(original).toEqual(["a", "b"]);
  });

  it("keeps two parallel lists aligned", () => {
    // Files and their previews are two arrays indexed together; removing from
    // one and not the other is what shows the wrong thumbnail.
    const files = ["f0", "f1", "f2"];
    const previews = ["p0", "p1", "p2"];
    expect(removeAt(files, 1)).toEqual(["f0", "f2"]);
    expect(removeAt(previews, 1)).toEqual(["p0", "p2"]);
  });
});
