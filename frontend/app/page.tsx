"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { getAuthToken, setAuthToken, apiFetch, getApiBase } from "@/lib/api";
const BACKEND_POLL_INTERVAL = 600;   // ms between backend availability checks
const BACKEND_TIMEOUT = 30_000;      // ms before giving up waiting for backend

const STARTUP_STEPS = [
  "Loading embedding model (all-MiniLM-L6-v2)",
  "Loading Russian lemmatiser (pymorphy3)",
  "Loading RuWordNet",
  "Loading English lemmatiser (NLTK WordNet)",
];

function isElectron(): boolean {
  return typeof window !== "undefined" && "yourOwn" in window;
}

type Phase =
  | "title"         // showing animated title + slogan
  | "waiting"       // polling backend until it responds
  | "loading"       // streaming startup/status progress
  | "done";         // redirecting

export default function LoadingScreen() {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("title");
  const [currentStep, setCurrentStep] = useState("");
  const [completedSteps, setCompletedSteps] = useState<string[]>([]);
  const [backendError, setBackendError] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // ── Phase 1: show title for 2.4s, then start polling backend ──────────────
  useEffect(() => {
    const t = setTimeout(() => setPhase("waiting"), 2400);
    return () => clearTimeout(t);
  }, []);

  // ── Phase 2: poll /api/startup/status until backend is up ─────────────────
  useEffect(() => {
    if (phase !== "waiting") return;

    let cancelled = false;
    const deadline = Date.now() + BACKEND_TIMEOUT;

    const poll = async () => {
      while (!cancelled && Date.now() < deadline) {
        try {
          const res = await fetch(`${getApiBase()}/api/startup/status`, {
            signal: AbortSignal.timeout(3000),
            headers: { "ngrok-skip-browser-warning": "true" },
          });
          if (res.ok) {
            if (!cancelled) setPhase("loading");
            return;
          }
        } catch {
          // backend not up yet — keep polling
        }
        await new Promise((r) => setTimeout(r, BACKEND_POLL_INTERVAL));
      }
      if (!cancelled) setBackendError(true);
    };

    poll();
    return () => { cancelled = true; };
  }, [phase]);

  // ── Phase 3: consume SSE startup progress ─────────────────────────────────
  useEffect(() => {
    if (phase !== "loading") return;

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const run = async () => {
      try {
        const res = await fetch(`${getApiBase()}/api/startup/status`, {
          signal: ctrl.signal,
          headers: { "ngrok-skip-browser-warning": "true" },
        });
        if (!res.body) return;

        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;

          buf += dec.decode(value, { stream: true });
          const parts = buf.split("\n\n");
          buf = parts.pop() ?? "";

          for (const part of parts) {
            const line = part.startsWith("data: ") ? part.slice(6) : part;
            if (!line.trim() || line.startsWith(":")) continue;
            try {
              const ev = JSON.parse(line);
              if (ev.status === "running") {
                setCurrentStep(ev.step);
              } else if (ev.status === "ok" && !ev.done) {
                setCurrentStep("");
                setCompletedSteps((prev) =>
                  prev.includes(ev.step) ? prev : [...prev, ev.step]
                );
              } else if (ev.done) {
                setPhase("done");
                return;
              }
            } catch {
              // malformed line — skip
            }
          }
        }

        setPhase("done");
      } catch (e: unknown) {
        if (e instanceof Error && e.name !== "AbortError") {
          setPhase("done"); // on error, proceed anyway
        }
      }
    };

    run();
    return () => ctrl.abort();
  }, [phase]);

  // ── Phase 4: auto-acquire auth token, then redirect ─────────────────────
  useEffect(() => {
    if (phase !== "done") return;

    const redirect = async () => {
      // Auto-acquire token from local backend if we don't have one yet
      if (!getAuthToken()) {
        try {
          const res = await fetch(`${getApiBase()}/api/settings/local-token`, {
            headers: { "ngrok-skip-browser-warning": "true" },
          });
          if (res.ok) {
            const data = await res.json();
            if (data.token) {
              setAuthToken(data.token);
            }
          }
        } catch {
          // backend unreachable — will go to settings
        }
      }

      let hasKey = false;
      const token = getAuthToken();
      if (token) {
        try {
          const res = await fetch(`${getApiBase()}/api/settings/raw`, {
            headers: {
              Authorization: `Bearer ${token}`,
              "ngrok-skip-browser-warning": "true",
            },
          });
          if (res.ok) {
            const data = await res.json();
            hasKey = !!data.openrouter_api_key;
          }
        } catch {
          // server unreachable
        }
      }
      router.push(hasKey ? "/chat" : "/dashboard/settings");
    };

    const t = setTimeout(redirect, 600);
    return () => clearTimeout(t);
  }, [phase, router]);

  // ── Progress bar width ─────────────────────────────────────────────────────
  const total = STARTUP_STEPS.length;
  const done = completedSteps.length;
  const progressPct =
    phase === "title" || phase === "waiting"
      ? 0
      : phase === "done"
      ? 100
      : Math.round((done / total) * 100);

  return (
    <div className="relative flex h-screen w-screen flex-col items-center justify-center bg-black overflow-hidden">

      {/* Title block */}
      <div className="flex flex-col items-center gap-6">
        <h1 className="anim-title text-[4.5rem] font-extralight tracking-[0.12em] text-white">
          Your Own
        </h1>
        <p className="anim-subtitle text-[1.25rem] font-light tracking-[0.06em] text-white/60">
          No corporation.&nbsp;&nbsp;No censorship.&nbsp;&nbsp;No limits.
        </p>
      </div>

      {/* Startup progress — appears after title animation */}
      <div
        className="absolute bottom-14 left-1/2 -translate-x-1/2 flex flex-col items-center gap-4 w-80"
        style={{
          opacity: phase === "title" ? 0 : 1,
          transition: "opacity 0.8s ease",
        }}
      >
        {/* Step label */}
        <p className="h-4 text-[0.62rem] tracking-[0.18em] uppercase text-white/35 text-center truncate w-full">
          {backendError
            ? "backend unavailable — check that the server is running"
            : phase === "waiting"
            ? "connecting…"
            : phase === "done"
            ? "ready"
            : currentStep || "initialising…"}
        </p>

        {/* Progress bar */}
        <div className="w-full h-px bg-white/10 relative overflow-hidden">
          <div
            className="absolute left-0 top-0 h-full bg-white/50"
            style={{
              width: `${progressPct}%`,
              transition: "width 0.5s cubic-bezier(0.16, 1, 0.3, 1)",
            }}
          />
        </div>

        {/* Step dots */}
        <div className="flex gap-3">
          {STARTUP_STEPS.map((s) => {
            const isDone = completedSteps.includes(s);
            const isActive = currentStep === s;
            return (
              <div
                key={s}
                className="w-1 h-1 rounded-full transition-all duration-500"
                style={{
                  background: isDone
                    ? "rgba(255,255,255,0.7)"
                    : isActive
                    ? "rgba(255,255,255,0.4)"
                    : "rgba(255,255,255,0.12)",
                }}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}
