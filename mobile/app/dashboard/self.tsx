/**
 * Self — avatar + journal shortcut + inspiration tickers  |  identity viewer.
 *
 * Two ambient toggles in the top-right corner switch between:
 *   WB  — avatar silhouette, JOURNAL card, scrolling inspiration lines
 *   ID  — collapsible identity document
 */
import React, { useEffect, useRef, useState } from "react";
import {
  Animated,
  Dimensions,
  Easing,
  Image,
  SafeAreaView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { getBackendUrl, loadWorkbenchEntries, loadIdentity, loadInspirationFacts } from "@/lib/api";
import { buildChatImageSource } from "@/lib/chatImages";
import Collapsible from "@/components/Collapsible";
import Marquee from "@/components/Marquee";
import { Empty, Loading, LoadFailed } from "@/components/ScreenState";
import { useResource } from "@/lib/useResource";

const SCREEN_W = Dimensions.get("window").width;

// ── Types ────────────────────────────────────────────────────────────────────

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

// ── Markdown helpers ─────────────────────────────────────────────────────────

type Span = { text: string; bold: boolean };

function parseInlineMarkdown(raw: string): Span[] {
  const spans: Span[] = [];
  const re = /\*\*(.+?)\*\*/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(raw)) !== null) {
    if (m.index > last) spans.push({ text: raw.slice(last, m.index), bold: false });
    spans.push({ text: m[1], bold: true });
    last = m.index + m[0].length;
  }
  if (last < raw.length) spans.push({ text: raw.slice(last), bold: false });
  return spans;
}

function FormattedBody({ body }: { body: string }) {
  const items = body
    .split(/^- /m)
    .map(s => s.trim())
    .filter(Boolean);

  if (items.length === 0) return null;

  return (
    <View>
      {items.map((item, i) => {
        const spans = parseInlineMarkdown(item);
        return (
          <View key={i} style={sty.idListItem}>
            {spans.length === 1 && !spans[0].bold ? (
              <Text style={sty.idBody}>{spans[0].text}</Text>
            ) : (
              <Text style={sty.idBody}>
                {spans.map((sp, j) =>
                  sp.bold ? (
                    <Text key={j} style={sty.idBold}>
                      {sp.text.toUpperCase()}
                    </Text>
                  ) : (
                    <Text key={j}>{sp.text}</Text>
                  ),
                )}
              </Text>
            )}
          </View>
        );
      })}
    </View>
  );
}

// ── Identity section renderer ────────────────────────────────────────────────

function IdentityTab() {
  // Its own resource rather than state in the parent: the tab is lazy, so the
  // request starts when it is opened and its three states belong to it. What
  // was here before caught the failure into `console.warn` and rendered an
  // empty list — a black screen that read as "he has written nothing".
  const { resource, reload } = useResource(() => loadIdentity().then((d) => d.text), []);

  if (resource.status === "loading") return <Loading />;
  if (resource.status === "error") {
    return <LoadFailed message={resource.message} onRetry={reload} />;
  }
  if (!resource.data.trim()) return <Empty text="identity is empty" />;
  return <IdentityView text={resource.data} />;
}

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
          <Collapsible key={i} title={heading}>
            <FormattedBody body={body} />
          </Collapsible>
        );
      })}
    </Animated.ScrollView>
  );
}

// ── WB tab: avatar + journal card + tickers ─────────────────────────────────

const TICKER_SPEEDS  = [35, 50, 42, 58, 30, 48, 55];
const TICKER_DELAYS  = [0, 4200, 1800, 6500, 3000, 8000, 5200];
const TICKER_COUNT = 7;
const TICKER_ROW_H = 26;
const TICKER_GAP = 6;
const TICKER_AREA_H = TICKER_COUNT * TICKER_ROW_H + (TICKER_COUNT - 1) * TICKER_GAP + 24;

interface WbData {
  avatarUri: string;
  latestTs: string | null;
  inspirations: string[];
}

