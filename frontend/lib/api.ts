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
 * Try to auto-acquire the auth token from a local backend.
 * Only works when the request originates from 127.0.0.1.
 */
async function ensureAuthToken(): Promise<void> {
  if (getAuthToken() || _autoTokenAttempted) return;
  _autoTokenAttempted = true;
  try {
    const base = isRemoteClient() ? "" : getBackendUrl();
    const res = await fetch(`${base}/api/settings/local-token`, {
      headers: { "ngrok-skip-browser-warning": "true" },
    });
    if (res.ok) {
      const data = await res.json();
      if (data.token) setAuthToken(data.token);
    }
  } catch {
    // not local or backend unreachable
  }
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
 * Convenience: GET JSON from the backend.
 */
export async function apiGet<T = unknown>(path: string): Promise<T> {
  const res = await apiFetch(path);
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

/**
 * Convenience: PUT JSON to the backend.
 */
export async function apiPut<T = unknown>(
  path: string,
  body: unknown,
): Promise<T> {
  const res = await apiFetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}
