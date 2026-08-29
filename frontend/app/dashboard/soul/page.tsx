"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { apiGet, apiPut } from "@/lib/api";
import { describeApiError } from "@/lib/apiError";

const PLACEHOLDER = `You are...

Write your companion's soul here.
This text becomes the system prompt — the core identity passed to the model on every message.`;

export default function SoulPage() {
  const router = useRouter();
  const [text, setText]   = useState("");
  const [saved, setSaved] = useState(false);
  const [chars, setChars] = useState(0);
  const textareaRef       = useRef<HTMLTextAreaElement>(null);

  // Loading has to be a state, not a silence. A failed load left an empty
  // textarea that looked like an empty soul, and the next Save wrote that
  // emptiness over the real one — the failure mode here is losing the text,
  // not seeing a wrong message.
  const [loadState, setLoadState] = useState<"loading" | "error" | "ready">("loading");
  const [loadError, setLoadError] = useState("");

  const load = useCallback(() => {
    setLoadState("loading");
    apiGet<{ text: string }>("/api/settings/soul")
      .then(({ text: val }) => {
        setText(val ?? "");
        setChars((val ?? "").length);
        setLoadState("ready");
      })
      .catch((err: unknown) => {
        setLoadError(describeApiError(err));
        setLoadState("error");
      });
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    setChars(e.target.value.length);
    setSaved(false);
  };

  const [saveError, setSaveError] = useState("");

  const handleSave = async () => {
    // Never write over a soul this page failed to read.
    if (loadState !== "ready") return;
    try {
      setSaveError("");
      await apiPut("/api/settings/soul", { text });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err: unknown) {
      // It used to say "saved" here regardless, which is worse than saying
      // nothing: it invites closing the window.
      setSaveError(describeApiError(err));
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Cmd/Ctrl + S to save
    if ((e.metaKey || e.ctrlKey) && e.key === "s") {
      e.preventDefault();
      handleSave();
    }
  };

  return (
    <div className="flex h-screen w-screen flex-col bg-black">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="flex shrink-0 items-center justify-between border-b border-white/10 px-8 py-4">
        <button
          onClick={() => router.push("/dashboard")}
          className="text-[0.68rem] tracking-[0.2em] uppercase text-white/25 transition-colors duration-300 hover:text-white/60"
        >
          ← back
        </button>

        <div className="flex items-center gap-6">
          <span className="text-[0.65rem] tracking-[0.12em] text-white/35 tabular-nums">
            {chars.toLocaleString()} chars
          </span>
          <button
            onClick={handleSave}
            disabled={loadState !== "ready"}
            className="text-[0.68rem] tracking-[0.2em] uppercase text-white/50 transition-colors duration-300 hover:text-white/90 disabled:cursor-not-allowed disabled:text-white/20 disabled:hover:text-white/20"
          >
            {saved ? "saved" : "save"}
          </button>
        </div>
      </header>

      {saveError && (
        <div className="shrink-0 border-b border-red-500/25 bg-red-500/[0.07] px-8 py-2.5">
          <span className="text-[0.72rem] tracking-wide text-red-200/80">
            Not saved — {saveError}
          </span>
        </div>
      )}

      {/* ── Editor ─────────────────────────────────────────────────────────── */}
      {loadState === "error" ? (
        // Not an empty editor: an empty editor invites typing, and typing here
        // would replace a soul this page never managed to read.
        <div className="flex flex-1 flex-col items-center justify-center gap-4">
          <span className="text-[0.75rem] tracking-wide text-red-200/70">{loadError}</span>
          <span className="text-[0.65rem] tracking-wide text-white/30">
            The editor stays closed so nothing overwrites what is stored.
          </span>
          <button
            type="button"
            onClick={load}
            className="text-[0.62rem] tracking-[0.2em] uppercase text-white/40 transition-colors hover:text-white/80"
          >
            retry
          </button>
        </div>
      ) : loadState === "loading" ? (
        <div className="flex flex-1 items-center justify-center">
          <span className="text-[0.7rem] tracking-[0.2em] uppercase text-white/25 animate-pulse">
            loading soul…
          </span>
        </div>
      ) : (
      <div className="flex flex-1 overflow-hidden">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={PLACEHOLDER}
          spellCheck={false}
          className="
            h-full w-full resize-none bg-transparent
            px-16 py-12
            text-[1.05rem] font-light leading-[1.9] tracking-wide
            text-white/90
            placeholder:text-white/30
            outline-none
            caret-white/60
          "
        />
      </div>
      )}

      {/* ── Footer hint ────────────────────────────────────────────────────── */}
      <footer className="shrink-0 border-t border-white/[0.06] px-8 py-3">
        <p className="text-[0.62rem] tracking-[0.12em] text-white/35">
          ⌘S to save&nbsp;&nbsp;·&nbsp;&nbsp;passed to every message as system prompt
        </p>
      </footer>
    </div>
  );
}
