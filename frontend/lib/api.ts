import { apiErrorFrom } from "@/lib/apiError";

/**
 * Thin wrapper around fetch that adds the auth token header
 * and resolves the backend URL.
 *
 * Server URL and auth token are stored in localStorage so they
 * work in both Electron and plain browser contexts.
 *
 * When the page is served from a remote domain (e.g. ngrok),
 * API calls use relative paths so Next.js rewrites proxy them
 * to the backend — this avoids cross-origin (CORS) issues.
 */

const DEFAULT_BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export function getBackendUrl(): string {
  if (typeof window === "undefined") return DEFAULT_BACKEND;
  return localStorage.getItem("your_own_backend_url") || DEFAULT_BACKEND;
}

export function setBackendUrl(url: string): void {
  localStorage.setItem("your_own_backend_url", url.replace(/\/+$/, ""));
}

export function getAuthToken(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("your_own_auth_token") || "";
}

export function setAuthToken(token: string): void {
  localStorage.setItem("your_own_auth_token", token);
}

/**
 * Returns true when the page is served from a remote domain (not localhost).
 * In this case we use relative paths so Next.js rewrites proxy to the backend.
 */
function isRemoteClient(): boolean {
  if (typeof window === "undefined") return false;
  const h = window.location.hostname;
  return h !== "localhost" && h !== "127.0.0.1" && h !== "0.0.0.0";
}

/**
 * Returns the URL prefix for API calls.
 * Empty string for remote clients (so requests go same-origin, proxied by
 * Next.js rewrites), or the full backend URL for local/Electron clients.
 */
export function getApiBase(): string {
  return isRemoteClient() ? "" : getBackendUrl();
}

let _autoTokenAttempted = false;

/**
 * Read the auth token from the desktop shell, which has it on disk.
 *
 * The backend used to serve it over HTTP to any caller whose socket looked
 * local — and behind a reverse proxy every remote caller looks local,
 * including the Next.js rewrite this app uses for tunnels. So the token is
 * handed over in-process or not at all; a plain browser pastes it in Settings.
 */
export async function readTokenFromDesktopShell(): Promise<string | null> {
  if (typeof window === "undefined" || !("yourOwn" in window)) return null;
  try {
    return await window.yourOwn.getBackendAuthToken();
  } catch {
    return null;
  }
}

async function ensureAuthToken(): Promise<void> {
  if (getAuthToken() || _autoTokenAttempted) return;
  _autoTokenAttempted = true;
  const token = await readTokenFromDesktopShell();
  if (token) setAuthToken(token);
}

/**
 * Authenticated fetch — adds Bearer token automatically.
 * On first call, tries to auto-acquire token from local backend.
 *
 * When served from a remote domain, uses relative paths (proxied by
 * Next.js rewrites) to avoid CORS issues with ngrok / tunnels.
 */
export async function apiFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  await ensureAuthToken();
  const base = isRemoteClient() ? "" : getBackendUrl();
  const url = `${base}${path}`;
  const token = getAuthToken();
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  headers.set("ngrok-skip-browser-warning", "true");
  return fetch(url, { ...init, headers });
}

/**
 * Every helper below throws an ApiError on a non-2xx.
 *
 * `apiFetch` deliberately does not: an SSE call needs the raw Response, and a
 * few callers read the body themselves. But that made "did this work?" opt-in,
 * and the opt-in was forgotten — a 401 from `POST /api/body/generate` looked
 * exactly like success, so the page started polling and left every card
 * shimmering "generating…" until the app was closed.
 */
async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) throw await apiErrorFrom(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** GET JSON. Throws on a non-2xx. */
export async function apiGet<T = unknown>(path: string): Promise<T> {
  return unwrap<T>(await apiFetch(path));
}

/** PUT JSON. Throws on a non-2xx. */
export async function apiPut<T = unknown>(path: string, body: unknown): Promise<T> {
  return unwrap<T>(
    await apiFetch(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

/**
 * POST, throwing on a non-2xx. Pass FormData as-is; the browser sets the
 * boundary itself and adding a Content-Type here would break the upload.
 */
export async function apiPost<T = unknown>(
  path: string,
  body?: unknown,
): Promise<T> {
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  return unwrap<T>(
    await apiFetch(path, {
      method: "POST",
      ...(body === undefined
        ? {}
        : isForm
          ? { body }
          : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    }),
  );
}

/**
 * Delete a chat pair by id. Best-effort — nothing depends on it succeeding.
 *
 * The backend saves what the user already saw when a stream breaks
 * (`_save_partial`), so an aborted or failed reply leaves a half-pair behind.
 * The `pair_id` frame is the only handle for removing it, which is why the
 * desktop must stop skipping that frame: the phone has done this all along.
 */
export async function deleteChatPair(pairId: string): Promise<void> {
  await apiFetch(`/api/chat/pair/${pairId}`, { method: "DELETE" });
}
