/**
 * Journal — full workbench entry history.
 *
 * Paginated list (newest at bottom, scroll up for history).
 * Navigated to from the JOURNAL card on the Self WB tab.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Animated,
  Easing,
  FlatList,
  SafeAreaView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { loadWorkbenchEntries } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

type WbEntry = { ts: string; text: string };

// ── Animated workbench entry ─────────────────────────────────────────────────

const ENTRY_ANIM_DURATION = 320;
const ENTRY_STAGGER = 40;

function WbEntryCard({
  item,
  index,
  batchStart,
}: {
  item: WbEntry;
  index: number;
  batchStart: number;
}) {
  const opacity = useRef(new Animated.Value(0)).current;
  const translateY = useRef(new Animated.Value(6)).current;

  useEffect(() => {
    const delay = Math.max(0, index - batchStart) * ENTRY_STAGGER;
    Animated.parallel([
      Animated.timing(opacity, {
        toValue: 1,
        duration: ENTRY_ANIM_DURATION,
        delay,
        easing: Easing.out(Easing.quad),
        useNativeDriver: true,
      }),
      Animated.timing(translateY, {
        toValue: 0,
        duration: ENTRY_ANIM_DURATION,
        delay,
        easing: Easing.out(Easing.quad),
        useNativeDriver: true,
      }),
    ]).start();
  }, []);

  return (
    <Animated.View
      style={[sty.entry, { opacity, transform: [{ translateY }] }]}
    >
      <Text style={sty.entryTs}>{item.ts}</Text>
      <Text style={sty.entryText}>{item.text}</Text>
    </Animated.View>
  );
}

// ── Main screen ──────────────────────────────────────────────────────────────

const PAGE_SIZE = 25;

export default function JournalScreen() {
  const [entries, setEntries] = useState<WbEntry[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const offsetRef = useRef(0);
  const batchStartRef = useRef(0);
  const listRef = useRef<FlatList<WbEntry>>(null);

  const fetchPage = useCallback(async (reset = false) => {
    if (loading) return;
    if (!reset && !hasMore) return;

    const offset = reset ? 0 : offsetRef.current;
    setLoading(true);
    try {
      const data = await loadWorkbenchEntries(offset, PAGE_SIZE);
      const newEntries = data.entries;

      if (reset) {
        batchStartRef.current = 0;
        setEntries(newEntries);
      } else {
        batchStartRef.current = offsetRef.current;
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

  const renderEntry = useCallback(
    ({ item, index }: { item: WbEntry; index: number }) => (
      <WbEntryCard
        item={item}
        index={index}
        batchStart={batchStartRef.current}
      />
    ),
    [],
  );

  const keyExtractor = useCallback(
    (item: WbEntry, index: number) => `${item.ts}-${index}`,
    [],
  );

  const handleEndReached = useCallback(() => {
    if (!loading && hasMore) fetchPage();
  }, [loading, hasMore, fetchPage]);

  return (
    <SafeAreaView style={sty.root}>
      <FlatList
        ref={listRef}
        data={entries}
        renderItem={renderEntry}
        keyExtractor={keyExtractor}
        inverted
        contentContainerStyle={sty.listContent}
        showsVerticalScrollIndicator={false}
        onEndReached={handleEndReached}
        onEndReachedThreshold={0.4}
        ListFooterComponent={
          loading ? (
            <View style={sty.loaderWrap}>
              <Text style={sty.loaderDots}>···</Text>
            </View>
          ) : null
        }
      />
    </SafeAreaView>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const sty = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#000" },
  listContent: {
    paddingHorizontal: 20,
    paddingTop: 24,
    paddingBottom: 8,
  },
  entry: {
    marginBottom: 24,
    borderLeftWidth: 1,
    borderLeftColor: "rgba(255,255,255,0.06)",
    paddingLeft: 14,
  },
  entryTs: {
    color: "rgba(255,255,255,0.2)",
    fontSize: 9,
    letterSpacing: 2,
    marginBottom: 6,
    fontWeight: "300",
  },
  entryText: {
    color: "rgba(255,255,255,0.55)",
    fontSize: 13,
    lineHeight: 20,
    fontWeight: "300",
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
