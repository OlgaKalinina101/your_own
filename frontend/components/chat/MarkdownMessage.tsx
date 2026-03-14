"use client";

import { memo, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { getApiBase } from "@/lib/api";

type MarkdownMessageProps = {
  content: string;
  role: "user" | "assistant";
  showCursor?: boolean;
  /** During streaming the last incomplete command should be hidden, not rendered */
  isStreaming?: boolean;
};

type CodeBlockProps = {
  code: string;
  language: string;
  role: "user" | "assistant";
};

type SkillBadgeKind = "save" | "search" | "web";

interface SkillCommand {
  kind: SkillBadgeKind;
  argument: string;
}

interface SavedFact {
  category: string;
  impressive: number;
  text: string;
}

interface GeneratedImage {
  path: string;
  model: string;
  prompt: string;
}

type InlinePart =
  | { type: "text"; text: string }
  | { type: "skill"; cmd: SkillCommand }
  | { type: "saved_fact"; fact: SavedFact }
  | { type: "generated_image"; image: GeneratedImage }
  | { type: "generating_image"; prompt: string };

// Groups: 1=skill_name, 2=skill_arg | 3=fact_cat, 4=fact_stars, 5=fact_text | 6=img_path, 7=img_model, 8=img_prompt
// GENERATE_IMAGE is matched but not rendered — just hidden from display (legacy DB entries)
const ALL_COMMANDS_RE =
  /\[(SAVE_MEMORY|SEARCH_MEMORIES|WEB_SEARCH|GENERATE_IMAGE|SCHEDULE_MESSAGE):\s*(.*?)\]|\[SAVED_FACT:\s*(.*?)\s*\|\s*(\d)\s*\|\s*(.*?)\]|\[GENERATED_IMAGE:\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\]/gs;

const KIND_MAP: Record<string, SkillBadgeKind> = {
  SAVE_MEMORY: "save",
  SEARCH_MEMORIES: "search",
  WEB_SEARCH: "web",
};

const BADGE_LABELS: Record<SkillBadgeKind, string> = {
  save: "saved to memory",
  search: "searched memories",
  web: "web search",
};

const BADGE_ICONS: Record<SkillBadgeKind, string> = {
  save: "✦",
  search: "◉",
  web: "⊕",
};

const PARTIAL_PREFIXES = [
  "[SAVE_MEMORY:",
  "[SEARCH_MEMORIES:",
  "[WEB_SEARCH:",
  "[SAVED_FACT:",
  "[GENERATED_IMAGE:",
  "[SCHEDULE_MESSAGE:",
];

function trimPartialCommand(text: string): string {
  const tail = text.slice(-25);
  const bracketIdx = tail.lastIndexOf("[");
  if (bracketIdx < 0) return text;
  const afterBracket = tail.slice(bracketIdx);
  for (const prefix of PARTIAL_PREFIXES) {
    if (prefix.startsWith(afterBracket) && afterBracket !== prefix) {
      return text.slice(0, text.length - (tail.length - bracketIdx)).trimEnd();
    }
  }
  return text;
}

function parseInlineParts(rawContent: string, isStreaming: boolean): InlinePart[] {
  const content = isStreaming ? trimPartialCommand(rawContent) : rawContent;
  const parts: InlinePart[] = [];
  let lastIndex = 0;
  const seenImagePaths = new Set<string>();

  for (const match of content.matchAll(ALL_COMMANDS_RE)) {
    const before = content.slice(lastIndex, match.index!);
    if (before) parts.push({ type: "text", text: before });

    if (match[1]) {
      if (match[1] === "SAVE_MEMORY" || match[1] === "SCHEDULE_MESSAGE") {
        lastIndex = match.index! + match[0].length;
        continue;
      }
      if (match[1] === "GENERATE_IMAGE") {
        const raw = match[2] ? match[2].trim() : "";
        const pipeIdx = raw.indexOf("|");
        const prompt = pipeIdx >= 0 ? raw.slice(pipeIdx + 1).trim() : raw;
        parts.push({ type: "generating_image", prompt });
        lastIndex = match.index! + match[0].length;
        continue;
      }
      const kind = KIND_MAP[match[1]] ?? "search";
      parts.push({
        type: "skill",
        cmd: { kind, argument: match[2] ? match[2].trim() : "" },
      });
    } else if (match[3] !== undefined) {
      parts.push({
        type: "saved_fact",
        fact: {
          category: match[3].trim(),
          impressive: parseInt(match[4], 10) || 0,
          text: match[5].trim(),
        },
      });
    } else if (match[6] !== undefined) {
      const imgPath = match[6].trim();
      if (seenImagePaths.has(imgPath)) {
        lastIndex = match.index! + match[0].length;
        continue;
      }
      seenImagePaths.add(imgPath);
      parts.push({
        type: "generated_image",
        image: {
          path: imgPath,
          model: match[7].trim(),
          prompt: match[8].trim(),
        },
      });
    }

    lastIndex = match.index! + match[0].length;
  }

  const tail = content.slice(lastIndex);
  if (tail) parts.push({ type: "text", text: tail });
  return parts;
}

function SkillBadge({ cmd }: { cmd: SkillCommand }) {
  if (cmd.kind === "save") return null;
  return (
    <div className="my-3 inline-flex w-fit items-center gap-2 border border-white/8 bg-white/[0.02] px-3 py-2 text-[0.64rem] tracking-[0.16em] uppercase text-white/42">
      <span className="text-white/30">{BADGE_ICONS[cmd.kind]}</span>
      <span>{BADGE_LABELS[cmd.kind]}</span>
      {cmd.argument && (
        <span className="max-w-[28rem] truncate normal-case tracking-wide text-white/30">
          {cmd.argument}
        </span>
      )}
    </div>
  );
}

function SavedFactCard({ fact }: { fact: SavedFact }) {
  return (
    <div className="my-3 flex w-fit items-start gap-2 border border-white/8 bg-white/[0.02] px-3 py-2 text-[0.65rem] tracking-[0.12em] text-white/40">
      <span className="mt-px shrink-0">✦</span>
      <span>
        <span className="uppercase tracking-[0.16em] text-white/55">{fact.category}</span>
        {fact.impressive >= 3 && (
          <span className="ml-1.5 text-white/30">{"★".repeat(Math.min(fact.impressive, 4))}</span>
        )}
        <span className="mx-1.5 text-white/20">—</span>
        <span className="normal-case tracking-wide italic text-white/45">{fact.text}</span>
      </span>
    </div>
  );
}

async function downloadImage(src: string, filename: string) {
  try {
    const res = await fetch(src);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch {
    // fallback: open in new tab
    window.open(src, "_blank");
  }
}

function ImageLightbox({ src, alt, filename, onClose }: { src: string; alt: string; filename: string; onClose: () => void }) {
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-sm"
      onClick={onClose}
    >
      {/* Close button — always visible in top-right corner of the screen */}
      <button
        type="button"
        onClick={onClose}
        className="fixed right-4 top-4 z-50 flex h-9 w-9 items-center justify-center rounded-full bg-white/10 text-white/80 backdrop-blur-sm transition-colors hover:bg-white/20 hover:text-white"
        aria-label="Close"
      >
        ✕
      </button>

      <div
        className="relative max-h-[90vh] max-w-[90vw]"
        onClick={(e) => e.stopPropagation()}
      >
        <img
          src={src}
          alt={alt}
          className="max-h-[90vh] max-w-[90vw] rounded object-contain shadow-2xl"
        />
        {/* Download button — bottom-right corner of the image */}
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); downloadImage(src, filename); }}
          className="absolute bottom-2 right-2 flex h-7 w-7 items-center justify-center rounded-full bg-black/60 text-white/70 transition-colors hover:bg-black/80 hover:text-white"
          title="Save image"
        >
          ↓
        </button>
      </div>
    </div>
  );
}

