/**
 * Self — workbench stream + identity viewer.
 *
 * Two ambient toggles in the top-right corner switch between:
 *   WB  — paginated workbench entries (newest at bottom, scroll up for history)
 *   ID  — raw identity document
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Animated,
  Easing,
  FlatList,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { loadWorkbenchEntries, loadIdentity } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

type WbEntry = { ts: string; text: string };
type Tab = "wb" | "id";

// ── Ambient toggle pill ──────────────────────────────────────────────────────

function TogglePill({
  left,
  right,
  active,
  onToggle,
}: {
  left: string;
  right: string;
  active: Tab;
  onToggle: (t: Tab) => void;
}) {
  const slide = useRef(new Animated.Value(active === "wb" ? 0 : 1)).current;

  useEffect(() => {
    Animated.timing(slide, {
      toValue: active === "wb" ? 0 : 1,
      duration: 220,
      easing: Easing.out(Easing.quad),
      useNativeDriver: false,
    }).start();
  }, [active]);

  const knobLeft = slide.interpolate({
    inputRange: [0, 1],
    outputRange: ["0%", "50%"],
  });

  return (
    <View style={sty.pill}>
      <Animated.View style={[sty.pillKnob, { left: knobLeft }]} />
      <TouchableOpacity
        style={sty.pillHalf}
        activeOpacity={0.7}
        onPress={() => onToggle("wb")}
      >
        <Text
          style={[sty.pillLabel, active === "wb" && sty.pillLabelActive]}
        >
          {left}
        </Text>
      </TouchableOpacity>
      <TouchableOpacity
        style={sty.pillHalf}
        activeOpacity={0.7}
        onPress={() => onToggle("id")}
      >
        <Text
          style={[sty.pillLabel, active === "id" && sty.pillLabelActive]}
        >
          {right}
        </Text>
      </TouchableOpacity>
    </View>
  );
}

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

// ── Identity section renderer ────────────────────────────────────────────────

function IdentityView({ text }: { text: string }) {
  const opacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    opacity.setValue(0);
    Animated.timing(opacity, {
      toValue: 1,
      duration: 400,
      easing: Easing.out(Easing.quad),
      useNativeDriver: true,
    }).start();
  }, [text]);

  const sections = text.split(/^(?=## )/m).filter(Boolean);

  return (
    <Animated.ScrollView
      style={[sty.idScroll, { opacity }]}
      contentContainerStyle={sty.idContent}
      showsVerticalScrollIndicator={false}
    >
      {sections.map((sec, i) => {
        const nlIdx = sec.indexOf("\n");
        const heading = nlIdx > -1 ? sec.slice(0, nlIdx).replace(/^#+\s*/, "") : sec.replace(/^#+\s*/, "");
        const body = nlIdx > -1 ? sec.slice(nlIdx + 1).trim() : "";

        return (
          <View key={i} style={sty.idSection}>
            <Text style={sty.idHeading}>{heading}</Text>
            {body ? <Text style={sty.idBody}>{body}</Text> : null}
          </View>
        );
      })}
    </Animated.ScrollView>
  );
}

// ── Main screen ──────────────────────────────────────────────────────────────

const PAGE_SIZE = 25;

export default function SelfScreen() {
  const [tab, setTab] = useState<Tab>("wb");

  // Workbench state
  const [entries, setEntries] = useState<WbEntry[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const offsetRef = useRef(0);
  const batchStartRef = useRef(0);

  // Identity state
  const [identity, setIdentity] = useState<string>("");

  const listRef = useRef<FlatList<WbEntry>>(null);

  // ── Workbench loader ────────────────────────────────────────────────────

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
      console.warn("[self] workbench fetch failed:", err);
    } finally {
      setLoading(false);
    }
  }, [loading, hasMore]);

  // Initial load
  useEffect(() => {
    fetchPage(true);
  }, []);

  // ── Identity loader ────────────────────────────────────────────────────

  useEffect(() => {
    if (tab === "id" && !identity) {
      loadIdentity()
        .then(d => setIdentity(d.text))
        .catch(err => console.warn("[self] identity fetch failed:", err));
    }
  }, [tab, identity]);

  // ── Render helpers ─────────────────────────────────────────────────────

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

  // ── UI ──────────────────────────────────────────────────────────────────

  return (
    <SafeAreaView style={sty.root}>
      {/* Toggle row */}
      <View style={sty.header}>
        <View style={sty.spacer} />
        <TogglePill
          left="WB"
          right="ID"
          active={tab}
          onToggle={setTab}
        />
      </View>

      {tab === "wb" ? (
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
      ) : (
        <IdentityView text={identity} />
      )}
    </SafeAreaView>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const sty = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#000" },

  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "flex-end",
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 4,
  },
  spacer: { flex: 1 },

  // Toggle pill
  pill: {
    flexDirection: "row",
    width: 96,
    height: 28,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.12)",
    borderRadius: 14,
    overflow: "hidden",
    position: "relative",
  },
  pillKnob: {
    position: "absolute",
    top: 0,
    width: "50%",
    height: "100%",
    backgroundColor: "rgba(255,255,255,0.07)",
    borderRadius: 14,
  },
  pillHalf: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  pillLabel: {
    color: "rgba(255,255,255,0.3)",
    fontSize: 9,
    letterSpacing: 3,
    fontWeight: "500",
  },
  pillLabelActive: {
    color: "rgba(255,255,255,0.7)",
  },

  // Workbench list
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

  // Identity
  idScroll: { flex: 1 },
  idContent: {
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 40,
  },
  idSection: {
    marginBottom: 28,
  },
  idHeading: {
    color: "rgba(255,255,255,0.35)",
    fontSize: 10,
    letterSpacing: 4,
    textTransform: "uppercase",
    marginBottom: 10,
    fontWeight: "500",
  },
  idBody: {
    color: "rgba(255,255,255,0.55)",
    fontSize: 13,
    lineHeight: 21,
    fontWeight: "300",
  },
});
