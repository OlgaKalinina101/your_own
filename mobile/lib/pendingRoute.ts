/**
 * Where a notification asked the app to go, held until someone can take it there.
 *
 * Tapping a push is supposed to open the chat. It opened the dashboard, and two
 * separate things had to be fixed for it not to.
 *
 * The second one is this. On a cold start the connect screen is the first route,
 * and its `useEffect` verifies the stored token over the network before doing
 * `router.replace("/dashboard")`. Any navigation the push handler managed
 * earlier is wiped by that replace — not as a race, but every time, because the
 * replace is waiting on a round trip and therefore always lands last.
 *
 * So the tap does not navigate on a cold start. It leaves a note, and the
 * connect screen — the thing that decides where a launch ends up — reads the
 * note instead of assuming the dashboard. Once that decision has been made, a
 * later tap has nothing to collide with and navigates immediately.
 *
 * A factory rather than four module-level variables so a test gets a fresh one;
 * the app uses the singleton at the bottom.
 */

/** The screens a notification is allowed to ask for. */
export type RequestableRoute = "/chat" | "/dashboard";

export interface PendingRoute {
  /** A notification was tapped. */
  request: (path: RequestableRoute) => void;
  /** Take the pending route, if any. Whoever takes it owns the navigation. */
  consume: () => RequestableRoute | null;
  /** The launch screen has chosen a destination; later taps act at once. */
  markBootDecided: () => void;
  /** Called when a request arrives after boot. Returns an unsubscribe. */
  subscribe: (listener: () => void) => () => void;
}

export function createPendingRoute(): PendingRoute {
  let pending: RequestableRoute | null = null;
  let bootDecided = false;
  const listeners = new Set<() => void>();

  return {
    request(path) {
      pending = path;
      // Before boot is decided, storing is the whole job: navigating now only
      // to be replaced a moment later is what produced the bug.
      if (!bootDecided) return;
      for (const listener of [...listeners]) listener();
    },
    consume() {
      const path = pending;
      pending = null;
      return path;
    },
    markBootDecided() {
      bootDecided = true;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

export const pendingRoute = createPendingRoute();