function GeneratingImageShimmer({ prompt }: { prompt: string }) {
  return (
    <div className="my-3 overflow-hidden rounded-lg border border-white/10 bg-white/[0.03]">
      <div className="relative flex items-center gap-3 px-4 py-3">
        <div className="relative flex h-5 w-5 items-center justify-center">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400/30" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-400/70" />
        </div>
        <div className="min-w-0 flex-1">
          <span className="text-[0.7rem] font-medium uppercase tracking-widest text-white/40">
            generating image…
          </span>
          {prompt && (
            <p className="mt-0.5 truncate text-[0.75rem] leading-tight text-white/20">
              {prompt}
            </p>
          )}
        </div>
      </div>
      <div className="h-0.5 w-full overflow-hidden bg-white/5">
        <div className="h-full w-1/3 animate-[shimmer_1.5s_ease-in-out_infinite] rounded bg-gradient-to-r from-transparent via-amber-400/30 to-transparent" />
      </div>
    </div>
  );
}

function GeneratedImageCard({ image }: { image: GeneratedImage }) {
  const [lightboxOpen, setLightboxOpen] = useState(false);

  const src = image.path.startsWith("http") ? image.path : `${getApiBase()}${image.path}`;
  const filename = image.path.split("/").pop() ?? "image.png";
  const modelName = image.model.split("/").pop() ?? image.model;
  const promptPreview = image.prompt.length > 80 ? image.prompt.slice(0, 77) + "..." : image.prompt;

  return (
    <>
      <div className="my-3 max-w-sm overflow-hidden border border-white/10 bg-white/[0.02]">
        {/* div wrapper (not button/a) so right-click → "Save image as" works natively */}
        <div
          className="block w-full cursor-zoom-in"
          onClick={() => setLightboxOpen(true)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setLightboxOpen(true); }}
          aria-label="Open image"
        >
          <img
            src={src}
            alt={image.prompt}
            className="w-full block transition-opacity duration-200 hover:opacity-90"
            loading="lazy"
            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
          />
        </div>
        <div className="flex items-center gap-2 border-t border-white/8 px-3 py-2 text-[0.62rem] tracking-wide text-white/35">
          <span className="text-white/20">⬡</span>
          <span className="uppercase tracking-[0.14em] text-white/45">{modelName}</span>
          <span className="text-white/15">·</span>
          <span className="flex-1 italic truncate">{promptPreview}</span>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); downloadImage(src, filename); }}
            className="ml-auto shrink-0 text-white/30 transition-colors hover:text-white/70"
            title="Save image"
          >
            ↓
          </button>
        </div>
      </div>
      {lightboxOpen && (
        <ImageLightbox src={src} alt={image.prompt} filename={filename} onClose={() => setLightboxOpen(false)} />
      )}
    </>
  );
}

