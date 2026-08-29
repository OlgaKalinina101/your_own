/**
 * Push notifications, which on this phone means Pushy and nothing else.
 *
 * `expo-notifications` used to sit in package.json beside it with zero imports,
 * no entry in app.json's `plugins`, and no role — so "two push stacks" was never
 * a division of labour, just a dependency nobody removed. It is gone.
 *
 * The division that does exist is between the two things `Pushy.register()`
 * conflates and this file separates:
 *
 *  - **listeners** — free, silent, needed before any push can be handled, and so
 *    installed at app start (`app/_layout.tsx`);
 *  - **registration** — asks the operating system for permission and hands the
 *    device token to the backend, so it happens only once there is a backend to
 *    hand it to. Asking a stranger for notification permission on the very first
 *    frame of the very first launch is how permission gets denied for good.
 *
 * The decisions themselves are in `lib/pushPolicy.ts`, which imports nothing
 * native and is therefore the half that can be tested.
 */
import { AppState, Platform } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { registerPushyToken } from "./api";
import { pendingRoute } from "./pendingRoute";
import {
  KEY_DEVICE_TOKEN,
  KEY_REGISTERED_WITH,
  decidePushRegistration,
  shouldPostToShade,
} from "./pushPolicy";

// ── In-app push event bus ────────────────────────────────────────────────────

type PushHandler = (data: Record<string, string>) => void;
const _listeners = new Set<PushHandler>();

export function onPush(handler: PushHandler): void {
  _listeners.add(handler);
}

export function offPush(handler: PushHandler): void {
  _listeners.delete(handler);
}

function _emit(data: Record<string, string>): void {
  for (const fn of _listeners) {
    try {
      fn(data);
    } catch (e) {
      console.warn("[push] listener error:", e);
    }
  }
}

// ── Setup ────────────────────────────────────────────────────────────────────

let _listenersSet = false;

/**
 * Wire up delivery. Asks for nothing and shows nothing — safe at app start.
 */
export async function installPushListeners(): Promise<void> {
  if (_listenersSet) return;
  try {
    const Pushy = (await import("pushy-react-native")).default;

    Pushy.setNotificationListener(async (data: string | object) => {
      const d = data as Record<string, string>;
      _emit(d);

      if (!shouldPostToShade(AppState.currentState)) return;
      const body = d.message || d.body || "";
      if (body) Pushy.notify(d.title || "", body, d);
    });

    Pushy.setNotificationClickListener((data: string | object) => {
      console.log("[push] tapped:", data);
      // Not `router.push` from here. On a cold start the connect screen is still
      // deciding where the launch ends up and will replace whatever this does;
      // see lib/pendingRoute.ts.
      pendingRoute.request("/chat");
    });

    // The line without which none of the above ever fires.
    //
    // `Pushy.register()` only obtains a device token. `listen()` is the other
    // half, and it is not optional on either platform:
    //
    //  - Android: it re-delivers the launch intent as a `NotificationClick`,
    //    which is the *only* way a tap that started the app is ever seen. Taps
    //    on a running app came through; taps that launched it silently did not,
    //    which is exactly "the push opens the dashboard".
    //  - iOS: it installs the notification handler itself, so without it neither
    //    `Notification` nor `NotificationClick` is ever emitted at all.
    //
    // Called after the two listeners on purpose: the launch intent is replayed
    // inside it, and there has to be someone subscribed to hear it.
    Pushy.listen();

    _listenersSet = true;
  } catch (err) {
    // Expected in Expo Go, where the native module is absent. A real device
    // without it is a broken build, and the log line is the only clue.
    console.warn(`[push] listeners not installed (${Platform.OS}):`, err);
  }
}

/**
 * Ask the system for permission, get a device token, tell the backend.
 *
 * Call only once there is a backend worth telling: after a successful connect,
 * or at start when credentials are already stored.
 */
export async function registerForPush(backendUrl: string): Promise<void> {
  try {
    await installPushListeners();
    const Pushy = (await import("pushy-react-native")).default;

    const freshToken: string = await Pushy.register();
    const [storedToken, registeredWith] = await Promise.all([
      AsyncStorage.getItem(KEY_DEVICE_TOKEN),
      AsyncStorage.getItem(KEY_REGISTERED_WITH),
    ]);

    const decision = decidePushRegistration({
      storedToken,
      freshToken,
      backendUrl,
      registeredWith,
    });
    if (!decision.register) return;

    console.log(`[push] registering device token (${decision.reason})`);
    await AsyncStorage.multiSet([
      [KEY_DEVICE_TOKEN, freshToken],
      [KEY_REGISTERED_WITH, backendUrl],
    ]);
    await registerPushyToken(freshToken);
  } catch (err) {
    console.warn("[push] registration failed (expected in Expo Go):", err);
  }
}

/**
 * Tell the backend currently in storage to forget this device.
 *
 * Must run *before* the address or the token is changed, because it is that
 * backend — the one being left — that needs to stop pushing. Without this,
 * disconnecting cleared the phone's credentials and left the server happily
 * sending notifications to a device that no longer talks to it.
 */
export async function revokeDeviceToken(): Promise<void> {
  // `registerPushyToken` swallows its own failures: a server that cannot be
  // reached to be told is exactly the server we are walking away from.
  await registerPushyToken("");
  await AsyncStorage.removeItem(KEY_REGISTERED_WITH);
}

export async function getStoredDeviceToken(): Promise<string | null> {
  return AsyncStorage.getItem(KEY_DEVICE_TOKEN);
}
