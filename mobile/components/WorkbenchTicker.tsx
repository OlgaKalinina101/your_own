/**
 * WorkbenchBar — collapsible ticker bar showing the latest workbench note.
 *
 * Usage:
 *   // In headerRight:
 *   <WorkbenchDotsBtn open={open} onPress={() => setOpen(v => !v)} />
 *
 *   // In screen body (right below the header):
 *   <WorkbenchBar open={open} text={text} />
 */
import React, { useEffect, useRef } from "react";
import { Animated, Easing, StyleSheet, Text, TouchableOpacity } from "react-native";

import Marquee from "@/components/Marquee";

// ── Dots toggle button (goes in Stack.Screen headerRight) ─────────────────────

export function WorkbenchDotsBtn({
  open,
  onPress,
}: {
  open: boolean;
  onPress: () => void;
}) {
  return (
    <TouchableOpacity onPress={onPress} style={sty.dotsBtn} activeOpacity={0.6}>
      <Text style={[sty.dots, open && sty.dotsOpen]}>•••</Text>
    </TouchableOpacity>
  );
}

// ── Collapsible ticker bar (goes in screen body) ──────────────────────────────

const BAR_H = 64;
const FONT_SIZE = 13;
const SPEED = 60; // px per second

function getEmptyLabel(): string {
  try {
    const locale = typeof Intl !== "undefined" && Intl.DateTimeFormat
      ? new Intl.DateTimeFormat().resolvedOptions().locale
      : "en";
    return locale.toLowerCase().startsWith("ru") ? "Тишина..." : "Silence...";
  } catch {
    return "Silence...";
  }
}

export function WorkbenchBar({
  open,
  text,
}: {
  open: boolean;
  text: string | null;
}) {
  const barH = useRef(new Animated.Value(0)).current;

  // Slide the bar open / closed
  useEffect(() => {
    Animated.timing(barH, {
      toValue: open ? BAR_H : 0,
      duration: 240,
      easing: Easing.out(Easing.quad),
      useNativeDriver: false,
    }).start();
  }, [open, barH]);

  const displayText = text && text.trim() ? text : getEmptyLabel();

  return (
    <Animated.View style={[sty.bar, { height: barH }]}>
      <Marquee
        text={displayText}
        speed={SPEED}
        active={open}
        style={sty.inner}
        textStyle={sty.tickerText}
      />
    </Animated.View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const sty = StyleSheet.create({
  dotsBtn: {
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  dots: {
    color: "rgba(255,255,255,0.28)",
    fontSize: 13,
    letterSpacing: 4,
  },
  dotsOpen: {
    color: "rgba(255,255,255,0.7)",
  },
  bar: {
    backgroundColor: "#000",
    borderBottomWidth: 1,
    borderBottomColor: "rgba(255,255,255,0.07)",
    overflow: "hidden",
  },
  inner: {
    flex: 1,
    paddingHorizontal: 20,
  },
  tickerText: {
    color: "rgba(255,255,255,0.45)",
    fontSize: FONT_SIZE,
    fontWeight: "300",
    fontStyle: "italic",
    letterSpacing: 0.3,
  },
});
