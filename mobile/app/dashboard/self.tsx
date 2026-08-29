/**
 * Self — avatar + journal shortcut + inspiration tickers  |  identity viewer.
 *
 * Two ambient toggles in the top-right corner switch between:
 *   WB  — avatar silhouette, JOURNAL card, scrolling inspiration lines
 *   ID  — collapsible identity document
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
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
import type { NativeSyntheticEvent, TextLayoutEventData } from "react-native";
import { useRouter } from "expo-router";
import { getBackendUrl, loadWorkbenchEntries, loadIdentity, loadInspirationFacts } from "@/lib/api";
import { buildChatImageSource } from "@/lib/chatImages";

const NGROK_HEADER = { "ngrok-skip-browser-warning": "true" };
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

// ── Collapsible identity section ────────────────────────────────────────────

function IdentitySection({
  heading,
  body,
  initialOpen,
}: {
  heading: string;
  body: string;
  initialOpen: boolean;
}) {
  const [open, setOpen] = useState(initialOpen);
  const anim = useRef(new Animated.Value(initialOpen ? 1 : 0)).current;

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
    <View style={sty.idSection}>
      <TouchableOpacity
        onPress={toggle}
        activeOpacity={0.6}
        style={sty.idHeadingRow}
      >
        <Animated.Text
          style={[
            sty.idChevron,
            { transform: [{ rotate: chevronRotate }] },
          ]}
        >
          ›
        </Animated.Text>
        <Text style={sty.idHeading}>{heading}</Text>
      </TouchableOpacity>
      {body ? (
        <Animated.View
          style={{ maxHeight: bodyMaxHeight, opacity: bodyOpacity, overflow: "hidden" }}
        >
          <FormattedBody body={body} />
        </Animated.View>
      ) : null}
    </View>
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
          <IdentitySection
            key={i}
            heading={heading}
            body={body}
            initialOpen={false}
          />
        );
      })}
    </Animated.ScrollView>
  );
}

// ── Single marquee ticker row ────────────────────────────────────────────────

function TickerRow({ text, speed, delay = 0 }: { text: string; speed: number; delay?: number }) {
  const scrollX = useRef(new Animated.Value(0)).current;
  const animRef = useRef<Animated.CompositeAnimation | null>(null);
  const started = useRef(false);
  const [containerW, setContainerW] = useState(0);
  const [textW, setTextW] = useState(0);

  useEffect(() => {
    animRef.current?.stop();
    started.current = false;
    if (containerW === 0 || textW === 0) return;

    const totalDist = containerW + textW;
    const fullDuration = (totalDist / speed) * 1000;

    const startLoop = () => {
      scrollX.setValue(containerW);
      animRef.current = Animated.loop(
        Animated.timing(scrollX, {
          toValue: -textW,
          duration: fullDuration,
          easing: Easing.linear,
          useNativeDriver: true,
        }),
      );
      animRef.current.start();
    };

    if (delay <= 0) {
      startLoop();
    } else {
      const offset = ((delay / 1000) * speed) % totalDist;
      scrollX.setValue(containerW - offset);
      const remaining = ((containerW - offset + textW) / speed) * 1000;

      animRef.current = Animated.timing(scrollX, {
        toValue: -textW,
        duration: remaining,
        easing: Easing.linear,
        useNativeDriver: true,
      });
      animRef.current.start(({ finished }) => {
        if (finished) startLoop();
      });
    }

    return () => { animRef.current?.stop(); };
  }, [containerW, textW, speed, delay]);

  const handleTextLayout = (e: NativeSyntheticEvent<TextLayoutEventData>) => {
    const lines = e.nativeEvent.lines;
    if (lines && lines.length > 0) {
      setTextW(Math.ceil(lines[0].width));
    }
  };

  return (
    <View
      style={sty.tickerRow}
      onLayout={e => setContainerW(e.nativeEvent.layout.width)}
    >
      <Text
        style={[sty.tickerText, sty.tickerMeasurer]}
        onTextLayout={handleTextLayout}
      >
        {text}
      </Text>
      <Animated.Text
        style={[
          sty.tickerText,
          {
            width: textW > 0 ? textW + 40 : 9999,
            transform: [{ translateX: scrollX }],
          },
        ]}
        numberOfLines={1}
      >
        {text}
      </Animated.Text>
    </View>
  );
}

// ── WB tab: avatar + journal card + tickers ─────────────────────────────────

const TICKER_SPEEDS  = [35, 50, 42, 58, 30, 48, 55];
const TICKER_DELAYS  = [0, 4200, 1800, 6500, 3000, 8000, 5200];
const TICKER_COUNT = 7;
const TICKER_ROW_H = 26;
const TICKER_GAP = 6;
const TICKER_AREA_H = TICKER_COUNT * TICKER_ROW_H + (TICKER_COUNT - 1) * TICKER_GAP + 24;

function WbView() {
  const router = useRouter();
  const [latestTs, setLatestTs] = useState<string | null>(null);
  const [inspirations, setInspirations] = useState<string[]>([]);
  const [avatarUri, setAvatarUri] = useState<string | null>(null);
  const fadeIn = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.timing(fadeIn, {
      toValue: 1,
      duration: 500,
      easing: Easing.out(Easing.quad),
      useNativeDriver: true,
    }).start();
  }, []);

  useEffect(() => {
    getBackendUrl().then(base => {
      setAvatarUri(`${base.replace(/\/$/, "")}/api/body/anchor.png`);
    });

    loadWorkbenchEntries(0, 1)
      .then(d => {
        if (d.entries.length > 0) {
          const raw = d.entries[0].ts;
          const timePart = raw.includes(" ") ? raw.split(" ").pop()! : raw;
          const hhmm = timePart.slice(0, 5);
          setLatestTs(hhmm);
        }
      })
      .catch(() => {});

    loadInspirationFacts()
      .then(facts => {
        if (facts.length > 0) {
          setInspirations(facts.map(f => f.text));
        }
      })
      .catch(() => {});
  }, []);

  const tickerLines = inspirations.length > 0
    ? inspirations.slice(0, 7)
    : [];

  const avatarW = SCREEN_W;
  const avatarH = avatarW * (4 / 3);

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
        {avatarUri ? (
          <Image
            source={buildChatImageSource(avatarUri, "") ?? { uri: avatarUri, headers: NGROK_HEADER }}
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
          <TickerRow
            key={i}
            text={line}
            speed={TICKER_SPEEDS[i % TICKER_SPEEDS.length]}
            delay={TICKER_DELAYS[i % TICKER_DELAYS.length]}
          />
        ))}
      </View>
    </Animated.View>
  );
}

// ── Main screen ──────────────────────────────────────────────────────────────

export default function SelfScreen() {
  const [tab, setTab] = useState<Tab>("wb");

  // Identity state
  const [identity, setIdentity] = useState<string>("");

  useEffect(() => {
    if (tab === "id" && !identity) {
      loadIdentity()
        .then(d => setIdentity(d.text))
        .catch(err => console.warn("[self] identity fetch failed:", err));
    }
  }, [tab, identity]);

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

      {tab === "wb" ? <WbView /> : <IdentityView text={identity} />}
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
    overflow: "hidden",
    justifyContent: "center",
    paddingHorizontal: 16,
  },
  tickerText: {
    color: "rgba(255,255,255,0.3)",
    fontSize: 11,
    fontWeight: "300",
    letterSpacing: 1,
    textTransform: "uppercase",
  },
  tickerMeasurer: {
    position: "absolute",
    opacity: 0,
    top: -9999,
    left: 0,
    width: 99999,
  },

  // ── Identity ────────────────────────────────────────────────────────────
  idScroll: { flex: 1 },
  idContent: {
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 40,
  },
  idSection: {
    marginBottom: 12,
  },
  idHeadingRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 10,
  },
  idChevron: {
    color: "rgba(255,255,255,0.3)",
    fontSize: 14,
    marginRight: 8,
    fontWeight: "300",
  },
  idHeading: {
    color: "rgba(255,255,255,0.35)",
    fontSize: 10,
    letterSpacing: 4,
    textTransform: "uppercase",
    fontWeight: "500",
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
