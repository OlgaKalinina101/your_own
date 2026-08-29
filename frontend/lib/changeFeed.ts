"use client";

/**
 * "The conversation changed" — the server's side of keeping a client honest.
 *
 * Fetching when the window regains focus covers switching devices. It does not
 * cover a message arriving while you are looking at the window, which is exactly
 * what an assistant writing on its own schedule does. So the server says so, on
 * `GET /api/events`, and this turns that into a callback.
 *
 * Not `EventSource`: it cannot send an Authorization header, and every route but
 * three is behind the token. A plain streaming fetch can, and the SSE framing is
 * already parsed for us by `lib/chatSse.ts`.
 */

import { apiFetch } from "@/lib/api";
import { splitSseBuffer } from "@/lib/chatSse";

/** How long to wait before reconnecting, growing on repeated failure. */
const RETRY_MIN_MS = 1_000;
const RETRY_MAX_MS = 30_000;

export interface ChangeFeedHandle {
  close: () => void;
}

/**
 * Whether a frame means "go and sync", as opposed to the stream saying it is
 * still alive.
 *
 * The keepalive is an SSE comment with no `data:` line. A client that treated
 * every frame as an event would sync every twenty seconds forever — which is
 * the polling this whole design exists to avoid.
 */
export function isChangeFrame(rawFrame: string): boolean {
  return rawFrame.split("\n").some((line) => line.startsWith("data: "));
}

/**
 * Listen until closed. Reconnects on its own — an idle stream through a laptop
 * that slept, or a backend restarted by a deploy, must not end the feed for
 * good, and both are ordinary here.
 */
export function subscribeToChanges(onChange: () => void): ChangeFeedHandle {
  let closed = false;
  let controller: AbortController | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let backoff = RETRY_MIN_MS;

  const connect = async () => {
    if (closed) return;
    controller = new AbortController();
    try {
      const response = await apiFetch("/api/events", { signal: controller.signal });
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

      // Connected: the next failure starts its wait from the bottom again.
      backoff = RETRY_MIN_MS;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (!closed) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { events, remainder } = splitSseBuffer(buffer);
        buffer = remainder;
        for (const raw of events) {
          if (isChangeFrame(raw)) onChange();
        }
      }
    } catch {
      // A dropped connection is the normal end of a long-lived stream, not an
      // error worth showing anyone. Reconnecting is the whole response.
    }
    if (closed) return;
    retryTimer = setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, RETRY_MAX_MS);
  };

  void connect();

  return {
    close: () => {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      controller?.abort();
    },
  };
}
