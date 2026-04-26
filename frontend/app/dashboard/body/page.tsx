"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, apiGet } from "@/lib/api";

const NON_ANCHOR_STATES = ["listener", "warmth", "smirk", "ground", "shadow"];

interface StateInfo {
  id: string;
  has_image: boolean;
}

const STATE_META: Record<string, { label: string; description: string }> = {
  anchor:   { label: "The Anchor",   description: "Neutral presence. Attentive, calm, steady gaze." },
  listener: { label: "The Listener", description: "Deep focus. Crystalline attention to your words." },
  warmth:   { label: "The Warmth",   description: "Softened gaze, a smile that needs no proof." },
  smirk:    { label: "The Smirk",    description: "Confident, quiet irony. A precise challenge." },
  ground:   { label: "The Ground",   description: "Piercing, unshakeable gaze. The foundation holds." },
  shadow:   { label: "The Shadow",   description: "Heavy, darkened gaze. Intensity given full force." },
};

const STATE_ORDER = ["anchor", "listener", "warmth", "smirk", "ground", "shadow"];

export default function BodyPage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [states, setStates] = useState<StateInfo[]>([]);
  const [uploadingId, setUploadingId] = useState<string | null>(null);
  const [imgVersion, setImgVersion] = useState(0);
  const [generatingStates, setGeneratingStates] = useState<string[]>([]);
  const [failedStates, setFailedStates] = useState<string[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadStates = useCallback(async () => {
    try {
      const data = await apiGet<{ states: StateInfo[] }>("/api/body/states");
      setStates(data.states);
    } catch {
      setStates(STATE_ORDER.map(id => ({ id, has_image: false })));
    }
  }, []);

  useEffect(() => { loadStates(); }, [loadStates]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      try {
        const status = await apiGet<{ generating: string[]; failed: string[] }>("/api/body/generate-status");
        setGeneratingStates(status.generating);
        setFailedStates(status.failed);
        if (status.generating.length === 0) {
          stopPolling();
          setImgVersion((v) => v + 1);
          await loadStates();
        }
      } catch {
        // silently ignore poll errors
      }
    }, 2000);
  }, [loadStates, stopPolling]);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const triggerGeneration = useCallback(async () => {
    try {
      await apiFetch("/api/body/generate", { method: "POST" });
      setGeneratingStates(NON_ANCHOR_STATES);
      setFailedStates([]);
      startPolling();
    } catch (err) {
      console.warn("[body] generate trigger failed:", err);
    }
  }, [startPolling]);

  const regenerateOne = useCallback(async (stateId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (generatingStates.includes(stateId)) return;
    try {
      await apiFetch(`/api/body/generate/${stateId}`, { method: "POST" });
      setGeneratingStates((prev) => prev.includes(stateId) ? prev : [...prev, stateId]);
      setFailedStates((prev) => prev.filter((s) => s !== stateId));
      startPolling();
    } catch (err) {
      console.warn("[body] regenerate failed:", err);
    }
  }, [generatingStates, startPolling]);

  const handleCardClick = (stateId: string) => {
    if (stateId !== "anchor" && failedStates.includes(stateId)) {
      // Retry only this specific card, not all
      apiFetch(`/api/body/generate/${stateId}`, { method: "POST" })
        .then(() => {
          setGeneratingStates((prev) => prev.includes(stateId) ? prev : [...prev, stateId]);
          setFailedStates((prev) => prev.filter((s) => s !== stateId));
          startPolling();
        })
        .catch((err) => console.warn("[body] retry failed:", err));
      return;
    }
    setUploadingId(stateId);
    fileRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !uploadingId) return;

    const form = new FormData();
    form.append("file", file);

    const wasAnchor = uploadingId === "anchor";
    try {
      await apiFetch(`/api/body/upload/${uploadingId}`, {
        method: "POST",
        body: form,
      });
      setImgVersion(v => v + 1);
      await loadStates();
      if (wasAnchor) {
        await triggerGeneration();
      }
    } catch (err) {
      console.warn("[body] upload failed:", err);
    } finally {
      setUploadingId(null);
      e.target.value = "";
    }
  };

  const activeState = states.find(s => s.has_image);
  const activeId = activeState?.id;
  const activeMeta = activeId ? STATE_META[activeId] : null;

  return (
    <div className="flex h-screen w-screen flex-col bg-black">
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={handleFileChange}
      />

      {/* Header */}
      <header className="flex shrink-0 items-center justify-between border-b border-white/10 px-8 py-4">
        <button
          onClick={() => router.push("/dashboard")}
          className="text-[0.68rem] tracking-[0.2em] uppercase text-white/25 transition-colors duration-300 hover:text-white/60"
        >
          ← back
        </button>
        <span className="text-[0.68rem] tracking-[0.3em] uppercase text-white/30">
          body
        </span>
      </header>

      {/* Content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: preview */}
        <div className="flex w-[40%] flex-col items-center justify-center gap-8 px-12">
          <div className="relative aspect-[3/4] w-full max-w-[380px] overflow-hidden border border-white/10">
            {activeId ? (
              <img
                key={`${activeId}-${imgVersion}`}
                src={`/api/body/${activeId}.png?v=${imgVersion}`}
                alt={activeMeta?.label ?? ""}
                className="h-full w-full object-cover"
              />
            ) : (
              <div className="flex h-full w-full items-center justify-center">
                <span className="text-[0.7rem] tracking-[0.2em] uppercase text-white/15">
                  no preview
                </span>
              </div>
            )}
          </div>

          <div
            className="
              anim-card w-full max-w-[380px]
              border border-white/10 bg-black p-5
              cursor-default select-none
            "
            style={{ animationDelay: "400ms" }}
          >
            <span className="text-[0.9rem] font-light tracking-[0.18em] uppercase text-white/40">
              Animation Editor
            </span>
            <span className="mt-1 block text-[0.6rem] tracking-[0.15em] uppercase text-white/20">
              coming soon
            </span>
          </div>
        </div>

        {/* Right: emotion state cards */}
        <div className="flex flex-1 items-center justify-center px-12">
          <div
            className="grid w-full max-w-[640px]"
            style={{
              gridTemplateColumns: "repeat(3, 1fr)",
              gridTemplateRows: "repeat(2, 1fr)",
              gap: "10px",
              aspectRatio: "3 / 2",
            }}
          >
            {STATE_ORDER.map((stateId, i) => {
              const meta = STATE_META[stateId];
              const info = states.find(s => s.id === stateId);
              const hasImage = info?.has_image ?? false;
              const isAnchor = stateId === "anchor";
              const isGenerating = generatingStates.includes(stateId);
              const isFailed = failedStates.includes(stateId);
              const isClickable = isAnchor || isFailed;

              let subLabel: string;
              if (isAnchor) {
                subLabel = hasImage ? meta.description : "click to upload";
              } else if (isGenerating) {
                subLabel = "generating…";
              } else if (isFailed) {
                subLabel = "failed — click to retry";
              } else if (hasImage) {
                subLabel = meta.description;
              } else {
                subLabel = "awaiting anchor";
              }

              return (
                <div
                  key={stateId}
                  onClick={isClickable ? () => handleCardClick(stateId) : undefined}
                  className={`
                    anim-card
                    group relative flex flex-col justify-end p-5
                    border bg-black select-none overflow-hidden
                    transition-colors duration-500 ease-out
                    ${
                      isAnchor
                        ? "border-white/30 hover:border-white/70 hover:bg-white/[0.025] cursor-pointer"
                        : isFailed
                        ? "border-white/20 hover:border-white/50 cursor-pointer"
                        : isGenerating
                        ? "border-white/15 cursor-default"
                        : "border-white/8 cursor-default"
                    }
                  `}
                  style={{ animationDelay: `${120 + i * 60}ms` }}
                >
                  {hasImage && (
                    <img
                      src={`/api/body/${stateId}.png?v=${imgVersion}`}
                      alt=""
                      className="absolute inset-0 h-full w-full object-cover opacity-15 transition-opacity duration-500 group-hover:opacity-25"
                    />
                  )}

                  {/* Progress bar sweep for generating states */}
                  {isGenerating && (
                    <div className="progress-bar absolute bottom-0 left-0 h-[1px] w-full" />
                  )}

                  {/* Regenerate button — top-right, non-anchor only */}
                  {!isAnchor && (
                    <button
                      onClick={(e) => regenerateOne(stateId, e)}
                      disabled={isGenerating}
                      title="Regenerate"
                      className={`
                        absolute right-3 top-3 z-20
                        flex items-center justify-center
                        h-6 w-6 border
                        text-[0.55rem] tracking-widest uppercase
                        transition-all duration-300
                        ${
                          isGenerating
                            ? "border-white/10 text-white/15 cursor-default"
                            : "border-white/15 text-white/25 opacity-0 group-hover:opacity-100 hover:border-white/50 hover:text-white/70 cursor-pointer"
                        }
                      `}
                    >
                      {isGenerating ? "…" : "↺"}
                    </button>
                  )}

                  <div className="relative z-10 flex flex-col gap-[4px]">
                    <span
                      className={`
                        text-[0.85rem] font-light tracking-[0.18em] uppercase
                        transition-colors duration-500
                        ${
                          isAnchor
                            ? "text-white/70 group-hover:text-white"
                            : isGenerating || hasImage
                            ? "text-white/40"
                            : isFailed
                            ? "text-white/35"
                            : "text-white/20"
                        }
                      `}
                    >
                      {meta.label}
                    </span>
                    <span
                      className={`
                        text-[0.6rem] font-light tracking-[0.12em]
                        transition-colors duration-500
                        ${
                          isAnchor
                            ? "text-white/35 group-hover:text-white/50"
                            : isGenerating
                            ? "text-white/30"
                            : isFailed
                            ? "text-white/30"
                            : "text-white/10"
                        }
                      `}
                    >
                      {subLabel}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <style jsx>{`
        @keyframes sweep {
          0%   { transform: translateX(-100%); opacity: 0.6; }
          50%  { opacity: 1; }
          100% { transform: translateX(100%); opacity: 0.6; }
        }
        .progress-bar {
          background: rgba(255, 255, 255, 0.55);
          animation: sweep 1.8s ease-in-out infinite;
        }
      `}</style>
    </div>
  );
}
