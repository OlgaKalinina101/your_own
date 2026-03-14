"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { apiFetch } from "@/lib/api";
const ACCOUNT_ID = "default";

// ── Types ─────────────────────────────────────────────────────────────────────

interface Fact {
  id: string;
  text: string;
  category: string;
  impressive: number;
  frequency: number;
  created_at?: string | null;
  last_used?: string | null;
}

type SortKey = "created_at" | "impressive" | "frequency";

// ── Helpers ───────────────────────────────────────────────────────────────────

function Stars({ value, onChange }: { value: number; onChange?: (v: number) => void }) {
  return (
    <span className="flex gap-0.5">
      {[1, 2, 3, 4].map((n) => (
        <button
          key={n}
          onClick={onChange ? () => onChange(n) : undefined}
          className={`text-[0.75rem] transition-colors duration-150 ${
            n <= value
              ? "text-white/80"
              : "text-white/15"
          } ${onChange ? "hover:text-white/60 cursor-pointer" : "cursor-default"}`}
          title={onChange ? `Rate ${n}` : undefined}
        >
          ★
        </button>
      ))}
    </span>
  );
}

function timeAgo(iso?: string | null): string {
  if (!iso) return "";
  try {
    const date = new Date(iso);
    const diff = Date.now() - date.getTime();
    const days = Math.floor(diff / 86400000);
    if (days === 0) return "today";
    if (days === 1) return "yesterday";
    if (days < 7) return `${days}d ago`;
    if (days < 30) return `${Math.floor(days / 7)}w ago`;
    if (days < 365) return `${Math.floor(days / 30)}mo ago`;
    return `${Math.floor(days / 365)}y ago`;
  } catch {
    return "";
  }
}

const CATEGORY_COLORS: Record<string, string> = {
  "Работа": "border-blue-500/25 text-blue-400/70",
  "Work":   "border-blue-500/25 text-blue-400/70",
  "Семья": "border-rose-500/25 text-rose-400/70",
  "Family": "border-rose-500/25 text-rose-400/70",
  "Здоровье": "border-green-500/25 text-green-400/70",
  "Health":   "border-green-500/25 text-green-400/70",
  "Отношения": "border-pink-500/25 text-pink-400/70",
  "Relationship": "border-pink-500/25 text-pink-400/70",
  "Хобби": "border-purple-500/25 text-purple-400/70",
  "Hobby": "border-purple-500/25 text-purple-400/70",
  "Быт":  "border-amber-500/25 text-amber-400/70",
  "Home": "border-amber-500/25 text-amber-400/70",
  "Учёба": "border-cyan-500/25 text-cyan-400/70",
  "Study": "border-cyan-500/25 text-cyan-400/70",
  "Финансы": "border-yellow-500/25 text-yellow-400/70",
  "Finance": "border-yellow-500/25 text-yellow-400/70",
  "Путешествия": "border-teal-500/25 text-teal-400/70",
  "Travel": "border-teal-500/25 text-teal-400/70",
};

function categoryColor(cat: string): string {
  return CATEGORY_COLORS[cat] ?? "border-white/15 text-white/45";
}

// ── FactCard component ────────────────────────────────────────────────────────

