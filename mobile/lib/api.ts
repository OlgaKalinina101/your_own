/**
 * API client for the Your Own backend.
 *
 * Backend URL in AsyncStorage; the auth token in the keychain (see below).
 * All requests go directly to the backend URL (no Next.js proxy needed in native app).
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import { apiErrorFrom } from "./apiError";
import { PUSH_STORAGE_KEYS } from "./pushPolicy";
import { decideTokenMigration } from "./tokenMigration";
import type { Settings } from "./types";

const KEY_BACKEND_URL = "backend_url";

/**
 * The token lives in the keychain, not in AsyncStorage.
 *
 * AsyncStorage is a SQLite file on Android and a plist on iOS: fine for an
 * address and a volume, wrong for a credential that opens the entire backend —
 * `/api/settings/raw` included, which hands back the OpenRouter key in full.
 *
 * `LEGACY_KEY_AUTH_TOKEN` is where it used to sit; it is read once, moved, and
 * deleted. See lib/tokenMigration.ts.
 *
 * Default accessibility (`WHEN_UNLOCKED`) is deliberate and sufficient: nothing
 * reads the token while the phone is locked. Push delivery does not need it —
 * the notification is built by the Pushy handler from the payload alone.
 */
const SECURE_KEY_AUTH_TOKEN = "auth_token";
const LEGACY_KEY_AUTH_TOKEN = "auth_token";

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
// It also keeps the keychain out of the path of every request: `buildHeaders`
// runs per call, and a keychain read is not free the way a SQLite read was.
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

/** Set once the plaintext copy has been dealt with, so it is looked for once. */
let legacyChecked = false;

export async function getAuthToken(): Promise<string | null> {
  if (cachedAuthToken !== null) return cachedAuthToken;

  let secure: string | null;
  try {
    secure = await SecureStore.getItemAsync(SECURE_KEY_AUTH_TOKEN);
  } catch (error) {
    // A keychain that will not answer is not a reason to destroy anything.
    // Report no token, let the person reconnect, leave storage untouched.
    console.warn("[api] secure store unreadable:", error);
    return null;
  }

  if (legacyChecked) {
    cachedAuthToken = secure || null;
    return cachedAuthToken;
  }

  const legacy = await AsyncStorage.getItem(LEGACY_KEY_AUTH_TOKEN);
  const plan = decideTokenMigration({ secure, legacy });
  if (plan.writeSecure && plan.token) {
    await SecureStore.setItemAsync(SECURE_KEY_AUTH_TOKEN, plan.token);
  }
  if (plan.clearLegacy) {
    await AsyncStorage.removeItem(LEGACY_KEY_AUTH_TOKEN);
  }
  legacyChecked = true;
  cachedAuthToken = plan.token;
  return plan.token;
}

export async function setAuthToken(token: string): Promise<void> {
  const trimmed = token.trim();
  // Deliberately allowed to throw. A token that was not stored is gone on the
  // next launch, and the connect screen is the only place able to say so.
  await SecureStore.setItemAsync(SECURE_KEY_AUTH_TOKEN, trimmed);
  await AsyncStorage.removeItem(LEGACY_KEY_AUTH_TOKEN);
  legacyChecked = true;
  cachedAuthToken = trimmed;
}

export async function clearAuth(): Promise<void> {
  cachedAuthToken = null;
  legacyChecked = false;
  await SecureStore.deleteItemAsync(SECURE_KEY_AUTH_TOKEN).catch(() => {});
  // The push keys go too. Leaving them behind meant a disconnected phone still
  // believed it was registered, so the next connect decided "unchanged" and
  // never told the new server anything. The legacy key is in the list for a
  // phone that was disconnected before it ever migrated.
  await AsyncStorage.multiRemove([
    KEY_BACKEND_URL,
    LEGACY_KEY_AUTH_TOKEN,
    ...PUSH_STORAGE_KEYS,
  ]);
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

// Both of these throw. `apiFetch` deliberately does not — the streaming and
// media paths want the raw Response — but a screen asking for data has no use
// for a 401 that looks like success, and that is how "0 memories" used to mean
// "not authorised".
export async function apiGet<T>(path: string): Promise<T> {
  const res = await apiFetch(path);
  if (!res.ok) throw await apiErrorFrom(res);
  return res.json() as Promise<T>;
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const res = await apiFetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await apiErrorFrom(res);
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
