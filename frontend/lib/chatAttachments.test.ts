import { describe, expect, it } from "vitest";

import {
  MAX_CHAT_IMAGES,
  fileLabel,
  fitWithinCap,
  imageFilesFromClipboard,
  readPreview,
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

describe("previewing what was picked", () => {
  it("reads a picture into a data URL", async () => {
    // These tests run under node, which has no FileReader. Stubbing it keeps
    // the environment as it is for every other test in the suite.
    class StubReader {
      result = "";
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      readAsDataURL() {
        this.result = "data:image/png;base64,iVBOR";
        this.onload?.();
      }
    }
    // Narrowed rather than cast to `any`: this file is linted as part of the
    // production build, and one `any` here fails `next build` on the server.
    const env = globalThis as unknown as { FileReader?: unknown };
    const original = env.FileReader;
    env.FileReader = StubReader;
    try {
      const png = new File([new Uint8Array([137, 80, 78, 71])], "shot.png", { type: "image/png" });
      await expect(readPreview(png)).resolves.toMatch(/^data:/);
    } finally {
      env.FileReader = original;
    }
  });

  it("does not even construct a reader for a non-image", async () => {
    // The guard is before the FileReader, not inside its callback — so this
    // resolves with no reader in the environment at all.
    const env = globalThis as unknown as { FileReader?: unknown };
    const pdf = new File([], "doc.pdf", { type: "application/pdf" });
    expect(env.FileReader).toBeUndefined();
    await expect(readPreview(pdf)).resolves.toBe("");
  });

  it("does not read anything else", async () => {
    // A PDF read this way renders as a broken <img>, and a 20 MB video read
    // this way is a 27 MB base64 string held in React state to produce one.
    const pdf = new File([new Uint8Array([37, 80, 68, 70])], "doc.pdf", { type: "application/pdf" });
    await expect(readPreview(pdf)).resolves.toBe("");
  });
});

describe("fileLabel", () => {
  it("names the type from the extension", () => {
    expect(fileLabel(new File([], "invoice.pdf", { type: "application/pdf" }))).toBe("PDF");
    expect(fileLabel(new File([], "voice.mp3", { type: "audio/mpeg" }))).toBe("MP3");
  });

  it("falls back to the MIME type when there is no extension", () => {
    expect(fileLabel(new File([], "README", { type: "text/plain" }))).toBe("PLAI");
  });

  it("says something rather than nothing when it knows neither", () => {
    expect(fileLabel(new File([], "mystery", { type: "" }))).toBe("FILE");
  });
});
