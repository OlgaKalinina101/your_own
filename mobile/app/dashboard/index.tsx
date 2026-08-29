/**
 * Dashboard — tile grid matching the desktop layout.
 */
import { useRouter } from "expo-router";
import React, { useEffect, useRef } from "react";
import {
  Animated,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { loadSettings } from "@/lib/api";
import { useResource } from "@/lib/useResource";

// ── Single tile ───────────────────────────────────────────────────────────────

function Tile({
  label,
  sub,
  onPress,
  style,
  delay = 0,
  disabled = false,
}: {
  label: string;
  sub?: string;
  onPress?: () => void;
  style?: object;
  delay?: number;
  disabled?: boolean;
}) {
  const opacity = useRef(new Animated.Value(0)).current;
  const translateY = useRef(new Animated.Value(8)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(opacity, {
        toValue: 1,
        duration: 420,
        delay,
        useNativeDriver: true,
      }),
      Animated.timing(translateY, {
        toValue: 0,
        duration: 420,
        delay,
        useNativeDriver: true,
      }),
    ]).start();
  }, []);

  return (
    <Animated.View style={[{ opacity, transform: [{ translateY }] }, style]}>
      <TouchableOpacity
        style={[sty.tile, disabled && sty.tileDisabled]}
        onPress={disabled ? undefined : onPress}
        activeOpacity={disabled ? 1 : 0.65}
      >
        <Text style={sty.tileLabel}>{label}</Text>
        {sub ? <Text style={sty.tileSub}>{sub}</Text> : null}
      </TouchableOpacity>
    </Animated.View>
  );
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export default function DashboardScreen() {
  const router = useRouter();

  // The dashboard is navigation, so a sleeping backend must not take it away —
  // but it also must not pretend. The tiles stay; the name falls back and a
  // line at the bottom says why. Before this the failure was a bare
  // `.catch(() => {})` and the tile silently read "CHAT" forever.
  const { resource, reload } = useResource(loadSettings, []);
  const aiName =
    resource.status === "ready" && resource.data.ai_name
      ? resource.data.ai_name.toUpperCase()
      : "CHAT";

  return (
    <SafeAreaView style={sty.root}>
      <ScrollView contentContainerStyle={sty.container} showsVerticalScrollIndicator={false}>

        {/* ── Row 1: large CHAT + small stack ─────────────────────────── */}
        <View style={sty.row}>
          <Tile
            label={aiName}
            style={sty.tileLarge}
            onPress={() => router.navigate("/chat")}
            delay={60}
          />
          <View style={sty.colStack}>
            <Animated.View style={[sty.tileSmall, sty.tileDeco]} />
            <Animated.View style={[sty.tileSmall, sty.tileDeco]} />
          </View>
        </View>

        {/* ── Row 2: small deco + large HABITAT ───────────────────────── */}
        <View style={sty.row}>
          <View style={sty.colStack}>
            <Animated.View style={[sty.tileSmall, sty.tileDeco]} />
            <Animated.View style={[sty.tileSmall, sty.tileDeco]} />
          </View>
          <Tile
            label="HABITAT"
            sub="COMING SOON"
            style={sty.tileLarge}
            delay={160}
            disabled
          />
        </View>

        {/* ── Row 3: SELF + SETTINGS ───────────────────────────────────── */}
        <View style={sty.row}>
          <Tile
            label="SELF"
            style={sty.tileMid}
            onPress={() => router.push("/dashboard/self")}
            delay={240}
          />
          <Tile
            label="SETTINGS"
            style={sty.tileMid}
            onPress={() => router.push("/dashboard/settings")}
            delay={300}
          />
        </View>

        {resource.status === "error" ? (
          <TouchableOpacity onPress={reload} style={sty.offlineRow} activeOpacity={0.6}>
            <Text style={sty.offlineText}>{resource.message}</Text>
            <Text style={sty.offlineRetry}>tap to retry</Text>
          </TouchableOpacity>
        ) : null}

      </ScrollView>
    </SafeAreaView>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const GAP = 8;

const sty = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#000" },
  container: { padding: 16, gap: GAP },

  row: {
    flexDirection: "row",
    gap: GAP,
  },

  // Large tile — 2/3 width
  tileLarge: {
    flex: 2,
    height: 180,
  },

  // Mid tile — equal halves
  tileMid: {
    flex: 1,
    height: 140,
  },

  // Stack column — 1/3 width, fills row height
  colStack: {
    flex: 1,
    gap: GAP,
  },

  // Small decorative square inside the stack column
  tileSmall: {
    flex: 1,
    minHeight: 86,
  },

  tile: {
    flex: 1,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.15)",
    backgroundColor: "#000",
    padding: 16,
    justifyContent: "flex-end",
  },

  tileDisabled: {
    borderColor: "rgba(255,255,255,0.08)",
  },

  tileDeco: {
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.08)",
    backgroundColor: "#000",
  },

  offlineRow: {
    marginTop: 16,
    paddingVertical: 10,
    alignItems: "center",
    gap: 6,
  },
  offlineText: {
    color: "rgba(255,255,255,0.4)",
    fontSize: 12,
    fontWeight: "300",
    textAlign: "center",
  },
  offlineRetry: {
    color: "rgba(255,255,255,0.25)",
    fontSize: 8,
    letterSpacing: 3,
    textTransform: "uppercase",
  },

  tileLabel: {
    color: "rgba(255,255,255,0.75)",
    fontSize: 11,
    letterSpacing: 4,
    textTransform: "uppercase",
  },

  tileSub: {
    color: "rgba(255,255,255,0.25)",
    fontSize: 8,
    letterSpacing: 3,
    textTransform: "uppercase",
    marginTop: 5,
  },
});