function WbView() {
  const router = useRouter();
  const fadeIn = useRef(new Animated.Value(0)).current;

  // One resource for the whole tab. Two silent `.catch(() => {})` used to live
  // here, so an unreachable backend produced a screen with a blank avatar, a
  // JOURNAL card reading "· · ·" and no tickers — which is also exactly what a
  // reachable backend with nothing written yet produces.
  const { resource, reload } = useResource<WbData>(async () => {
    const [base, workbench, facts] = await Promise.all([
      getBackendUrl(),
      loadWorkbenchEntries(0, 1),
      loadInspirationFacts(),
    ]);
    const raw = workbench.entries[0]?.ts ?? null;
    const timePart = raw ? (raw.includes(" ") ? raw.split(" ").pop()! : raw) : null;
    return {
      avatarUri: `${base.replace(/\/$/, "")}/api/body/anchor.png`,
      latestTs: timePart ? timePart.slice(0, 5) : null,
      inspirations: facts.map((fact) => fact.text),
    };
  }, []);

  useEffect(() => {
    if (resource.status !== "ready") return;
    Animated.timing(fadeIn, {
      toValue: 1,
      duration: 500,
      easing: Easing.out(Easing.quad),
      useNativeDriver: true,
    }).start();
  }, [resource.status, fadeIn]);

  if (resource.status === "loading") return <Loading />;
  if (resource.status === "error") {
    return <LoadFailed message={resource.message} onRetry={reload} />;
  }

  const { avatarUri, latestTs, inspirations } = resource.data;
  const tickerLines = inspirations.slice(0, 7);

  const avatarW = SCREEN_W;
  const avatarH = avatarW * (4 / 3);
  const avatarSource = buildChatImageSource(avatarUri, "");

  return (
    <Animated.View style={[sty.wbRoot, { opacity: fadeIn }]}>
      {/* JOURNAL card — fixed top-left */}
      <TouchableOpacity
        style={sty.journalCard}
        activeOpacity={0.7}
        onPress={() => router.push("/dashboard/journal")}
      >
        <Text style={sty.journalTitle}>JOURNAL</Text>
        <Text style={sty.journalSub}>
          {latestTs ? `LATEST:  ${latestTs}` : "· · ·"}
        </Text>
      </TouchableOpacity>

      {/* Avatar — full width, left-aligned, fills middle zone */}
      <View style={sty.avatarArea}>
        {/* The fallback that used to be here sent no Authorization header, so
            it could only ever render a 401. Behind the token, an image source
            without the header is not a fallback — it is a guaranteed blank. */}
        {avatarSource ? (
          <Image
            source={avatarSource}
            style={{ width: avatarW, height: avatarH }}
            resizeMode="cover"
          />
        ) : (
          <View style={[sty.avatarPlaceholder, { width: avatarW, height: avatarH }]} />
        )}
      </View>

      {/* Ticker area — fixed bottom */}
      <View style={sty.tickerArea}>
        {tickerLines.map((line, i) => (
          <Marquee
            key={i}
            text={line}
            speed={TICKER_SPEEDS[i % TICKER_SPEEDS.length]}
            delay={TICKER_DELAYS[i % TICKER_DELAYS.length]}
            style={sty.tickerRow}
            textStyle={sty.tickerText}
          />
        ))}
      </View>
    </Animated.View>
  );
}

// ── Main screen ──────────────────────────────────────────────────────────────

export default function SelfScreen() {
  const [tab, setTab] = useState<Tab>("wb");

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

      {tab === "wb" ? <WbView /> : <IdentityTab />}
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

  // ── WB tab ──────────────────────────────────────────────────────────────
  wbRoot: {
    flex: 1,
  },

  // Journal card — fixed at top-left
  journalCard: {
    marginLeft: 16,
    marginTop: 4,
    marginBottom: 4,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.15)",
    paddingHorizontal: 14,
    paddingVertical: 10,
    alignSelf: "flex-start",
  },
  journalTitle: {
    color: "rgba(255,255,255,0.75)",
    fontSize: 10,
    letterSpacing: 4,
    fontWeight: "500",
  },
  journalSub: {
    color: "rgba(255,255,255,0.25)",
    fontSize: 8,
    letterSpacing: 2,
    fontWeight: "300",
    marginTop: 4,
  },

  // Avatar — full width, top-aligned, fills available space
  avatarArea: {
    flex: 1,
    alignItems: "flex-start",
    justifyContent: "flex-start",
    overflow: "hidden",
  },
  avatarPlaceholder: {
    backgroundColor: "rgba(255,255,255,0.02)",
  },

  // ── Tickers — fixed height so layout doesn't shift on load ────────────
  tickerArea: {
    height: TICKER_AREA_H,
    paddingBottom: 16,
    paddingTop: 8,
    gap: TICKER_GAP,
  },
  tickerRow: {
    height: 26,
    paddingHorizontal: 16,
  },
  tickerText: {
    color: "rgba(255,255,255,0.3)",
    fontSize: 11,
    fontWeight: "300",
    letterSpacing: 1,
    textTransform: "uppercase",
  },

  // ── Identity ────────────────────────────────────────────────────────────
  idScroll: { flex: 1 },
  idContent: {
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 40,
  },
  idListItem: {
    marginBottom: 16,
    paddingLeft: 22,
  },
  idBody: {
    color: "rgba(255,255,255,0.55)",
    fontSize: 13,
    lineHeight: 21,
    fontWeight: "300",
  },
  idBold: {
    color: "rgba(255,255,255,0.7)",
    fontSize: 10,
    letterSpacing: 3,
    textTransform: "uppercase",
    fontWeight: "500",
  },
});
