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

// Mirrors the stored token so it can be read synchronously.
//
// `<Image source={{uri, headers}}>` in React Native *can* carry an
// Authorization header — a web `<img>` cannot, which is why the desktop signs
// media URLs instead (see `frontend/lib/media.ts`). The two clients differ here
// on purpose: this is the better mechanism, and it is available only here.
//
// Warmed by the first getAuthToken() of the session, which every screen does
// long before it renders an image.
let cachedAuthToken: string | null = null;

/** The token if it has been read at least once this session, else null. */
export function peekAuthToken(): string | null {
  return cachedAuthToken;
}

export async function getAuthToken(): Promise<string | null> {
  cachedAuthToken = await AsyncStorage.getItem(KEY_AUTH_TOKEN);
  return cachedAuthToken;
}

export async function setAuthToken(token: string): Promise<void> {
  cachedAuthToken = token.trim();
  await AsyncStorage.setItem(KEY_AUTH_TOKEN, cachedAuthToken);
}

export async function clearAuth(): Promise<void> {
  cachedAuthToken = null;
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

/** Delete a chat pair from the backend DB by pair_id. Best-effort. */
export async function deleteChatPair(pairId: string): Promise<void> {
  await apiFetch(`/api/chat/pair/${pairId}`, { method: "DELETE" });
}

/** Return the latest workbench note (stripped of markdown). */
export async function loadWorkbenchLatest(
  accountId = "default",
): Promise<{ ts: string | null; text: string | null }> {
  return apiGet(`/api/settings/workbench/latest?account_id=${accountId}`);
}

/** Paginated workbench entries (file + Chroma archive), newest first. */
export async function loadWorkbenchEntries(
  offset = 0,
  limit = 25,
  accountId = "default",
): Promise<{ entries: { ts: string; text: string }[]; has_more: boolean }> {
  return apiGet(
    `/api/settings/workbench/entries?account_id=${accountId}&offset=${offset}&limit=${limit}`,
  );
}

/** Inspiration facts from Chroma (category = Inspiration or Вдохновение). */
export async function loadInspirationFacts(
  accountId = "default",
): Promise<{ id: string; text: string }[]> {
  let facts = await apiGet<{ id: string; text: string }[]>(
    `/api/chroma/facts?account_id=${accountId}&category=Inspiration&sort=created_at`,
  );
  if (facts.length === 0) {
    facts = await apiGet<{ id: string; text: string }[]>(
      `/api/chroma/facts?account_id=${encodeURIComponent(accountId)}&category=${encodeURIComponent("Вдохновение")}&sort=created_at`,
    );
  }
  return facts;
}

/** Return raw identity.md content. */
export async function loadIdentity(
  accountId = "default",
): Promise<{ text: string }> {
  return apiGet(`/api/settings/identity?account_id=${accountId}`);
}
