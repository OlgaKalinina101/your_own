/**
 * API client for the Your Own backend.
 *
 * Stores backend URL and auth token in AsyncStorage.
 * All requests go directly to the backend URL (no Next.js proxy needed in native app).
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import type { Settings } from "./types";

const KEY_BACKEND_URL = "backend_url";
const KEY_AUTH_TOKEN = "auth_token";

export const DEFAULT_BACKEND_URL = "http://localhost:8000";

// ── Storage helpers ───────────────────────────────────────────────────────────

export async function getBackendUrl(): Promise<string> {
  const stored = await AsyncStorage.getItem(KEY_BACKEND_URL);
  return stored ?? DEFAULT_BACKEND_URL;
}

export async function setBackendUrl(url: string): Promise<void> {
  await AsyncStorage.setItem(KEY_BACKEND_URL, url.trim().replace(/\/$/, ""));
}

export async function getAuthToken(): Promise<string | null> {
  return AsyncStorage.getItem(KEY_AUTH_TOKEN);
}

export async function setAuthToken(token: string): Promise<void> {
  await AsyncStorage.setItem(KEY_AUTH_TOKEN, token.trim());
}

export async function clearAuth(): Promise<void> {
  await AsyncStorage.multiRemove([KEY_BACKEND_URL, KEY_AUTH_TOKEN]);
}

// ── HTTP helpers ──────────────────────────────────────────────────────────────

const NGROK_HEADER = { "ngrok-skip-browser-warning": "true" };

async function buildHeaders(extra?: Record<string, string>): Promise<Record<string, string>> {
  const token = await getAuthToken();
  return {
    ...NGROK_HEADER,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(extra ?? {}),
  };
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const base = await getBackendUrl();
  const url = `${base}${path}`;
  const authHeaders = await buildHeaders();
  const initHeaders = (init?.headers ?? {}) as Record<string, string>;
  return fetch(url, {
    ...init,
    headers: { ...initHeaders, ...authHeaders },
  });
}

/**
 * Streaming-capable fetch using expo/fetch (supports ReadableStream body).
 * Use for SSE endpoints like /api/chat where response.body.getReader() is needed.
 */
export async function apiFetchStreaming(path: string, init?: RequestInit): Promise<Response> {
  const { fetch: expoFetch } = await import("expo/fetch");
  const base = await getBackendUrl();
  const url = `${base}${path}`;
  const authHeaders = await buildHeaders();
  const initHeaders = (init?.headers ?? {}) as Record<string, string>;
  return expoFetch(url, {
    ...init,
    headers: { ...initHeaders, ...authHeaders },
  });
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await apiFetch(path);
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const res = await apiFetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`PUT ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

/** Register the Pushy device token on the backend. */
export async function registerPushyToken(deviceToken: string): Promise<void> {
  try {
    await apiPut<unknown>("/api/settings", { pushy_device_token: deviceToken });
  } catch (err) {
    console.warn("[api] failed to register pushy token:", err);
  }
}

/**
 * Test connectivity and (optionally) verify the auth token.
 *
 * When `token` is provided, hits POST /api/settings/verify-token with Bearer auth.
 * Otherwise just pings the unprotected /ping endpoint.
 *
 * Returns null on success, or a human-readable error string.
 */
export async function testConnection(url: string, token?: string | null): Promise<string | null> {
  const cleanUrl = url.replace(/\/$/, "");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 6000);
  try {
    const headers: Record<string, string> = {
      ...NGROK_HEADER,
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };
    const endpoint = token
      ? `${cleanUrl}/api/settings/verify-token`
      : `${cleanUrl}/api/settings/ping`;
    const method = token ? "POST" : "GET";

    const res = await fetch(endpoint, { method, signal: controller.signal, headers });
    if (res.ok) return null;
    if (res.status === 401) return "Invalid auth token";
    return `HTTP ${res.status}`;
  } catch (e: unknown) {
    if (e instanceof Error && e.name === "AbortError") return "Timeout (6s) — check URL and network";
    return e instanceof Error ? e.message : String(e);
  } finally {
    clearTimeout(timer);
  }
}

/** Load full settings (raw) from backend. */
export async function loadSettings(): Promise<Settings> {
  return apiGet<Settings>("/api/settings/raw");
}

/** Save partial settings patch to backend. */
export async function saveSettings(patch: Partial<Settings>): Promise<void> {
  await apiPut<unknown>("/api/settings", patch);
}
