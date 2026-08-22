"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiGet, apiPut } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

interface SkillInfo {
  id: string;
  cmd_name: string;
  display: { en: string; ru: string };
  description: { en: string; ru: string };
  example: string | null;
  action_type: string;
  enabled: boolean;
}

// ── How-it-works steps (static) ──────────────────────────────────────────────

const HOW_IT_WORKS = [
  {
    step: "01",
    text: "AI streams its reply. If skill commands appear, the backend parses them from the text",
    textRu: "AI стримит ответ. Если появляются команды навыков, бэкенд парсит их из текста",
  },
  {
    step: "02",
    text: "For [SEARCH_DIALOGUE] or [WEB_SEARCH], the research agent runs the search, judges the result, re-queries if it missed, and feeds a brief back — AI continues with new context",
    textRu: "Для [SEARCH_DIALOGUE] или [WEB_SEARCH] агент-поисковик выполняет поиск, оценивает результат, переформулирует запрос при промахе и возвращает сводку — AI продолжает с новым контекстом",
  },
  {
    step: "03",
    text: "For [GENERATE_IMAGE], the backend calls GPT-5 or Gemini, saves the PNG, and shows it inline with a pulsing shimmer during generation",
    textRu: "Для [GENERATE_IMAGE] бэкенд вызывает GPT-5 или Gemini, сохраняет PNG и показывает его в чате с пульсирующей анимацией во время генерации",
  },
  {
    step: "04",
    text: "For [SAVE_MEMORY], a key fact is extracted via LLM, rated 1–4, deduplicated by AI, and stored in ChromaDB",
    textRu: "Для [SAVE_MEMORY] ключевой факт извлекается через LLM, оценивается 1–4, дедуплицируется с помощью AI и сохраняется в ChromaDB",
  },
];

// ── Status badge / button ────────────────────────────────────────────────────

// ── Page ─────────────────────────────────────────────────────────────────────

