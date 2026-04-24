import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { View, StyleSheet } from "react-native";
import { KeyboardProvider } from "react-native-keyboard-controller";
import { setupPushNotifications } from "@/lib/push";
import InAppNotification from "@/components/InAppNotification";

export default function RootLayout() {
  useEffect(() => {
    setupPushNotifications().catch(console.warn);
  }, []);

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
