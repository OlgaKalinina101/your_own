/**
 * "The conversation changed" — the server's side of keeping a client honest.
 *
 * Counterpart to `frontend/lib/changeFeed.ts`. Two differences, both forced by
 * the platform rather than chosen:
 *
 *  - `expo/fetch`, because React Native's built-in fetch does not expose a
 *    readable body. `lib/api.ts` already keeps `apiFetchStreaming` for the same
 *    reason on the chat stream;
 *  - `AppState` instead of `visibilitychange`, wired up by the caller.
 *
 * Why this exists at all: the conversation has three writers — this app, the
 * desktop, and the assistant, which persists a message on its own schedule.
 * A push notification wakes the phone, but a phone already sitting open with
 * the chat on screen was told nothing.
 */

import { apiFetchStreaming } from "@/lib/api";
import { splitSseBuffer } from "@/lib/chatSse";

const RETRY_MIN_MS = 1_000;
const RETRY_MAX_MS = 30_000;

export interface ChangeFeedHandle {
  close: () => void;
}

/**
 * Whether a frame means "go and sync", as opposed to the stream saying it is
 * still alive.
 *
 * The keepalive is an SSE comment with no `data:` line. Treating every frame as
 * an event would turn this into a twenty-second poll.
 */
export function isChangeFrame(rawFrame: string): boolean {
  return rawFrame.split("\n").some((line) => line.startsWith("data: "));
}

/**
 * Listen until closed. Reconnects on its own — a phone that slept and a backend
 * restarted by a deploy are both ordinary here, and neither should end the feed
 * for good.
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
      const response = await apiFetchStreaming("/api/events", {
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

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
      // A dropped connection is how a long-lived stream normally ends, not an
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