function FactCard({
  fact,
  onDelete,
  onUpdate,
}: {
  fact: Fact;
  onDelete: (id: string) => void;
  onUpdate: (id: string, patch: Partial<Fact>) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(fact.text);
  const [editCategory, setEditCategory] = useState(fact.category);
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await apiFetch(
        `/api/chroma/facts/${fact.id}?account_id=${ACCOUNT_ID}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: editText, category: editCategory }),
        }
      );
      if (res.ok) {
        const updated = await res.json() as Fact;
        onUpdate(fact.id, updated);
        setEditing(false);
      }
    } finally {
      setSaving(false);
    }
  };

  const handleRating = async (value: number) => {
    const res = await apiFetch(
      `/api/chroma/facts/${fact.id}?account_id=${ACCOUNT_ID}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ impressive: value }),
      }
    );
    if (res.ok) {
      onUpdate(fact.id, { impressive: value });
    }
  };

  const handleDelete = async () => {
    if (!confirm("Delete this memory?")) return;
    const res = await apiFetch(
      `/api/chroma/facts/${fact.id}?account_id=${ACCOUNT_ID}`,
      { method: "DELETE" }
    );
    if (res.ok || res.status === 204) {
      onDelete(fact.id);
    }
  };

  const colClass = categoryColor(fact.category);

  return (
    <div className={`border ${colClass.split(" ")[0]} bg-white/[0.02] p-4 flex flex-col gap-3 transition-all duration-200`}>
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`text-[0.6rem] tracking-[0.2em] uppercase ${colClass.split(" ")[1]}`}>
            {fact.category || "—"}
          </span>
          {fact.impressive >= 4 && (
            <span className="text-[0.6rem] tracking-[0.12em] uppercase text-white/40">critical</span>
          )}
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <Stars value={fact.impressive} onChange={handleRating} />
          <button
            onClick={() => { setEditing(!editing); setEditText(fact.text); setEditCategory(fact.category); }}
            className="text-[0.6rem] tracking-[0.18em] uppercase text-white/25 transition-colors hover:text-white/65"
          >
            {editing ? "cancel" : "edit"}
          </button>
          <button
            onClick={handleDelete}
            className="text-[0.6rem] tracking-[0.18em] uppercase text-red-400/30 transition-colors hover:text-red-400/80"
          >
            del
          </button>
        </div>
      </div>

      {/* Body */}
      {editing ? (
        <div className="flex flex-col gap-2">
          <textarea
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            rows={3}
            className="w-full resize-none bg-white/[0.04] border border-white/15 p-2 text-[0.82rem] leading-relaxed text-white/80 outline-none focus:border-white/35"
          />
          <input
            value={editCategory}
            onChange={(e) => setEditCategory(e.target.value)}
            placeholder="Category"
            className="w-full bg-transparent border-b border-white/15 py-1 text-[0.75rem] text-white/60 outline-none placeholder:text-white/25 focus:border-white/35"
          />
          <button
            onClick={handleSave}
            disabled={saving}
            className="self-end text-[0.65rem] tracking-[0.2em] uppercase text-white/50 hover:text-white/90 disabled:opacity-30"
          >
            {saving ? "saving…" : "save"}
          </button>
        </div>
      ) : (
        <p className="text-[0.84rem] leading-relaxed text-white/72">{fact.text}</p>
      )}

      {/* Footer meta */}
      <div className="flex items-center gap-4 text-[0.6rem] tracking-[0.12em] text-white/20">
        {fact.created_at && (
          <span title={fact.created_at}>saved {timeAgo(fact.created_at)}</span>
        )}
        {fact.last_used && (
          <span title={fact.last_used}>used {timeAgo(fact.last_used)}</span>
        )}
        {fact.frequency > 0 && (
          <span>recalled {fact.frequency}×</span>
        )}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function FactsPage() {
  const router = useRouter();
  const [facts, setFacts] = useState<Fact[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [filterCategory, setFilterCategory] = useState<string>("");
  const [sort, setSort] = useState<SortKey>("created_at");
  const [loading, setLoading] = useState(true);
  const [expandedCats, setExpandedCats] = useState<Set<string>>(new Set());

  const loadFacts = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        account_id: ACCOUNT_ID,
        sort,
        ...(filterCategory ? { category: filterCategory } : {}),
      });
      const [factsRes, catsRes] = await Promise.all([
        apiFetch(`/api/chroma/facts?${params}`),
        apiFetch(`/api/chroma/categories?account_id=${ACCOUNT_ID}`),
      ]);
      if (factsRes.ok) setFacts(await factsRes.json());
      if (catsRes.ok) {
        const data = await catsRes.json() as { categories: string[] };
        setCategories(data.categories);
        setExpandedCats(new Set(data.categories));
      }
    } finally {
      setLoading(false);
    }
  }, [filterCategory, sort]);

  useEffect(() => { void loadFacts(); }, [loadFacts]);

  const handleDelete = (id: string) => setFacts((prev) => prev.filter((f) => f.id !== id));

  const handleUpdate = (id: string, patch: Partial<Fact>) => {
    setFacts((prev) => prev.map((f) => (f.id === id ? { ...f, ...patch } : f)));
  };

  const toggleCat = (cat: string) => {
    setExpandedCats((prev) => {
      const next = new Set(prev);
      next.has(cat) ? next.delete(cat) : next.add(cat);
      return next;
    });
  };

  // Group facts by category
  const grouped = facts.reduce<Record<string, Fact[]>>((acc, fact) => {
    const cat = fact.category || "Other";
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(fact);
    return acc;
  }, {});
  const sortedCats = Object.keys(grouped).sort();

  return (
    <div className="flex h-screen w-screen flex-col bg-black text-white">

      {/* ── Header ── */}
      <header className="shrink-0 flex items-center justify-between border-b border-white/8 px-10 py-5">
        <div className="flex flex-col gap-1">
          <h1 className="text-[1.1rem] font-extralight tracking-[0.28em] uppercase text-white/80">
            Saved Facts
          </h1>
          <p className="text-[0.62rem] tracking-[0.18em] uppercase text-white/25">
            {facts.length} memories · long-term chroma store
          </p>
        </div>
        <div className="flex items-center gap-6">
          <button
            onClick={() => router.push("/dashboard/memory")}
            className="text-[0.65rem] tracking-[0.2em] uppercase text-white/30 transition-colors hover:text-white/75"
          >
            ← memory
          </button>
        </div>
      </header>

      {/* ── Controls ── */}
      <div className="shrink-0 flex items-center gap-4 px-10 py-4 border-b border-white/6">
        {/* Category filter */}
        <div className="flex items-center gap-2">
          <span className="text-[0.58rem] tracking-[0.2em] uppercase text-white/20">filter</span>
          <button
            onClick={() => setFilterCategory("")}
            className={`border px-2.5 py-1 text-[0.58rem] tracking-[0.16em] uppercase transition-colors duration-150 ${
              !filterCategory
                ? "border-white/40 text-white/70"
                : "border-white/10 text-white/25 hover:border-white/25 hover:text-white/50"
            }`}
          >
            all
          </button>
          {categories.map((cat) => (
            <button
              key={cat}
              onClick={() => setFilterCategory(cat === filterCategory ? "" : cat)}
              className={`border px-2.5 py-1 text-[0.58rem] tracking-[0.16em] uppercase transition-colors duration-150 ${
                filterCategory === cat
                  ? "border-white/40 text-white/70"
                  : "border-white/10 text-white/25 hover:border-white/25 hover:text-white/50"
              }`}
            >
              {cat}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <span className="text-[0.58rem] tracking-[0.2em] uppercase text-white/20">sort</span>
          {(["created_at", "impressive", "frequency"] as SortKey[]).map((s) => (
            <button
              key={s}
              onClick={() => setSort(s)}
              className={`border px-2.5 py-1 text-[0.58rem] tracking-[0.16em] uppercase transition-colors duration-150 ${
                sort === s
                  ? "border-white/40 text-white/70"
                  : "border-white/10 text-white/25 hover:border-white/25 hover:text-white/50"
              }`}
            >
              {s === "created_at" ? "recent" : s}
            </button>
          ))}
        </div>
      </div>

      {/* ── Body ── */}
      <div className="flex-1 overflow-y-auto px-10 py-8">
        {loading && (
          <p className="text-center text-[0.68rem] tracking-[0.18em] uppercase text-white/30 mt-20">
            loading…
          </p>
        )}

        {!loading && facts.length === 0 && (
          <div className="flex flex-col items-center justify-center h-64 gap-4">
            <p className="text-[0.75rem] tracking-[0.16em] uppercase text-white/25">
              no facts saved yet
            </p>
            <p className="text-[0.65rem] tracking-[0.12em] text-white/15 text-center max-w-xs leading-relaxed">
              The AI will save facts automatically when it uses the{" "}
              <span className="text-white/30">[SAVE_MEMORY]</span> skill during chat.
            </p>
          </div>
        )}

        {!loading && facts.length > 0 && (
          <div className="mx-auto max-w-3xl flex flex-col gap-8">
            {sortedCats.map((cat) => {
              const catFacts = grouped[cat];
              const isOpen = expandedCats.has(cat);
              const colClass = categoryColor(cat);

              return (
                <div key={cat}>
                  {/* Category header */}
                  <button
                    onClick={() => toggleCat(cat)}
                    className="w-full flex items-center justify-between mb-3 group"
                  >
                    <div className="flex items-center gap-3">
                      <span className={`text-[0.65rem] tracking-[0.22em] uppercase ${colClass.split(" ")[1]}`}>
                        {cat}
                      </span>
                      <span className="text-[0.58rem] tracking-[0.14em] text-white/20">
                        {catFacts.length} {catFacts.length === 1 ? "fact" : "facts"}
                      </span>
                    </div>
                    <span className="text-[0.6rem] text-white/20 group-hover:text-white/50 transition-colors">
                      {isOpen ? "▲" : "▼"}
                    </span>
                  </button>

                  {/* Category divider */}
                  <div className={`h-px mb-4 ${colClass.split(" ")[0]} opacity-30`}
                    style={{ background: "currentColor" }}
                  />

                  {/* Facts grid */}
                  {isOpen && (
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      {catFacts.map((fact) => (
                        <FactCard
                          key={fact.id}
                          fact={fact}
                          onDelete={handleDelete}
                          onUpdate={handleUpdate}
                        />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
