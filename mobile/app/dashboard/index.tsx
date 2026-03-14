/**
 * Dashboard home — navigation hub.
 */
import { useRouter } from "expo-router";
import React from "react";
import {
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";

const TILES = [
  { label: "CHAT", route: "/chat" as const, desc: "Talk to your AI" },
  { label: "SETTINGS", route: "/dashboard/settings" as const, desc: "API key, model, notifications" },
  { label: "MEMORY", route: "/dashboard/memory" as const, desc: "Saved facts & reflections" },
  { label: "SOUL", route: "/dashboard/soul" as const, desc: "AI's core personality" },
];

export default function DashboardScreen() {
  const router = useRouter();

  return (
    <SafeAreaView style={styles.root}>
      <ScrollView contentContainerStyle={styles.container}>
        <Text style={styles.title}>dashboard</Text>
        {TILES.map((tile) => (
          <TouchableOpacity
            key={tile.label}
            style={styles.tile}
            onPress={() => router.push(tile.route)}
            activeOpacity={0.7}
          >
            <View>
              <Text style={styles.tileLabel}>{tile.label}</Text>
              <Text style={styles.tileDesc}>{tile.desc}</Text>
            </View>
            <Text style={styles.tileArrow}>→</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#000" },
  container: { paddingHorizontal: 24, paddingTop: 32, paddingBottom: 40 },
  title: {
    color: "rgba(255,255,255,0.4)",
    fontSize: 9,
    letterSpacing: 5,
    textTransform: "uppercase",
    marginBottom: 32,
  },
  tile: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.1)",
    paddingHorizontal: 20,
    paddingVertical: 18,
    marginBottom: 12,
  },
  tileLabel: {
    color: "rgba(255,255,255,0.75)",
    fontSize: 11,
    letterSpacing: 4,
    textTransform: "uppercase",
    marginBottom: 4,
  },
  tileDesc: {
    color: "rgba(255,255,255,0.35)",
    fontSize: 12,
    fontWeight: "300",
  },
  tileArrow: {
    color: "rgba(255,255,255,0.3)",
    fontSize: 18,
  },
});