function BlockCode({ code, language, role }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) {
      return;
    }
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const canCopy = typeof navigator !== "undefined" && !!navigator.clipboard;
  const headerTone = role === "user" ? "text-white/55" : "text-white/45";
  const panelTone = role === "user" ? "border-white/12 bg-white/[0.04]" : "border-white/10 bg-white/[0.03]";

  const handleCopy = async () => {
    if (!canCopy) {
      return;
    }
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
    } catch {
      // Ignore clipboard failures and keep rendering stable.
    }
  };

  return (
    <div className={`my-3 overflow-hidden border ${panelTone}`}>
      <div className="flex items-center justify-between gap-4 border-b border-white/10 px-3 py-2">
        <span className={`text-[0.62rem] uppercase tracking-[0.18em] ${headerTone}`}>
          {language || "code"}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          disabled={!canCopy}
          className="text-[0.62rem] uppercase tracking-[0.18em] text-white/45 transition-colors duration-200 hover:text-white/85 disabled:cursor-default disabled:opacity-40"
        >
          {copied ? "copied" : "copy"}
        </button>
      </div>
      <pre className="overflow-x-auto p-4 text-[0.82rem] leading-7 text-white/88">
        <code className="font-mono">{code}</code>
      </pre>
    </div>
  );
}

function MarkdownBlock({ text, role }: { text: string; role: "user" | "assistant" }) {
  const textTone = role === "user" ? "text-white" : "text-white/85";
  const mutedTone = role === "user" ? "text-white/78" : "text-white/70";
  const borderTone = role === "user" ? "border-white/20" : "border-white/15";
  const inlineCodeTone = role === "user" ? "bg-white/10 text-white" : "bg-white/8 text-white/90";
  const quoteTone = role === "user" ? "border-white/25 text-white/85" : "border-white/20 text-white/75";
  const linkTone = role === "user" ? "text-white underline decoration-white/40 underline-offset-4" : "text-white underline decoration-white/30 underline-offset-4";

  const components = useMemo(
    () => ({
      p: ({ children }: any) => <p className={`mb-4 last:mb-0 ${textTone}`}>{children}</p>,
      strong: ({ children }: any) => <strong className="font-semibold text-white">{children}</strong>,
      em: ({ children }: any) => <em className="italic">{children}</em>,
      a: ({ href, children }: any) => (
        <a
          href={href}
          target="_blank"
          rel="noreferrer"
          className={`${linkTone} transition-colors duration-200 hover:text-white`}
        >
          {children}
        </a>
      ),
      blockquote: ({ children }: any) => (
        <blockquote className={`my-4 border-l-2 pl-4 italic ${quoteTone}`}>{children}</blockquote>
      ),
      h1: ({ children }: any) => <h1 className="mb-3 mt-5 text-[1.4rem] font-semibold text-white first:mt-0">{children}</h1>,
      h2: ({ children }: any) => <h2 className="mb-3 mt-5 text-[1.22rem] font-semibold text-white first:mt-0">{children}</h2>,
      h3: ({ children }: any) => <h3 className="mb-2 mt-4 text-[1.08rem] font-semibold text-white first:mt-0">{children}</h3>,
      h4: ({ children }: any) => <h4 className="mb-2 mt-4 text-[1rem] font-semibold text-white first:mt-0">{children}</h4>,
      h5: ({ children }: any) => <h5 className="mb-2 mt-4 text-[0.95rem] font-semibold text-white first:mt-0">{children}</h5>,
      h6: ({ children }: any) => <h6 className="mb-2 mt-4 text-[0.9rem] font-semibold text-white/90 first:mt-0">{children}</h6>,
      hr: () => <hr className={`my-5 border-t ${borderTone}`} />,
      ul: ({ children }: any) => <ul className={`mb-4 ml-5 list-disc space-y-2 ${textTone}`}>{children}</ul>,
      ol: ({ children }: any) => <ol className={`mb-4 ml-5 list-decimal space-y-2 ${textTone}`}>{children}</ol>,
      li: ({ children }: any) => <li className={mutedTone}>{children}</li>,
      table: ({ children }: any) => (
        <div className="my-4 overflow-x-auto">
          <table className="min-w-full border-collapse text-left text-[0.82rem]">{children}</table>
        </div>
      ),
      thead: ({ children }: any) => <thead className="border-b border-white/15 text-white/75">{children}</thead>,
      tbody: ({ children }: any) => <tbody>{children}</tbody>,
      tr: ({ children }: any) => <tr className="border-b border-white/8 last:border-b-0">{children}</tr>,
      th: ({ children }: any) => <th className="px-3 py-2 font-medium">{children}</th>,
      td: ({ children }: any) => <td className="px-3 py-2 text-white/78">{children}</td>,
      code: ({ inline, className, children, ...props }: any) => {
        const rawCode = String(children ?? "").replace(/\n$/, "");
        const language = /language-([\w-]+)/.exec(className || "")?.[1] ?? "";
        if (inline) {
          return (
            <code className={`rounded px-1.5 py-0.5 font-mono text-[0.84em] ${inlineCodeTone}`} {...props}>
              {children}
            </code>
          );
        }
        return <BlockCode code={rawCode} language={language} role={role} />;
      },
    }),
    [borderTone, inlineCodeTone, linkTone, mutedTone, quoteTone, role, textTone],
  );

  return (
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={components}>
      {text.trim()}
    </ReactMarkdown>
  );
}