export default function SkillsPage() {
  const router = useRouter();
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiGet<{ skills: SkillInfo[] }>("/api/settings/skills")
      .then((d) => setSkills(d.skills))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const toggleSkill = useCallback(
    async (id: string, enabled: boolean) => {
      const updated = skills.map((s) =>
        s.id === id ? { ...s, enabled } : s,
      );
      setSkills(updated);

      const allEnabled = updated.every((s) => s.enabled);
      const enabledIds = allEnabled
        ? null
        : updated.filter((s) => s.enabled).map((s) => s.id);

      setSaving(true);
      try {
        await apiPut("/api/settings", { enabled_skills: enabledIds });
      } catch {
        setSkills(skills);
      } finally {
        setSaving(false);
      }
    },
    [skills],
  );

  return (
    <div className="flex h-screen w-screen flex-col bg-black text-white">

      {/* ── Header ── */}
      <header className="shrink-0 flex items-center justify-between border-b border-white/8 px-12 py-6">
        <div className="flex flex-col gap-1">
          <h1 className="text-[1.4rem] font-extralight tracking-[0.28em] uppercase text-white/80">
            Skills
          </h1>
          <p className="text-[0.62rem] tracking-[0.18em] uppercase text-white/25">
            AI-invoked capabilities · agentic pipeline
          </p>
        </div>
        <button
          onClick={() => router.push("/dashboard")}
          className="text-[0.65rem] tracking-[0.2em] uppercase text-white/30 transition-colors hover:text-white/75"
        >
          ← dashboard
        </button>
      </header>

      {/* ── Body ── */}
      <div className="flex-1 overflow-y-auto px-12 py-10">
        <div className="mx-auto max-w-4xl flex flex-col gap-14">

          {/* ── Skill cards grid ── */}
          {loading ? (
            <div className="flex items-center justify-center py-20">
              <span className="text-[0.7rem] tracking-[0.2em] uppercase text-white/25 animate-pulse">
                loading skills…
              </span>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {skills.map((skill) => (
                <div
                  key={skill.id}
                  className={`
                    border p-6 flex flex-col gap-4
                    transition-colors duration-300
                    ${skill.enabled
                      ? "border-white/18 hover:border-white/40 bg-white/[0.015]"
                      : "border-white/8 bg-black opacity-50"
                    }
                  `}
                >
                  {/* Top row */}
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex flex-col gap-1.5">
                      <span className="text-[0.95rem] font-light tracking-[0.18em] uppercase text-white/75">
                        {skill.display.en}
                      </span>
                      <span className="text-[0.72rem] tracking-[0.1em] text-white/35">
                        {skill.display.ru}
                      </span>
                    </div>
                    <button
                      onClick={() => toggleSkill(skill.id, !skill.enabled)}
                      disabled={saving}
                      className={`
                        shrink-0 border px-2.5 py-1 text-[0.55rem] tracking-[0.2em] uppercase
                        transition-colors duration-200 cursor-pointer
                        ${skill.enabled
                          ? "border-white/25 text-white/50 hover:border-white/40 hover:text-white/70"
                          : "border-white/10 text-white/20 hover:border-white/20 hover:text-white/35"
                        }
                        ${saving ? "opacity-40 cursor-not-allowed" : ""}
                      `}
                    >
                      {skill.enabled ? "enabled" : "disabled"}
                    </button>
                  </div>

                  {/* Command chip */}
                  <code className="block w-full border border-white/10 bg-white/[0.03] px-3 py-2 text-[0.68rem] tracking-[0.06em] text-white/45 font-mono">
                    [{skill.cmd_name}: …]
                  </code>

                  {/* Description */}
                  <p className="text-[0.78rem] leading-relaxed text-white/45">
                    {skill.description.en}
                  </p>
                  <p className="text-[0.72rem] leading-relaxed text-white/28">
                    {skill.description.ru}
                  </p>

                  {/* Example */}
                  {skill.example && (
                    <div className="border-t border-white/8 pt-3">
                      <p className="mb-1 text-[0.55rem] tracking-[0.2em] uppercase text-white/20">
                        example
                      </p>
                      <code className="block text-[0.65rem] leading-relaxed text-white/30 font-mono break-all">
                        {skill.example}
                      </code>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* ── How it works ── */}
          <div>
            <p className="mb-6 text-[0.6rem] tracking-[0.28em] uppercase text-white/20">
              how it works · как это работает
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {HOW_IT_WORKS.map((item) => (
                <div
                  key={item.step}
                  className="border border-white/8 p-5 flex flex-col gap-3"
                >
                  <span className="text-[1.6rem] font-extralight tracking-[0.1em] text-white/12 leading-none">
                    {item.step}
                  </span>
                  <p className="text-[0.72rem] leading-relaxed text-white/45">
                    {item.text}
                  </p>
                  <p className="text-[0.65rem] leading-relaxed text-white/25 border-t border-white/6 pt-3">
                    {item.textRu}
                  </p>
                </div>
              ))}
            </div>
          </div>

          {/* ── Context flow note ── */}
          <div className="border border-white/8 p-6 flex flex-col gap-4">
            <p className="text-[0.6rem] tracking-[0.24em] uppercase text-white/20">
              agentic pipeline flow
            </p>
            <div className="flex items-center gap-0 flex-wrap">
              {[
                { label: "user message", sub: "query text" },
                { label: "→" },
                { label: "chroma + pgvector", sub: "context retrieval" },
                { label: "→" },
                { label: "ai streams reply", sub: "with skills" },
                { label: "→" },
                { label: "[SEARCH_DIALOGUE]", sub: "raw history" },
                { label: "[WEB_SEARCH]", sub: "live internet" },
                { label: "[GENERATE_IMAGE]", sub: "gpt5 / gemini" },
                { label: "→" },
                { label: "results injected", sub: "continuation" },
                { label: "→" },
                { label: "[SAVE_MEMORY]", sub: "key facts" },
                { label: "→" },
                { label: "chromadb", sub: "long-term store" },
              ].map((node, i) =>
                node.sub === undefined ? (
                  <span key={i} className="text-[0.7rem] text-white/20 px-1">{node.label}</span>
                ) : (
                  <div key={i} className="border border-white/10 px-3 py-2 flex flex-col gap-0.5 my-1">
                    <span className="text-[0.65rem] tracking-[0.12em] uppercase text-white/45">{node.label}</span>
                    <span className="text-[0.55rem] tracking-[0.1em] text-white/22">{node.sub}</span>
                  </div>
                )
              )}
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}
