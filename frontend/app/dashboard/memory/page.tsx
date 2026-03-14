"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch } from "@/lib/api";

type ImportState =
  | { phase: "idle" }
  | { phase: "uploading" }
  | { phase: "parsing"; done: number; total: number }
  | { phase: "done"; total: number }
  | { phase: "error"; message: string };

type MemoryStats = {
  pair_count?: number | null;
  sentence_count?: number | null;
  message_count?: number | null;
};

type ImportListener = (state: ImportState) => void;

let sharedImportState: ImportState = { phase: "idle" };
let sharedImportTask: Promise<number> | null = null;
const importListeners = new Set<ImportListener>();

function setSharedImportState(next: ImportState) {
  sharedImportState = next;
  for (const listener of importListeners) {
    listener(next);
  }
}

function subscribeImportState(listener: ImportListener) {
  importListeners.add(listener);
  listener(sharedImportState);
  return () => {
    importListeners.delete(listener);
  };
}

async function startImport(file: File): Promise<number> {
  if (sharedImportTask) {
    return sharedImportTask;
  }

  sharedImportTask = (async () => {
    setSharedImportState({ phase: "uploading" });

    const form = new FormData();
    form.append("file", file);
    form.append("account_id", "default");

    let done = 0;
    let total = 0;

    try {
      const res = await apiFetch(`/api/memory/import`, {
        method: "POST",
        body: form,
      });

      if (!res.ok || !res.body) {
        throw new Error(`Server error ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      setSharedImportState({ phase: "parsing", done: 0, total: 0 });

      while (true) {
        const { value, done: streamDone } = await reader.read();
        if (streamDone) {
          break;
        }

        buf += decoder.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";

        for (const part of parts) {
          const line = part.startsWith("data: ") ? part.slice(6) : part;
          if (!line.trim()) {
            continue;
          }
          try {
            const payload = JSON.parse(line);
            if (payload.error) {
              throw new Error(payload.error);
            }
            done = payload.done ?? done;
            total = payload.total ?? total;

            if (payload.finished) {
              setSharedImportState({ phase: "done", total: done });
              return done;
            }
            setSharedImportState({ phase: "parsing", done, total });
          } catch (err) {
            if (err instanceof Error && err.message !== "Unexpected end of JSON input") {
              throw err;
            }
          }
        }
      }

      setSharedImportState({ phase: "done", total: done });
      return done;
    } catch (err) {
      setSharedImportState({
        phase: "error",
        message: err instanceof Error ? err.message : String(err),
      });
      throw err;
    } finally {
      sharedImportTask = null;
    }
  })();

  return sharedImportTask;
}

export default function MemoryPage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<ImportState>(sharedImportState);
  const [stats, setStats] = useState<MemoryStats | null>(null);

  // Load existing message count on mount
  useEffect(() => {
    apiFetch(`/api/memory/stats?account_id=default`)
      .then((r) => r.json())
      .then((d) => setStats(d))
      .catch(() => {});
  }, []);

  useEffect(() => subscribeImportState(setState), []);

  useEffect(() => {
    if (state.phase === "done") {
      setStats((prev) => ({
        ...prev,
        pair_count: state.total,
      }));
    }
  }, [state]);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      const importedTotal = await startImport(file);
      setStats((prev) => ({
        ...prev,
        pair_count: importedTotal,
      }));
    } catch (err) {
      setState({
        phase: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      // reset file input so the same file can be re-uploaded
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const displayedPairCount = stats?.pair_count ?? stats?.message_count ?? null;
  const displayedChunkCount = stats?.sentence_count ?? null;

  const progress =
    state.phase === "parsing" && state.total > 0
      ? Math.round((state.done / state.total) * 100)
      : 0;

  return (
    <div className="flex h-screen w-screen flex-col bg-black px-14 py-12 text-white">
      {/* Header */}
      <div className="mb-12 flex items-start justify-between">
        <div className="flex flex-col gap-[6px]">
          <h1 className="text-[1.6rem] font-extralight tracking-[0.22em] uppercase text-white/85">
            Memory
          </h1>
          {displayedPairCount !== null && (
            <p className="text-[0.72rem] tracking-[0.18em] text-white/30 uppercase">
              {displayedPairCount.toLocaleString()} imported pairs
              {displayedChunkCount !== null ? ` · ${displayedChunkCount.toLocaleString()} stored chunks` : ""}
            </p>
          )}
        </div>
        <div className="flex items-center gap-6">
          <button
            onClick={() => router.push("/dashboard/memory/facts")}
            className="text-[0.65rem] tracking-[0.2em] uppercase text-white/30 transition-colors duration-300 hover:text-white/75"
          >
            saved facts →
          </button>
          <button
            onClick={() => router.push("/dashboard")}
            className="text-[0.68rem] tracking-[0.2em] uppercase text-white/35 transition-colors duration-300 hover:text-white/80"
          >
            ← dashboard
          </button>
        </div>
      </div>

      {/* Main content */}
      <div className="flex flex-1 flex-col items-center justify-center gap-10">

        {/* Upload zone */}
        <div className="flex flex-col items-center gap-6 w-full max-w-md">
          <div className="w-full border border-white/15 p-8 flex flex-col items-center gap-5 transition-colors duration-500 hover:border-white/40">
            <p className="text-[0.75rem] tracking-[0.16em] uppercase text-white/40 text-center leading-relaxed">
              Export your ChatGPT history from{" "}
              <span className="text-white/60">Settings → Data controls → Export data</span>
              {" "}then upload{" "}
              <span className="text-white/60">conversations.json</span>
            </p>

            <button
              onClick={() => fileRef.current?.click()}
              disabled={state.phase === "uploading" || state.phase === "parsing"}
              className="
                mt-2 px-8 py-3
                border border-white/25
                text-[0.72rem] tracking-[0.22em] uppercase text-white/60
                transition-all duration-400
                hover:border-white/70 hover:text-white/90
                disabled:opacity-30 disabled:cursor-not-allowed
              "
            >
              {state.phase === "uploading" || state.phase === "parsing"
                ? "importing…"
                : "import from chatgpt"}
            </button>

            <input
              ref={fileRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={handleFileChange}
            />
          </div>

          {/* Progress bar */}
          {(state.phase === "uploading" || state.phase === "parsing") && (
            <div className="w-full flex flex-col gap-2">
              <div className="w-full h-px bg-white/10 relative overflow-hidden">
                <div
                  className="absolute left-0 top-0 h-full bg-white/60 transition-all duration-300"
                  style={{
                    width: state.phase === "uploading" ? "5%" : `${progress}%`,
                  }}
                />
              </div>
              <p className="text-[0.65rem] tracking-[0.18em] uppercase text-white/30">
                {state.phase === "uploading"
                  ? "uploading…"
                  : `${state.done.toLocaleString()} / ${state.total.toLocaleString()} pairs`}
              </p>
            </div>
          )}

          {/* Done state */}
          {state.phase === "done" && (
            <p className="text-[0.72rem] tracking-[0.18em] uppercase text-white/50">
              ✓ {state.total.toLocaleString()} pairs imported
            </p>
          )}

          {/* Error state */}
          {state.phase === "error" && (
            <p className="text-[0.72rem] tracking-[0.18em] uppercase text-red-400/70">
              {state.message}
            </p>
          )}
        </div>

        {/* Info strip */}
        <div className="w-full max-w-md border-t border-white/8 pt-6 flex flex-col gap-3">
          <p className="text-[0.65rem] tracking-[0.16em] uppercase text-white/20 leading-relaxed">
            Imported chat pairs are split into semantic chunks for retrieval, so the chunk count will be much larger than the pair count.
          </p>
        </div>
      </div>
    </div>
  );
}
