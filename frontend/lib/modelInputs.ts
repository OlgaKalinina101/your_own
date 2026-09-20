/**
 * What each model can be handed, beyond the text itself.
 *
 * This mirrors MODEL_INPUTS in infrastructure/llm/client.py, and the mirror is
 * the point: the client decides what the picker will even offer, the backend
 * decides what actually goes out. A model missing a kind here has the button
 * quietly greyed; a model missing it there has the file dropped on the way out
 * and answers about a document it was never shown. Both are silent, which is
 * why tests/test_models.py reads these literals straight out of the source and
 * compares them with Python's.
 *
 * Every entry was established by sending the real thing through OpenRouter —
 * a PDF holding a word no model could have memorised, a recording, a video —
 * not by reading the catalogue, which is wrong in both directions: Kimi's entry
 * advertises video it refuses, and GLM is listed text-only yet reads PDFs.
 *
 * The file this lives in is duplicated at mobile/lib/modelInputs.ts. The two
 * are not imported from one another because the phone and the web build from
 * separate trees; the test is what keeps them identical.
 */

export type InputKind = "image" | "pdf" | "text" | "audio" | "video" | "other";

export const MODEL_INPUTS: Record<string, InputKind[]> = {
  "~anthropic/claude-fable-latest": ["image", "pdf", "text"],
  "~moonshotai/kimi-latest": ["image", "pdf", "text"],
  "~google/gemini-pro-latest": ["image", "pdf", "text", "audio", "video"],
  "openai/gpt-chat-latest": ["image", "pdf", "text"],
  // The one that cannot be shown a photograph — but it does read PDFs, because
  // OpenRouter turns those into text before a provider ever sees them.
  "~z-ai/glm-latest": ["pdf", "text"],
};

/** Models that accept image attachments. Derived, never kept beside the table. */
export const VISION_MODELS = new Set(
  Object.keys(MODEL_INPUTS).filter((m) => MODEL_INPUTS[m].includes("image")),
);

/**
 * Whether a model can be handed an attachment of this kind.
 *
 * A model nobody has heard of accepts nothing: a new slug arrives with no
 * evidence behind it, and text-only is the failure that still answers.
 */
export function acceptsKind(model: string, kind: InputKind): boolean {
  return (MODEL_INPUTS[model] ?? []).includes(kind);
}

/** Whether a model can take any attachment at all — i.e. show the button. */
export function acceptsAnything(model: string): boolean {
  return (MODEL_INPUTS[model] ?? []).length > 0;
}

const TEXT_MIMES = new Set([
  "application/json",
  "application/xml",
  "application/javascript",
  "application/x-yaml",
  "application/yaml",
  "application/sql",
  "application/x-sh",
  "application/rtf",
]);

/**
 * Which kind a MIME type is.
 *
 * A PDF is its own kind and not a document-in-general: every model reads one,
 * while the same file part holding a .txt is refused by four of the five — so
 * text files are their own kind too, and are folded into the message as prose
 * rather than attached. Anything else is "other", which nothing accepts.
 */
export function kindOf(mime: string): InputKind {
  const m = (mime ?? "").toLowerCase().split(";")[0].trim();
  if (m.startsWith("image/")) return "image";
  if (m.startsWith("audio/")) return "audio";
  if (m.startsWith("video/")) return "video";
  if (m === "application/pdf") return "pdf";
  if (m.startsWith("text/") || TEXT_MIMES.has(m)) return "text";
  return "other";
}

/**
 * The `accept` attribute for a file input, given the chosen model.
 *
 * Offering a type the model cannot read gets the file dropped after she has
 * already waited for the upload, so the filter belongs on the picker.
 */
export function acceptAttribute(model: string): string {
  const kinds = MODEL_INPUTS[model] ?? [];
  const patterns: string[] = [];
  if (kinds.includes("image")) patterns.push("image/*");
  if (kinds.includes("audio")) patterns.push("audio/*");
  if (kinds.includes("video")) patterns.push("video/*");
  if (kinds.includes("pdf")) patterns.push("application/pdf");
  if (kinds.includes("text")) {
    patterns.push("text/*", ".md", ".json", ".csv", ".yml", ".yaml", ".log", ".py");
  }
  return patterns.join(",");
}

/**
 * The `type` filter for the phone's document picker, given the chosen model.
 *
 * The same question as `acceptAttribute` with a different answer shape: the
 * picker takes MIME patterns and no extensions, so a text file is offered as
 * "text/*" plus the few application/… types that are text in all but name.
 * Photographs are left out — they have their own door, the camera roll.
 */
export function documentPickerTypes(model: string): string[] {
  const kinds = MODEL_INPUTS[model] ?? [];
  const types: string[] = [];
  if (kinds.includes("pdf")) types.push("application/pdf");
  if (kinds.includes("text")) types.push("text/*", ...Array.from(TEXT_MIMES));
  if (kinds.includes("audio")) types.push("audio/*");
  if (kinds.includes("video")) types.push("video/*");
  // An empty list would mean "everything" to the picker, which is the opposite
  // of what no accepted kinds means.
  return types.length > 0 ? types : ["application/x-nothing-this-model-reads"];
}

/** What to tell her the current model will take, in her own language. */
export function describeAccepted(model: string, language: "ru" | "en"): string {
  const kinds = MODEL_INPUTS[model] ?? [];
  if (kinds.length === 0) return language === "ru" ? "только текст" : "text only";
  const names: Record<string, [string, string]> = {
    image: ["фото", "photos"],
    pdf: ["PDF", "PDF"],
    text: ["текстовые файлы", "text files"],
    audio: ["аудио", "audio"],
    video: ["видео", "video"],
  };
  const order: InputKind[] = ["image", "pdf", "text", "audio", "video"];
  return order
    .filter((k) => kinds.includes(k))
    .map((k) => names[k][language === "ru" ? 0 : 1])
    .join(", ");
}
