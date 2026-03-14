"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  getBackendUrl,
  setBackendUrl,
  getAuthToken,
  setAuthToken,
  apiGet,
  apiPut,
} from "@/lib/api";

const MODELS = [
  { id: "anthropic/claude-opus-4.6",  label: "Claude Opus 4.6",  vision: true  },
  { id: "z-ai/glm-5",                 label: "GLM 5",             vision: false },
  { id: "openai/gpt-5.1",             label: "GPT 5.1",           vision: true  },
  { id: "openai/gpt-5.4",             label: "GPT 5.4",           vision: true  },
  { id: "moonshotai/kimi-k2.5",       label: "Kimi K2.5",         vision: false },
  { id: "meta-llama/llama-4-maverick", label: "Llama 4 Maverick",  vision: false },
  { id: "meta-llama/llama-4-scout",    label: "Llama 4 Scout",     vision: false },
  { id: "google/gemini-3-pro-preview", label: "Gemini 3 Pro Preview", vision: false },
  { id: "qwen/qwen3-max",              label: "Qwen 3",            vision: false },
  { id: "deepseek/deepseek-v3.2",     label: "Deepseek V3.2",     vision: false },
  { id: "mistralai/mistral-large",    label: "Mistral Large",     vision: false },
] as const;

type ModelId = (typeof MODELS)[number]["id"];

function SliderRow({
  label,
  hint,
  value,
  onChange,
  min = 1,
  max = 10,
}: {
  label: string;
  hint: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between">
        <label className="text-[0.68rem] tracking-[0.22em] uppercase text-white/55">
          {label}
        </label>
        <span className="text-[0.88rem] font-light tabular-nums text-white/80">
          {value}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={1}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="slider w-full"
      />
      <p className="text-[0.62rem] tracking-wide text-white/40">{hint}</p>
    </div>
  );
}

