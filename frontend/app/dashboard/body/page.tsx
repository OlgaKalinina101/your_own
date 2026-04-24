"use client";

import { useRouter } from "next/navigation";

const STATES = [
  {
    id: "anchor",
    label: "The Anchor",
    description: "Neutral presence. Attentive, calm, steady gaze.",
    image: "/api/body/anchor.png",
    active: true,
  },
  {
    id: "listener",
    label: "The Listener",
    description: "Deep focus. Crystalline attention to your words.",
    active: false,
  },
  {
    id: "warmth",
    label: "The Warmth",
    description: "Softened gaze, a smile that needs no proof.",
    active: false,
  },
  {
    id: "smirk",
    label: "The Smirk",
    description: "Confident, quiet irony. A precise challenge.",
    active: false,
  },
  {
    id: "ground",
    label: "The Ground",
    description: "Piercing, unshakeable gaze. The foundation holds.",
    active: false,
  },
  {
    id: "shadow",
    label: "The Shadow",
    description: "Heavy, darkened gaze. Intensity given full force.",
    active: false,
  },
] as const;

export default function BodyPage() {
  const router = useRouter();
  const activeState = STATES.find((s) => s.active);

  return (
    <div className="flex h-screen w-screen flex-col bg-black">
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
            {activeState?.image ? (
              <img
                src={activeState.image}
                alt={activeState.label}
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
            {STATES.map((state, i) => (
              <div
                key={state.id}
                className={`
                  anim-card
                  group relative flex flex-col justify-end p-5
                  border bg-black select-none
                  transition-colors duration-500 ease-out
                  ${
                    state.active
                      ? "border-white/30 hover:border-white/70 hover:bg-white/[0.025] cursor-pointer"
                      : "border-white/8 cursor-default"
                  }
                `}
                style={{ animationDelay: `${120 + i * 60}ms` }}
              >
                {state.active && state.image && (
                  <img
                    src={state.image}
                    alt=""
                    className="absolute inset-0 h-full w-full object-cover opacity-15 transition-opacity duration-500 group-hover:opacity-25"
                  />
                )}
                <div className="relative z-10 flex flex-col gap-[4px]">
                  <span
                    className={`
                      text-[0.85rem] font-light tracking-[0.18em] uppercase
                      transition-colors duration-500
                      ${state.active ? "text-white/70 group-hover:text-white" : "text-white/20"}
                    `}
                  >
                    {state.label}
                  </span>
                  <span
                    className={`
                      text-[0.6rem] font-light tracking-[0.12em]
                      transition-colors duration-500
                      ${state.active ? "text-white/35 group-hover:text-white/50" : "text-white/10"}
                    `}
                  >
                    {state.active ? state.description : "coming soon"}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