function MarkdownMessageInner({ content, role, showCursor = false, isStreaming = false }: MarkdownMessageProps) {
  const textTone = role === "user" ? "text-white" : "text-white/85";
  const parts = useMemo(
    () => (role === "assistant" ? parseInlineParts(content, isStreaming) : [{ type: "text" as const, text: content }]),
    [content, role, isStreaming],
  );

  return (
    <div className={`max-w-[80%] text-[0.95rem] font-light leading-relaxed tracking-wide ${textTone}`}>
      {parts.map((part, i) =>
        part.type === "skill" ? (
          <SkillBadge key={`skill-${i}`} cmd={part.cmd} />
        ) : part.type === "saved_fact" ? (
          <SavedFactCard key={`fact-${i}`} fact={part.fact} />
        ) : part.type === "generated_image" ? (
          <GeneratedImageCard key={`img-${i}`} image={part.image} />
        ) : part.type === "generating_image" ? (
          <GeneratingImageShimmer key={`shimmer-${i}`} prompt={part.prompt} />
        ) : (
          <MarkdownBlock key={`md-${i}`} text={part.text} role={role} />
        ),
      )}
      {showCursor && <span className="ml-0.5 inline-block h-3 w-[1px] animate-pulse bg-white/60 align-middle" />}
    </div>
  );
}

const MarkdownMessage = memo(MarkdownMessageInner);
export default MarkdownMessage;
