"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, apiGet } from "@/lib/api";

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

  const loadStates = useCallback(async () => {
    try {
      const data = await apiGet<{ states: StateInfo[] }>("/api/body/states");
      setStates(data.states);
    } catch {
      setStates(STATE_ORDER.map(id => ({ id, has_image: false })));
    }
  }, []);

  useEffect(() => { loadStates(); }, [loadStates]);

  const handleCardClick = (stateId: string) => {
    setUploadingId(stateId);
    fileRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !uploadingId) return;

    const form = new FormData();
    form.append("file", file);

    try {
      await apiFetch(`/api/body/upload/${uploadingId}`, {
        method: "POST",
        body: form,
      });
      setImgVersion(v => v + 1);
      await loadStates();
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
              const isActive = stateId === "anchor";

              return (
                <div
                  key={stateId}
                  onClick={isActive ? () => handleCardClick(stateId) : undefined}
                  className={`
                    anim-card
                    group relative flex flex-col justify-end p-5
                    border bg-black select-none
                    transition-colors duration-500 ease-out
                    ${
                      isActive
                        ? "border-white/30 hover:border-white/70 hover:bg-white/[0.025] cursor-pointer"
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
                  <div className="relative z-10 flex flex-col gap-[4px]">
                    <span
                      className={`
                        text-[0.85rem] font-light tracking-[0.18em] uppercase
                        transition-colors duration-500
                        ${isActive ? "text-white/70 group-hover:text-white" : "text-white/20"}
                      `}
                    >
                      {meta.label}
                    </span>
                    <span
                      className={`
                        text-[0.6rem] font-light tracking-[0.12em]
                        transition-colors duration-500
                        ${isActive ? "text-white/35 group-hover:text-white/50" : "text-white/10"}
                      `}
                    >
                      {isActive ? (hasImage ? meta.description : "click to upload") : "coming soon"}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
