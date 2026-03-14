"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { apiGet, apiPut } from "@/lib/api";

const PLACEHOLDER = `You are...

Write your companion's soul here.
This text becomes the system prompt — the core identity passed to the model on every message.`;

export default function SoulPage() {
  const router = useRouter();
  const [text, setText]   = useState("");
  const [saved, setSaved] = useState(false);
  const [chars, setChars] = useState(0);
  const textareaRef       = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    apiGet<{ text: string }>("/api/settings/soul")
      .then(({ text: val }) => {
        if (val) {
          setText(val);
          setChars(val.length);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    setChars(e.target.value.length);
    setSaved(false);
  };

  const handleSave = async () => {
    try {
      await apiPut("/api/settings/soul", { text });
    } catch (err) {
      console.error("Failed to save soul:", err);
    }
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
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
            className="text-[0.68rem] tracking-[0.2em] uppercase text-white/50 transition-colors duration-300 hover:text-white/90"
          >
            {saved ? "saved" : "save"}
          </button>
        </div>
      </header>

      {/* ── Editor ─────────────────────────────────────────────────────────── */}
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

      {/* ── Footer hint ────────────────────────────────────────────────────── */}
      <footer className="shrink-0 border-t border-white/[0.06] px-8 py-3">
        <p className="text-[0.62rem] tracking-[0.12em] text-white/35">
          ⌘S to save&nbsp;&nbsp;·&nbsp;&nbsp;passed to every message as system prompt
        </p>
      </footer>
    </div>
  );
}
