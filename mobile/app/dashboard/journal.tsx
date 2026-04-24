/**
 * Journal — full workbench entry history.
 *
 * Each entry is a collapsible section with the timestamp as header.
 * Tap to expand/collapse. All collapsed by default.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Animated,
  Easing,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { loadWorkbenchEntries } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

type WbEntry = { ts: string; text: string };

// ── Collapsible journal entry ────────────────────────────────────────────────

function JournalEntry({ entry }: { entry: WbEntry }) {
  const [open, setOpen] = useState(false);
  const anim = useRef(new Animated.Value(0)).current;

  const toggle = useCallback(() => {
    const next = !open;
    setOpen(next);
    Animated.timing(anim, {
      toValue: next ? 1 : 0,
      duration: 260,
      easing: Easing.out(Easing.quad),
      useNativeDriver: false,
    }).start();
  }, [open, anim]);

  const bodyMaxHeight = anim.interpolate({
    inputRange: [0, 1],
    outputRange: [0, 4000],
  });

  const bodyOpacity = anim.interpolate({
    inputRange: [0, 0.3, 1],
    outputRange: [0, 0, 1],
  });

  const chevronRotate = anim.interpolate({
    inputRange: [0, 1],
    outputRange: ["0deg", "90deg"],
  });

  return (
    <View style={sty.section}>
      <TouchableOpacity
        onPress={toggle}
        activeOpacity={0.6}
        style={sty.sectionHeader}
      >
        <Animated.Text
          style={[sty.chevron, { transform: [{ rotate: chevronRotate }] }]}
        >
          ›
        </Animated.Text>
        <Text style={sty.sectionTs}>{entry.ts}</Text>
      </TouchableOpacity>

      <Animated.View
        style={{ maxHeight: bodyMaxHeight, opacity: bodyOpacity, overflow: "hidden" }}
      >
        <Text style={sty.entryText}>{entry.text}</Text>
      </Animated.View>
    </View>
  );
}

// ── Main screen ──────────────────────────────────────────────────────────────

const PAGE_SIZE = 50;

export default function JournalScreen() {
  const [entries, setEntries] = useState<WbEntry[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const offsetRef = useRef(0);

  const fetchPage = useCallback(async (reset = false) => {
    if (loading) return;
    if (!reset && !hasMore) return;

    const offset = reset ? 0 : offsetRef.current;
    setLoading(true);
    try {
      const data = await loadWorkbenchEntries(offset, PAGE_SIZE);
      const newEntries = data.entries;

      if (reset) {
        setEntries(newEntries);
      } else {
        setEntries(prev => [...prev, ...newEntries]);
      }
      offsetRef.current = offset + newEntries.length;
      setHasMore(data.has_more);
    } catch (err) {
      console.warn("[journal] workbench fetch failed:", err);
    } finally {
      setLoading(false);
    }
  }, [loading, hasMore]);

  useEffect(() => {
    fetchPage(true);
  }, []);

  const handleLoadMore = useCallback(() => {
    if (!loading && hasMore) fetchPage();
  }, [loading, hasMore, fetchPage]);

  return (
    <SafeAreaView style={sty.root}>
      <ScrollView
        contentContainerStyle={sty.scrollContent}
        showsVerticalScrollIndicator={false}
        onMomentumScrollEnd={(e) => {
          const { layoutMeasurement, contentOffset, contentSize } = e.nativeEvent;
          if (contentOffset.y + layoutMeasurement.height >= contentSize.height - 100) {
            handleLoadMore();
          }
        }}
      >
        {entries.map((entry, i) => (
          <JournalEntry key={`${entry.ts}-${i}`} entry={entry} />
        ))}
        {loading ? (
          <View style={sty.loaderWrap}>
            <Text style={sty.loaderDots}>···</Text>
          </View>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const sty = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#000" },
  scrollContent: {
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 40,
  },

  section: {
    marginBottom: 12,
  },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 10,
  },
  chevron: {
    color: "rgba(255,255,255,0.3)",
    fontSize: 14,
    marginRight: 8,
    fontWeight: "300",
  },
  sectionTs: {
    color: "rgba(255,255,255,0.35)",
    fontSize: 10,
    letterSpacing: 4,
    textTransform: "uppercase",
    fontWeight: "500",
  },

  entryText: {
    color: "rgba(255,255,255,0.55)",
    fontSize: 13,
    lineHeight: 21,
    fontWeight: "300",
    paddingLeft: 22,
    paddingBottom: 8,
  },

  loaderWrap: {
    alignItems: "center",
    paddingVertical: 16,
  },
  loaderDots: {
    color: "rgba(255,255,255,0.2)",
    fontSize: 18,
    letterSpacing: 6,
  },
});
