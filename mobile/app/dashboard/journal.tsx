/**
 * Journal — full workbench entry history.
 *
 * Each entry is a collapsible section with the timestamp as header.
 * Tap to expand/collapse. All collapsed by default.
 */
import React, { useCallback, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  SafeAreaView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { loadWorkbenchEntries } from "@/lib/api";
import Collapsible from "@/components/Collapsible";
import { Empty, Loading, LoadFailed } from "@/components/ScreenState";
import { useResource } from "@/lib/useResource";

// ── Types ────────────────────────────────────────────────────────────────────

type WbEntry = { ts: string; text: string };

// ── Main screen ──────────────────────────────────────────────────────────────

const PAGE_SIZE = 50;

export default function JournalScreen() {
  // Paging state lives in refs, not state, and that is the fix rather than a
  // style choice: the old `if (loading) return` read a state flag that is still
  // the previous value inside one tick, so two scroll events in the same frame
  // both passed it and both fetched — the same page twice, appended twice. The
  // desktop hit this exact bug in its history loader and fixed it the same way.
  const offsetRef = useRef(0);
  const hasMoreRef = useRef(true);
  const loadingMoreRef = useRef(false);
  const [loadingMore, setLoadingMore] = useState(false);

  const { resource, reload, update } = useResource<WbEntry[]>(async () => {
    const data = await loadWorkbenchEntries(0, PAGE_SIZE);
    offsetRef.current = data.entries.length;
    hasMoreRef.current = data.has_more;
    return data.entries;
  }, []);

  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current || !hasMoreRef.current) return;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    try {
      const data = await loadWorkbenchEntries(offsetRef.current, PAGE_SIZE);
      offsetRef.current += data.entries.length;
      hasMoreRef.current = data.has_more;
      update((current) => [...current, ...data.entries]);
    } catch (error) {
      // The first page is the one that must not lie about being empty; a failed
      // page two just leaves what is already on screen and can be tried again
      // by scrolling.
      console.warn("[journal] could not load more:", error);
      hasMoreRef.current = false;
    } finally {
      loadingMoreRef.current = false;
      setLoadingMore(false);
    }
  }, [update]);

  if (resource.status === "loading") {
    return (
      <SafeAreaView style={sty.root}>
        <Loading />
      </SafeAreaView>
    );
  }

  if (resource.status === "error") {
    return (
      <SafeAreaView style={sty.root}>
        <LoadFailed message={resource.message} onRetry={reload} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={sty.root}>
      <FlatList
        data={resource.data}
        keyExtractor={(entry, index) => `${entry.ts}-${index}`}
        renderItem={({ item }) => (
          <Collapsible title={item.ts}>
            <Text style={sty.entryText}>{item.text}</Text>
          </Collapsible>
        )}
        contentContainerStyle={sty.scrollContent}
        showsVerticalScrollIndicator={false}
        // onEndReached, not onMomentumScrollEnd: the old handler never fired
        // when someone dragged slowly to the bottom without flinging, so the
        // list simply stopped growing and looked finished.
        onEndReached={loadMore}
        onEndReachedThreshold={0.4}
        ListEmptyComponent={<Empty text="nothing written yet" />}
        ListFooterComponent={
          loadingMore ? (
            <View style={sty.loaderWrap}>
              <ActivityIndicator size="small" color="rgba(255,255,255,0.4)" />
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
  scrollContent: {
    flexGrow: 1,
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 40,
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
});