export default function SettingsPage() {
  const router = useRouter();

  // ── Connection ─────────────────────────────────────────────
  const [serverUrl, setServerUrl]   = useState("");
  const [authToken, setAuthTokenSt] = useState("");
  const [connected, setConnected]   = useState<boolean | null>(null);

  // ── AI settings ────────────────────────────────────────────
  const [aiName, setAiName]             = useState("");
  const [apiKey, setApiKey]             = useState("");
  const [model, setModel]               = useState<ModelId>(MODELS[0].id);
  const [temperature, setTemperature]   = useState(7);
  const [topP, setTopP]                 = useState(9);
  const [historyPairs, setHistoryPairs]       = useState(6);
  const [memoryCutoffDays, setMemoryCutoffDays] = useState(2);

  // ── Pushy notifications ────────────────────────────────────
  const [pushyApiKey, setPushyApiKey]           = useState("");
  const [pushyDeviceToken, setPushyDeviceToken] = useState("");
  const [pushyMasked, setPushyMasked]           = useState(true);
  const [reflectionCooldown, setReflectionCooldown]   = useState(4);
  const [reflectionInterval, setReflectionInterval]   = useState(12);
  const [triggeringReflection, setTriggeringReflection] = useState(false);

  const [saved, setSaved]     = useState(false);
  const [masked, setMasked]   = useState(true);
  const [open, setOpen]       = useState(false);
  const dropdownRef           = useRef<HTMLDivElement>(null);

  // ── Load connection settings from localStorage ─────────────
  useEffect(() => {
    setServerUrl(getBackendUrl());
    setAuthTokenSt(getAuthToken());
  }, []);

  // ── Load AI settings from backend + migrate from keytar if needed ──
  useEffect(() => {
    loadRemoteSettings();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function loadRemoteSettings() {
    try {
      const data: Record<string, unknown> = await apiGet("/api/settings/raw");
      const key = data.openrouter_api_key as string;

      // One-time migration: if server has no API key but Electron keytar does, push it
      if (!key && typeof window !== "undefined" && "yourOwn" in window) {
        const keytarKey = await window.yourOwn.getApiKey();
        if (keytarKey) {
          const keytarModel = await window.yourOwn.getModel();
          const keytarTemp = await window.yourOwn.getTemperature();
          const keytarTopP = await window.yourOwn.getTopP();
          const keytarHistory = await window.yourOwn.getHistoryPairs();
          const keytarCutoff = await window.yourOwn.getMemoryCutoffDays();
          const keytarSoul = await window.yourOwn.getSoul();

          await apiPut("/api/settings", {
            openrouter_api_key: keytarKey,
            ...(keytarModel ? { model: keytarModel } : {}),
            ...(keytarTemp ? { temperature: Number(keytarTemp) / 10 } : {}),
            ...(keytarTopP ? { top_p: Number(keytarTopP) / 10 } : {}),
            ...(keytarHistory ? { history_pairs: Number(keytarHistory) } : {}),
            ...(keytarCutoff ? { memory_cutoff_days: Number(keytarCutoff) } : {}),
          });
          if (keytarSoul) {
            await apiPut("/api/settings/soul", { text: keytarSoul });
          }
          console.log("[settings] Migrated from keytar to server");
          return loadRemoteSettings();
        }
      }

      if (data.ai_name) setAiName(data.ai_name as string);
      if (key) setApiKey(key);
      const m = data.model as string;
      if (m && MODELS.find((x) => x.id === m)) setModel(m as ModelId);
      if (data.temperature != null) setTemperature(Math.round((data.temperature as number) * 10));
      if (data.top_p != null) setTopP(Math.round((data.top_p as number) * 10));
      if (data.history_pairs != null) setHistoryPairs(data.history_pairs as number);
      if (data.memory_cutoff_days != null) setMemoryCutoffDays(data.memory_cutoff_days as number);
      if (data.pushy_api_key) setPushyApiKey(data.pushy_api_key as string);
      if (data.pushy_device_token) setPushyDeviceToken(data.pushy_device_token as string);
      if (data.reflection_cooldown_hours != null) setReflectionCooldown(data.reflection_cooldown_hours as number);
      if (data.reflection_interval_hours != null) setReflectionInterval(data.reflection_interval_hours as number);
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }

  // ── Close dropdown on outside click ────────────────────────
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // ── Save connection settings ───────────────────────────────
  const handleSaveConnection = async () => {
    setBackendUrl(serverUrl);
    setAuthToken(authToken);
    await loadRemoteSettings();
  };

  // ── Save AI settings to backend ────────────────────────────
  const handleSave = async () => {
    try {
      await apiPut("/api/settings", {
        ai_name: aiName,
        openrouter_api_key: apiKey,
        model,
        temperature: temperature / 10,
        top_p: topP / 10,
        history_pairs: historyPairs,
        memory_cutoff_days: memoryCutoffDays,
        ...(pushyApiKey ? { pushy_api_key: pushyApiKey } : {}),
        ...(pushyDeviceToken ? { pushy_device_token: pushyDeviceToken } : {}),
        reflection_cooldown_hours: reflectionCooldown,
        reflection_interval_hours: reflectionInterval,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      console.error("Failed to save settings:", err);
    }
  };

  // ── Trigger reflection now ─────────────────────────────────
  const handleTriggerReflection = async () => {
    setTriggeringReflection(true);
    try {
      await apiPut("/api/settings/trigger-reflection", {});
    } catch {
      // endpoint may not exist yet, ignore
    } finally {
      setTimeout(() => setTriggeringReflection(false), 2000);
    }
  };

  const selectedLabel = MODELS.find((m) => m.id === model)?.label ?? "";

  return (
    <div className="flex h-screen w-screen flex-col bg-black text-white">
      <div className="sticky top-0 z-10 border-b border-white/8 bg-black/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-xl items-center justify-between px-8 py-6">
          <button
            onClick={() => router.push("/dashboard")}
            className="text-[0.72rem] tracking-[0.2em] uppercase text-white/50 transition-colors duration-300 hover:text-white/90"
          >
            ← back
          </button>
          <h2 className="text-[1.5rem] font-light tracking-[0.18em] uppercase text-white">
            Settings
          </h2>
          <div className="w-[72px]" />
        </div>
      </div>

      <div className="flex-1 overflow-auto py-12">
        <div className="mx-auto flex w-full max-w-xl flex-col gap-10 px-8">

        {/* ── Server Connection ── */}
        <div className="flex flex-col gap-5 border border-white/10 p-6">
          <div className="flex items-center justify-between">
            <label className="text-[0.68rem] tracking-[0.22em] uppercase text-white/55">
              Server Connection
            </label>
            {connected === true && (
              <span className="text-[0.6rem] tracking-[0.2em] uppercase text-emerald-400/70">
                connected
              </span>
            )}
            {connected === false && (
              <span className="text-[0.6rem] tracking-[0.2em] uppercase text-red-400/70">
                disconnected
              </span>
            )}
          </div>

          <div className="flex flex-col gap-2">
            <label className="text-[0.6rem] tracking-[0.18em] uppercase text-white/35">
              Server URL
            </label>
            <input
              type="text"
              value={serverUrl}
              onChange={(e) => setServerUrl(e.target.value)}
              placeholder="http://localhost:8000"
              spellCheck={false}
              className="border-b border-white/20 bg-transparent py-2 text-[0.9rem] font-light tracking-wide text-white placeholder:text-white/25 outline-none transition-colors focus:border-white/50"
            />
          </div>

          <div className="flex flex-col gap-2">
            <label className="text-[0.6rem] tracking-[0.18em] uppercase text-white/35">
              Auth Token
            </label>
            <input
              type="password"
              value={authToken}
              onChange={(e) => setAuthTokenSt(e.target.value)}
              placeholder="paste token from backend console"
              spellCheck={false}
              className="border-b border-white/20 bg-transparent py-2 text-[0.9rem] font-light tracking-wide text-white placeholder:text-white/25 outline-none transition-colors focus:border-white/50"
            />
          </div>

          <button
            onClick={handleSaveConnection}
            className="self-start border border-white/20 px-6 py-2 text-[0.65rem] tracking-[0.2em] uppercase text-white/50 transition-colors hover:border-white/50 hover:text-white/80"
          >
            connect
          </button>
        </div>

        {/* ── Divider ── */}
        <div className="border-t border-white/10" />

        {/* AI Name */}
        <div className="flex flex-col gap-3">
          <label className="text-[0.68rem] tracking-[0.22em] uppercase text-white/55">
            AI Name
          </label>
          <input
            type="text"
            value={aiName}
            onChange={(e) => setAiName(e.target.value)}
            placeholder="How your AI introduces itself"
            spellCheck={false}
            className="
              border-b border-white/30 bg-transparent
              py-3 text-[1rem] font-light tracking-wide text-white
              placeholder:text-white/30 outline-none
              transition-colors duration-300
              focus:border-white/70
            "
          />
        </div>

        {/* API Key */}
        <div className="flex flex-col gap-3">
          <label className="text-[0.68rem] tracking-[0.22em] uppercase text-white/55">
            OpenRouter API Key
          </label>
          <div className="flex items-center gap-3">
            <input
              type={masked ? "password" : "text"}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-or-..."
              spellCheck={false}
              className="
                flex-1 border-b border-white/30 bg-transparent
                py-3 text-[1rem] font-light tracking-wide text-white
                placeholder:text-white/30 outline-none
                transition-colors duration-300
                focus:border-white/70
              "
            />
            <button
              onClick={() => setMasked((v) => !v)}
              className="shrink-0 text-[0.68rem] tracking-[0.16em] uppercase text-white/40 transition-colors duration-300 hover:text-white/80"
            >
              {masked ? "show" : "hide"}
            </button>
          </div>
        </div>

        {/* Model */}
        <div className="flex flex-col gap-3" ref={dropdownRef}>
          <label className="text-[0.68rem] tracking-[0.22em] uppercase text-white/55">
            Model
          </label>
          <div className="relative">
            <button
              onClick={() => setOpen((v) => !v)}
              className="
                flex w-full items-center justify-between
                border-b border-white/30 py-3 text-left
                text-[1rem] font-light tracking-wide text-white
                transition-colors duration-300
                hover:border-white/60
              "
            >
              <span>{selectedLabel}</span>
              <span className="text-[0.65rem] tracking-widest text-white/45">
                {open ? "▲" : "▼"}
              </span>
            </button>
            {open && (
              <ul className="absolute left-0 right-0 top-full z-10 mt-1 border border-white/25 bg-black">
                {MODELS.map((m) => (
                  <li key={m.id}>
                    <button
                      onClick={() => { setModel(m.id); setOpen(false); }}
                      className={`
                        w-full flex items-center justify-between px-4 py-3 text-left
                        text-[0.92rem] font-light tracking-wide
                        transition-colors duration-200 hover:bg-white/[0.06]
                        ${model === m.id ? "text-white" : "text-white/60"}
                      `}
                    >
                      <span>{m.label}</span>
                      {m.vision && (
                        <span className="text-[0.6rem] tracking-widest uppercase text-white/40">
                          vision
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {/* Chat retrieval settings */}
        <div className="flex flex-col gap-3">
          <label className="text-[0.68rem] tracking-[0.22em] uppercase text-white/55">
            Chat Context
          </label>
          <p className="text-[0.62rem] tracking-wide text-white/35 -mt-1">
            Recent conversation and recalled memories included in each chat prompt.
          </p>
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2 mt-2">
            <SliderRow
              label="Message History Pairs"
              hint="How many recent user/assistant pairs to include as the active thread."
              value={historyPairs}
              onChange={setHistoryPairs}
              min={1}
              max={10}
            />
            <SliderRow
              label="Memory Cutoff Days"
              hint="Only recall memories older than this many days. Prevents recycling recent context."
              value={memoryCutoffDays}
              onChange={setMemoryCutoffDays}
              min={1}
              max={10}
            />
          </div>
        </div>

        {/* Divider */}
        <div className="border-t border-white/10" />

        {/* Temperature */}
        <SliderRow
          label="Temperature"
          hint="Controls randomness. Low = precise, high = creative."
          value={temperature}
          onChange={setTemperature}
        />

        {/* Top-P */}
        <SliderRow
          label="Top-P"
          hint="Nucleus sampling. Lower = more focused vocabulary."
          value={topP}
          onChange={setTopP}
        />

        {/* Divider */}
        <div className="border-t border-white/10" />

        {/* Pushy Push Notifications */}
        <div className="flex flex-col gap-5">
          <div className="flex items-baseline justify-between">
            <label className="text-[0.68rem] tracking-[0.22em] uppercase text-white/55">
              Push Notifications (Pushy)
            </label>
            <a
              href="https://pushy.me/dashboard"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[0.6rem] tracking-[0.14em] uppercase text-white/30 hover:text-white/60 transition-colors"
            >
              pushy.me ↗
            </a>
          </div>
          <p className="text-[0.62rem] tracking-wide text-white/35 -mt-2">
            Secret API Key from the Pushy dashboard. Device token is registered automatically by the mobile app.
          </p>

          <div className="flex flex-col gap-2">
            <label className="text-[0.6rem] tracking-[0.18em] uppercase text-white/35">
              Pushy Secret API Key
            </label>
            <div className="flex items-center gap-3">
              <input
                type={pushyMasked ? "password" : "text"}
                value={pushyApiKey}
                onChange={(e) => setPushyApiKey(e.target.value)}
                placeholder="Your app's secret API key"
                spellCheck={false}
                className="flex-1 border-b border-white/20 bg-transparent py-2 text-[0.9rem] font-light tracking-wide text-white placeholder:text-white/25 outline-none transition-colors focus:border-white/50"
              />
              <button
                onClick={() => setPushyMasked((v) => !v)}
                className="shrink-0 text-[0.65rem] tracking-[0.14em] uppercase text-white/35 hover:text-white/70 transition-colors"
              >
                {pushyMasked ? "show" : "hide"}
              </button>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <label className="text-[0.6rem] tracking-[0.18em] uppercase text-white/35">
              Device Token
            </label>
            <input
              type="text"
              value={pushyDeviceToken}
              onChange={(e) => setPushyDeviceToken(e.target.value)}
              placeholder="Auto-filled by the mobile app on first launch"
              spellCheck={false}
              className="border-b border-white/20 bg-transparent py-2 text-[0.9rem] font-light tracking-wide text-white placeholder:text-white/25 outline-none transition-colors focus:border-white/50"
            />
          </div>
        </div>

        {/* Divider */}
        <div className="border-t border-white/10" />

        {/* Reflection timing */}
        <div className="flex flex-col gap-5">
          <label className="text-[0.68rem] tracking-[0.22em] uppercase text-white/55">
            Reflection Pipeline
          </label>
          <p className="text-[0.62rem] tracking-wide text-white/35 -mt-2">
            Background worker that lets the AI reflect on conversations and send proactive messages.
          </p>
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <SliderRow
              label="Cooldown (hours)"
              hint="Min hours of silence before reflection can trigger."
              value={reflectionCooldown}
              onChange={setReflectionCooldown}
              min={1}
              max={24}
            />
            <SliderRow
              label="Interval (hours)"
              hint="Min hours between two reflections."
              value={reflectionInterval}
              onChange={setReflectionInterval}
              min={1}
              max={48}
            />
          </div>
          <button
            onClick={handleTriggerReflection}
            className="self-start border border-white/15 px-5 py-2 text-[0.65rem] tracking-[0.18em] uppercase text-white/40 transition-colors hover:border-white/40 hover:text-white/70"
          >
            {triggeringReflection ? "triggered" : "trigger now"}
          </button>
        </div>

        {/* Save */}
        <button
          onClick={handleSave}
          className="
            self-start border border-white/30 px-8 py-3
            text-[0.72rem] tracking-[0.22em] uppercase text-white/70
            transition-colors duration-500
            hover:border-white/70 hover:text-white
          "
        >
          {saved ? "saved" : "save"}
        </button>

      </div>
      </div>

      <style jsx>{`
        .slider {
          -webkit-appearance: none;
          appearance: none;
          height: 1px;
          background: rgba(255, 255, 255, 0.15);
          outline: none;
          cursor: pointer;
        }
        .slider::-webkit-slider-thumb {
          -webkit-appearance: none;
          appearance: none;
          width: 10px;
          height: 10px;
          border-radius: 50%;
          background: rgba(255, 255, 255, 0.7);
          transition: background 0.2s;
        }
        .slider::-webkit-slider-thumb:hover {
          background: #fff;
        }
        .slider::-moz-range-thumb {
          width: 10px;
          height: 10px;
          border-radius: 50%;
          border: none;
          background: rgba(255, 255, 255, 0.7);
        }
      `}</style>
    </div>
  );
}
