/**
 * The client half of the capability table.
 *
 * Run: npx vitest run lib/modelInputs.test.ts
 *
 * The table itself is checked against Python by tests/test_models.py, which
 * also asserts this file's twin at mobile/lib/modelInputs.ts is identical —
 * so there is one copy of these tests and it covers both.
 *
 * What is tested here is the part Python has no counterpart for: turning the
 * table into what a picker will offer. Getting that wrong is quiet — she picks
 * a file, watches it upload, and the model answers as though it never arrived.
 */

import { describe, expect, it } from "vitest";

import {
  MODEL_INPUTS,
  VISION_MODELS,
  acceptAttribute,
  acceptsAnything,
  acceptsKind,
  describeAccepted,
  documentPickerTypes,
  kindOf,
} from "./modelInputs";

const GEMINI = "~google/gemini-pro-latest";
const GLM = "~z-ai/glm-latest";
const CLAUDE = "~anthropic/claude-fable-latest";

describe("which model takes what", () => {
  it("derives the vision set rather than keeping a second list", () => {
    expect(VISION_MODELS).toEqual(
      new Set(Object.keys(MODEL_INPUTS).filter((m) => MODEL_INPUTS[m].includes("image"))),
    );
  });

  it("offers nothing to a model nobody has heard of", () => {
    expect(acceptsKind("some/model-invented-tomorrow", "image")).toBe(false);
    expect(acceptsAnything("some/model-invented-tomorrow")).toBe(false);
  });

  it("still offers the attach button on the model that cannot see", () => {
    // It reads PDFs. Hiding the button on it — which is what keying the button
    // off VISION_MODELS did — left no way to hand it one.
    expect(acceptsKind(GLM, "image")).toBe(false);
    expect(acceptsAnything(GLM)).toBe(true);
  });
});

describe("kindOf", () => {
  it("separates a PDF from documents in general", () => {
    expect(kindOf("application/pdf")).toBe("pdf");
    expect(kindOf("text/plain")).toBe("text");
  });

  it("reads through a charset and through capitals", () => {
    expect(kindOf("TEXT/PLAIN; charset=utf-8")).toBe("text");
    expect(kindOf("IMAGE/JPEG")).toBe("image");
  });

  it("counts the text types that do not say text", () => {
    expect(kindOf("application/json")).toBe("text");
    expect(kindOf("application/x-yaml")).toBe("text");
  });

  it("calls a format nobody can open what it is", () => {
    expect(kindOf("application/zip")).toBe("other");
    expect(kindOf("")).toBe("other");
  });
});

describe("what the file dialog offers", () => {
  it("offers sound and video only on the model that takes them", () => {
    expect(acceptAttribute(GEMINI)).toContain("audio/*");
    expect(acceptAttribute(GEMINI)).toContain("video/*");
    expect(acceptAttribute(CLAUDE)).not.toContain("audio/*");
    expect(acceptAttribute(CLAUDE)).not.toContain("video/*");
  });

  it("does not offer pictures to the model that cannot see", () => {
    expect(acceptAttribute(GLM)).not.toContain("image/*");
    expect(acceptAttribute(GLM)).toContain("application/pdf");
  });

  it("every pattern it produces maps back to a kind the model accepts", () => {
    // The two functions are written separately and this is what keeps them
    // honest: an accept pattern for a kind the backend will drop is a file
    // uploaded for nothing.
    for (const model of Object.keys(MODEL_INPUTS)) {
      for (const pattern of acceptAttribute(model).split(",").filter(Boolean)) {
        if (pattern.startsWith(".")) continue; // extensions carry no MIME
        const kind = kindOf(pattern.replace("/*", "/x"));
        expect(acceptsKind(model, kind), `${model} offers ${pattern}`).toBe(true);
      }
    }
  });
});

describe("what the phone's document picker offers", () => {
  it("leaves photographs to the camera roll", () => {
    expect(documentPickerTypes(GEMINI)).not.toContain("image/*");
  });

  it("never comes back empty, which the picker reads as everything", () => {
    // A model that accepts nothing must filter to nothing, not to no filter.
    const types = documentPickerTypes("some/model-invented-tomorrow");
    expect(types.length).toBeGreaterThan(0);
    expect(types.some((t) => kindOf(t) !== "other")).toBe(false);
  });

  it("offers sound on Gemini and not on Claude", () => {
    expect(documentPickerTypes(GEMINI)).toContain("audio/*");
    expect(documentPickerTypes(CLAUDE)).not.toContain("audio/*");
  });
});

describe("what she is told the model reads", () => {
  it("names every kind, in both languages", () => {
    expect(describeAccepted(GEMINI, "ru")).toBe("фото, PDF, текстовые файлы, аудио, видео");
    expect(describeAccepted(GEMINI, "en")).toBe("photos, PDF, text files, audio, video");
  });

  it("says so plainly when there is nothing to attach", () => {
    expect(describeAccepted("some/model-invented-tomorrow", "ru")).toBe("только текст");
  });

  it("does not promise pictures on the model that cannot see", () => {
    expect(describeAccepted(GLM, "ru")).not.toContain("фото");
  });
});
