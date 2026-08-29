/**
 * The two decisions inside push handling, tested away from the native module.
 *
 * Both used to live inside `setupPushNotifications`, downstream of a dynamic
 * import of `pushy-react-native` — which is to say unreachable by any test. That
 * was the finding; the missing coverage was only its symptom.
 *
 * They live in `pushPolicy.ts` rather than `push.ts` because that is what makes
 * the extraction real: `push.ts` imports `AppState`, and importing it here
 * fails the runner outright.
 */
import { describe, expect, it } from "vitest";

import { decidePushRegistration, shouldPostToShade } from "./pushPolicy";

const HOME = "http://192.168.1.10:8000";
const VPS = "https://viktor.example";

describe("decidePushRegistration", () => {
  it("registers a device that has never registered", () => {
    expect(
      decidePushRegistration({
        storedToken: null,
        freshToken: "t1",
        backendUrl: HOME,
        registeredWith: null,
      }),
    ).toEqual({ register: true, reason: "new" });
  });

  it("stays quiet when nothing has changed", () => {
    // Pushy.register() runs on every launch and mostly returns the same token.
    // Re-sending it every time is a write to the settings file for no reason.
    expect(
      decidePushRegistration({
        storedToken: "t1",
        freshToken: "t1",
        backendUrl: HOME,
        registeredWith: HOME,
      }),
    ).toEqual({ register: false, reason: "unchanged" });
  });

  it("registers again when Pushy hands out a new token", () => {
    expect(
      decidePushRegistration({
        storedToken: "t1",
        freshToken: "t2",
        backendUrl: HOME,
        registeredWith: HOME,
      }),
    ).toEqual({ register: true, reason: "rotated" });
  });

  it("registers with a server that has never heard of this device", () => {
    // The move to a VPS. Same phone, same token, a backend that knows nothing
    // about it — and until this case existed, pushes simply stopped arriving
    // and nothing anywhere said why.
    expect(
      decidePushRegistration({
        storedToken: "t1",
        freshToken: "t1",
        backendUrl: VPS,
        registeredWith: HOME,
      }),
    ).toEqual({ register: true, reason: "moved" });
  });

  it("treats a token stored before this bookkeeping existed as a move", () => {
    // Upgrading the app: the token is in storage, the address it was sent to
    // is not, because nothing used to record it.
    expect(
      decidePushRegistration({
        storedToken: "t1",
        freshToken: "t1",
        backendUrl: HOME,
        registeredWith: null,
      }),
    ).toEqual({ register: true, reason: "moved" });
  });
});

describe("shouldPostToShade", () => {
  it("stays out of the shade while the app is on screen", () => {
    // The in-app banner is already showing it; the shade would be a second copy
    // of one message, and the copy is the one that stays there.
    expect(shouldPostToShade("active")).toBe(false);
  });

  it("posts when the app is in the background", () => {
    expect(shouldPostToShade("background")).toBe(true);
    expect(shouldPostToShade("inactive")).toBe(true);
  });

  it("posts when the app state is not something it recognises", () => {
    // One-sided on purpose: a Headless JS task has no reliable AppState, and a
    // duplicate notification is a wart while a dropped one is the whole feature
    // failing silently.
    expect(shouldPostToShade(undefined)).toBe(true);
    expect(shouldPostToShade(null)).toBe(true);
    expect(shouldPostToShade("unknown")).toBe(true);
  });
});
