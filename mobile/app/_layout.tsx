import { router, Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { View, StyleSheet } from "react-native";
import { KeyboardProvider } from "react-native-keyboard-controller";
import { getAuthToken, getBackendUrl } from "@/lib/api";
import { pendingRoute } from "@/lib/pendingRoute";
import { installPushListeners, registerForPush } from "@/lib/push";
import InAppNotification from "@/components/InAppNotification";

export default function RootLayout() {
  useEffect(() => {
    // Listeners cost nothing and show nothing, so they go up immediately.
    void installPushListeners();

    // Registration is the half that asks the operating system for permission,
    // and it waits until there is a backend to hand the token to. On a first
    // launch that is the connect screen's job — asking a stranger for
    // notification permission on frame one is how it gets denied for good.
    void (async () => {
      const [token, url] = await Promise.all([getAuthToken(), getBackendUrl()]);
      if (token) await registerForPush(url);
    })();
  }, []);

  // A tap on a push while the app is already running. The cold-start case is
  // handled by the connect screen instead — see lib/pendingRoute.ts.
  useEffect(
    () =>
      pendingRoute.subscribe(() => {
        const path = pendingRoute.consume();
        // `navigate`, not `push`: tapping three notifications should not leave
        // three chat screens stacked on top of each other.
        if (path) router.navigate(path);
      }),
    [],
  );

  return (
    <KeyboardProvider>
      <View style={s.root}>
        <StatusBar style="light" />
        <Stack
          screenOptions={{
            headerStyle: { backgroundColor: "#000" },
            headerTintColor: "#fff",
            headerTitleStyle: { fontWeight: "300" },
            contentStyle: { backgroundColor: "#000" },
            animation: "fade",
          }}
        >
          <Stack.Screen name="index" options={{ title: "YOUR OWN" }} />
          <Stack.Screen name="chat" options={{ title: "CHAT" }} />
          <Stack.Screen name="dashboard/index" options={{ title: "" }} />
          <Stack.Screen name="dashboard/self" options={{ title: "SELF" }} />
          <Stack.Screen name="dashboard/settings" options={{ title: "SETTINGS" }} />
          <Stack.Screen name="dashboard/journal" options={{ title: "JOURNAL" }} />
        </Stack>
        <InAppNotification />
      </View>
    </KeyboardProvider>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#000" },
});
