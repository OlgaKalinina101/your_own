"use client";

/**
 * URLs for media the backend serves behind auth.
 *
 * `<img src=…>` cannot send an Authorization header — no browser does, and the
 * three media routes (`/api/generated_images`, `/api/user_uploads`, `/api/body`)
 * are authenticated. So the proof of auth travels in the query string as a
 * short-lived signature derived from the token; the master token itself must
 * not go there, because a URL lands in access logs, history and referrers.
 * The backend side is documented above `issue_media_signature` in
 * `infrastructure/auth.py`.
 *
 * The signature is fetched once and shared by every image on the page. It is
 * refreshed a minute before it expires, so an `<img>` never starts loading with
 * a value that dies mid-flight.
 */

import { useCallback, useMemo, useSyncExternalStore } from "react";

import { apiGet, getApiBase } from "@/lib/api";

type Cached = { sig: string; expiresAt: number };

const REFRESH_MARGIN_MS = 60_000;

let cached: Cached | null = null;
let inflight: Promise<void> | null = null;
const listeners = new Set<() => void>();

function currentSignature(): string | null {
  if (!cached) return null;
  return Date.now() < cached.expiresAt ? cached.sig : null;
}

function refresh(): Promise<void> {
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const data = await apiGet<{ sig: string; expires_in: number }>(
        "/api/settings/media-signature",
      );
      const ttlMs = Math.max(data.expires_in * 1000 - REFRESH_MARGIN_MS, 0);
      cached = { sig: data.sig, expiresAt: Date.now() + ttlMs };
    } catch {
      // No token yet, or the backend is down. Images stay hidden rather than
      // rendering a broken tag; the next subscriber retries.
      cached = null;
    } finally {
      inflight = null;
      listeners.forEach((notify) => notify());
    }
  })();
  return inflight;
}

/**
 * Full URL for a backend media path, or `""` while the signature is loading.
 *
 * Anything that is already a complete URL is returned untouched — the chat
 * shows freshly attached images as `data:` previews before the upload lands,
 * and prefixing those with the backend origin would break them.
 */
export function mediaUrl(path: string): string {
  if (!path.startsWith("/")) return path;
  const sig = currentSignature();
  if (!sig) {
    void refresh();
    return "";
  }
  const separator = path.includes("?") ? "&" : "?";
  return `${getApiBase()}${path}${separator}sig=${encodeURIComponent(sig)}`;
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  if (!currentSignature()) void refresh();
  return () => {
    listeners.delete(onChange);
  };
}

/**
 * Re-renders the caller once the signature is available.
 *
 * Call it wherever `mediaUrl()` is used inside a loop — a hook cannot go in
 * the loop itself, so the component subscribes once and the plain function
 * reads the fresh value.
 */
export function useMediaSignature(): string | null {
  return useSyncExternalStore(
    subscribe,
    currentSignature,
    useCallback(() => null, []),
  );
}

/** Convenience for a single image: subscribes and resolves in one call. */
export function useMediaUrl(path: string): string {
  const sig = useMediaSignature();
  return useMemo(() => (sig ? mediaUrl(path) : ""), [sig, path]);
}
