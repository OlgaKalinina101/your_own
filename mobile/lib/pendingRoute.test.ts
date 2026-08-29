/**
 * The two launch orders that produced "the push opens the dashboard".
 */
import { describe, expect, it, vi } from "vitest";

import { createPendingRoute } from "./pendingRoute";

describe("a tap during a cold start", () => {
  it("waits instead of navigating, and the launch screen reads it", () => {
    const route = createPendingRoute();
    const listener = vi.fn();
    route.subscribe(listener);

    // The notification click arrives while the connect screen is still
    // verifying the token.
    route.request("/chat");
    expect(listener).not.toHaveBeenCalled();

    // The connect screen finishes and asks where to go.
    route.markBootDecided();
    expect(route.consume()).toBe("/chat");
  });

  it("leaves the launch screen with its own default when nothing was tapped", () => {
    const route = createPendingRoute();
    route.markBootDecided();
    expect(route.consume()).toBeNull();
  });

  it("is taken exactly once", () => {
    // Two readers — the launch screen and the subscriber — must not both
    // navigate, or the second one lands on top of the first.
    const route = createPendingRoute();
    route.request("/chat");
    expect(route.consume()).toBe("/chat");
    expect(route.consume()).toBeNull();
  });
});

describe("a tap while the app is already running", () => {
  it("acts immediately", () => {
    const route = createPendingRoute();
    route.markBootDecided();

    const listener = vi.fn();
    route.subscribe(listener);

    route.request("/chat");
    expect(listener).toHaveBeenCalledTimes(1);
    expect(route.consume()).toBe("/chat");
  });

  it("stops calling a listener that unsubscribed", () => {
    const route = createPendingRoute();
    route.markBootDecided();
    const listener = vi.fn();
    const unsubscribe = route.subscribe(listener);

    unsubscribe();
    route.request("/chat");
    expect(listener).not.toHaveBeenCalled();
  });

  it("keeps only the most recent request", () => {
    // Two notifications tapped in quick succession are one destination.
    const route = createPendingRoute();
    route.request("/dashboard");
    route.request("/chat");
    expect(route.consume()).toBe("/chat");
  });
});
