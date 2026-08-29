/**
 * The decisions inside push handling, with nothing native attached.
 *
 * Separate from `lib/push.ts` for a reason the test runner made concrete: that
 * file imports `AppState` from react-native, so importing it at all pulls in
 * Flow-typed sources the runner cannot parse. "Extract the decision into a
 * function" is only done when the function can be reached without a device —
 * otherwise it is the same code with a name.
 *
 * Storage key names live here too, so `lib/api.ts` can clear them on logout
 * without importing the native half.
 */

export const KEY_DEVICE_TOKEN = "pushy_device_token";

/**
 * Which backend the stored token was last handed to.
 *
 * Without it there is no way to notice that the server moved, and the old one
 * keeps a token it will go on pushing to forever.
 */
export const KEY_REGISTERED_WITH = "pushy_registered_with";

/** Everything push owns in storage, for `clearAuth` to wipe along with the rest. */
export const PUSH_STORAGE_KEYS = [KEY_DEVICE_TOKEN, KEY_REGISTERED_WITH];

export type RegistrationReason = "new" | "rotated" | "moved" | "unchanged";

export interface PushRegistration {
  /** Whether the backend needs to be told about this token. */
  register: boolean;
  reason: RegistrationReason;
}

/**
 * Does the backend need to hear about this device token?
 *
 * `unchanged` is the case that earns this function its keep: `Pushy.register()`
 * runs on every launch and mostly hands back the same token, and re-sending it
 * every time is a write to the server's settings file for no reason.
 *
 * `moved` is the case that used to be missing entirely. The token stays the
 * same when the backend changes address, so nothing looked different — and
 * pushes simply stopped arriving, with nothing anywhere saying why.
 */
export function decidePushRegistration(input: {
  storedToken: string | null;
  freshToken: string;
  backendUrl: string;
  registeredWith: string | null;
}): PushRegistration {
  const { storedToken, freshToken, backendUrl, registeredWith } = input;

  if (!storedToken) return { register: true, reason: "new" };
  if (storedToken !== freshToken) return { register: true, reason: "rotated" };
  // Same token, different server: the new one has never heard of this device.
  if (registeredWith !== backendUrl) return { register: true, reason: "moved" };
  return { register: false, reason: "unchanged" };
}

/**
 * Should this push be posted to the notification shade?
 *
 * On Android `setNotificationListener` registers a Headless JS task, and it runs
 * for every push whether the app is open or not. So with the app on screen the
 * same message arrived twice: once as the in-app banner, once in the shade via
 * `Pushy.notify` — and the shade copy is the one that stays there.
 *
 * The test is one-sided on purpose. Only a state we are certain about
 * ("active") suppresses the system notification; anything else falls through to
 * showing it. A duplicate notification is a wart; a dropped one is the feature
 * failing silently, which is the failure this app can least afford.
 */
export function shouldPostToShade(appState: string | null | undefined): boolean {
  return appState !== "active";
}
