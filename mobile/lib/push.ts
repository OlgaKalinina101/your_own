/**
 * Pushy push notification setup for the mobile app.
 *
 * On first launch:
 *  1. Registers the device with Pushy to get a device token.
 *  2. Saves the token to AsyncStorage and to the backend.
 *
 * On notification tap — navigates to the chat screen.
 * On notification received (foreground) — fires registered listeners
 * so the InAppNotification component can display the message.
 */
import { Platform } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { registerPushyToken } from "./api";

const KEY_DEVICE_TOKEN = "pushy_device_token";

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

export async function setupPushNotifications(): Promise<void> {
  try {
    const Pushy = (await import("pushy-react-native")).default;

    if (!_listenersSet) {
      Pushy.setNotificationListener((data: Record<string, string>) => {
        console.log("[push] received:", data);
        _emit(data);

        // Display a system notification (sound + shade) on Android
        const title = data.title || "AI";
        const body = data.message || data.body || "";
        if (body) {
          Pushy.notify(title, body, data);
        }
      });

      Pushy.setNotificationClickListener((data: Record<string, string>) => {
        console.log("[push] tapped:", data);
        import("expo-router").then(({ router }) => {
          router.push("/chat");
        });
      });
      _listenersSet = true;
    }

    const deviceToken: string = await Pushy.register();
    console.log("[push] device token:", deviceToken);

    await AsyncStorage.setItem(KEY_DEVICE_TOKEN, deviceToken);
    await registerPushyToken(deviceToken);
  } catch (err) {
    console.warn("[push] setup failed (expected in Expo Go):", err);
  }
}

export async function getStoredDeviceToken(): Promise<string | null> {
  return AsyncStorage.getItem(KEY_DEVICE_TOKEN);
}
