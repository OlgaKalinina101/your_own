/**
 * One line of text, scrolling right to left, forever.
 *
 * The other half of the duplication `jscpd` found: `TickerRow` in
 * `dashboard/self.tsx` and the bar in `WorkbenchTicker.tsx` were the same
 * animation with different props, down to the hidden copy used to measure the
 * text.
 *
 * That hidden copy is not incidental. `Animated` needs a number to move to, so
 * the width of the text has to be known before it can scroll, and the only way
 * to learn it is to lay the text out once and ask. `onTextLayout` gives the
 * width of the first line, which for a single line is the width of the text.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Animated,
  Easing,
  StyleSheet,
  Text,
  View,
  type NativeSyntheticEvent,
  type StyleProp,
  type TextLayoutEventData,
  type TextStyle,
  type ViewStyle,
} from "react-native";

export default function Marquee({
  text,
  speed,
  delay = 0,
  active = true,
  style,
  textStyle,
}: {
  text: string;
  /** Pixels per second. */
  speed: number;
  /** Start this far into the cycle, so a stack of rows is not in lockstep. */
  delay?: number;
  /** Off means stopped and reset — a bar that is closed should not animate. */
  active?: boolean;
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
}) {
  const scrollX = useRef(new Animated.Value(0)).current;
  const animRef = useRef<Animated.CompositeAnimation | null>(null);
  const [containerW, setContainerW] = useState(0);
  const [textW, setTextW] = useState(0);

  useEffect(() => {
    animRef.current?.stop();
    if (!active || !text || containerW === 0 || textW === 0) return;

    const totalDistance = containerW + textW;
    const fullDuration = (totalDistance / speed) * 1000;

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
      // Not a pause before starting — a jump to a point partway through, so
      // seven rows opening at once are already spread out rather than all
      // beginning from the right edge together.
      const offset = ((delay / 1000) * speed) % totalDistance;
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

    return () => {
      animRef.current?.stop();
    };
  }, [active, text, containerW, textW, speed, delay, scrollX]);

  const onTextLayout = useCallback((event: NativeSyntheticEvent<TextLayoutEventData>) => {
    const lines = event.nativeEvent.lines;
    if (lines && lines.length > 0) setTextW(Math.ceil(lines[0].width));
  }, []);

  return (
    <View style={[s.row, style]} onLayout={(e) => setContainerW(e.nativeEvent.layout.width)}>
      {/* Off-screen, unclipped, unwrapped: exists only to be measured. */}
      <Text style={[textStyle, s.measurer]} onTextLayout={onTextLayout}>
        {text}
      </Text>

      <Animated.Text
        style={[
          textStyle,
          {
            // Before the measurement lands, wide enough not to wrap; after it,
            // the real width plus a little tail so the loop does not butt up.
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

const s = StyleSheet.create({
  row: {
    overflow: "hidden",
    justifyContent: "center",
  },
  measurer: {
    position: "absolute",
    opacity: 0,
    top: -9999,
    left: 0,
    width: 99999,
  },
});
