/**
 * A titled section that opens and closes.
 *
 * Written twice before this, in `dashboard/journal.tsx` and `dashboard/self.tsx`
 * — the same thirty lines, chevron rotation and all, and `jscpd` named them as
 * the largest clone in the app. Both carried the same bug, which is what a
 * duplicate is actually for: `maxHeight` interpolated to a flat `4000`, so a
 * long journal entry or a long identity section was quietly cut off partway.
 *
 * The height is measured instead of guessed. The copy behind `measurer` is laid
 * out off-screen at full size and reports it; the visible one animates to that
 * number. It is the same trick the marquee next door uses to find the width of
 * its text, and the reason both need it is the same: React Native cannot
 * animate to `auto`.
 */
import React, { useCallback, useRef, useState } from "react";
import {
  Animated,
  Easing,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
  type LayoutChangeEvent,
} from "react-native";

const DURATION_MS = 260;

export default function Collapsible({
  title,
  initialOpen = false,
  children,
}: {
  title: string;
  initialOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(initialOpen);
  const [contentHeight, setContentHeight] = useState(0);
  const anim = useRef(new Animated.Value(initialOpen ? 1 : 0)).current;

  const toggle = useCallback(() => {
    const next = !open;
    setOpen(next);
    Animated.timing(anim, {
      toValue: next ? 1 : 0,
      duration: DURATION_MS,
      easing: Easing.out(Easing.quad),
      // Height is not a transform, so this one cannot leave the JS thread.
      useNativeDriver: false,
    }).start();
  }, [open, anim]);

  const onMeasure = useCallback((event: LayoutChangeEvent) => {
    const measured = Math.ceil(event.nativeEvent.layout.height);
    setContentHeight((current) => (current === measured ? current : measured));
  }, []);

  const height = anim.interpolate({
    inputRange: [0, 1],
    outputRange: [0, contentHeight],
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
    <View style={s.section}>
      <TouchableOpacity onPress={toggle} activeOpacity={0.6} style={s.header}>
        <Animated.Text style={[s.chevron, { transform: [{ rotate: chevronRotate }] }]}>
          ›
        </Animated.Text>
        <Text style={s.title}>{title}</Text>
      </TouchableOpacity>

      {/* Off-screen and unclickable: exists only to be measured. */}
      <View style={s.measurer} onLayout={onMeasure} pointerEvents="none">
        {children}
      </View>

      <Animated.View style={{ height, opacity: bodyOpacity, overflow: "hidden" }}>
        {children}
      </Animated.View>
    </View>
  );
}

const s = StyleSheet.create({
  section: { marginBottom: 12 },
  header: {
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
  title: {
    color: "rgba(255,255,255,0.35)",
    fontSize: 10,
    letterSpacing: 4,
    textTransform: "uppercase",
    fontWeight: "500",
  },
  measurer: {
    position: "absolute",
    opacity: 0,
    top: 0,
    left: 0,
    right: 0,
  },
});
