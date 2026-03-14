/**
 * In-app push notification banner with markdown rendering.
 *
 * Slides down from the top when a push arrives while the app is
 * foregrounded. Auto-dismisses after 6 seconds; tap navigates to chat.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Animated,
  Platform,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import Markdown from "react-native-markdown-display";
import { router } from "expo-router";
import { onPush, offPush } from "@/lib/push";

const DISMISS_MS = 6000;

const mdStyles = StyleSheet.create({
  body: { color: "rgba(255,255,255,0.85)", fontSize: 14, lineHeight: 20 },
  paragraph: { marginTop: 0, marginBottom: 4 },
  strong: { fontWeight: "600" as const, color: "#fff" },
  em: { fontStyle: "italic" as const },
  link: { color: "rgba(100,180,255,0.9)" },
  code_inline: {
    backgroundColor: "rgba(255,255,255,0.1)",
    color: "rgba(255,200,100,0.9)",
    fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace",
    fontSize: 12,
    paddingHorizontal: 3,
    borderRadius: 2,
  },
});

export default function InAppNotification() {
  const [visible, setVisible] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const slideAnim = useRef(new Animated.Value(-200)).current;
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const dismiss = useCallback(() => {
    Animated.timing(slideAnim, {
      toValue: -200,
      duration: 250,
      useNativeDriver: true,
    }).start(() => setVisible(false));
  }, [slideAnim]);

  const show = useCallback(
    (data: { title?: string; body?: string; message?: string }) => {
      const t = data.title || "";
      const b = data.body || data.message || "";
      if (!b) return;

      setTitle(t);
      setBody(b);
      setVisible(true);
      slideAnim.setValue(-200);

      Animated.spring(slideAnim, {
        toValue: 0,
        useNativeDriver: true,
        tension: 60,
        friction: 10,
      }).start();

      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(dismiss, DISMISS_MS);
    },
    [slideAnim, dismiss],
  );

  useEffect(() => {
    onPush(show);
    return () => {
      offPush(show);
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [show]);

  const handleTap = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    dismiss();
    router.push("/chat");
  };

  if (!visible) return null;

  return (
    <Animated.View
      style={[s.container, { transform: [{ translateY: slideAnim }] }]}
    >
      <TouchableOpacity
        style={s.inner}
        activeOpacity={0.85}
        onPress={handleTap}
      >
        {title ? <Text style={s.title} numberOfLines={1}>{title}</Text> : null}
        <View style={s.bodyWrap}>
          <Markdown style={mdStyles}>{body}</Markdown>
        </View>
      </TouchableOpacity>
    </Animated.View>
  );
}

const s = StyleSheet.create({
  container: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    zIndex: 9999,
    paddingTop: Platform.OS === "ios" ? 50 : 36,
    paddingHorizontal: 12,
    paddingBottom: 8,
  },
  inner: {
    backgroundColor: "rgba(30,30,30,0.96)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.1)",
    borderRadius: 14,
    paddingHorizontal: 16,
    paddingVertical: 12,
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.5,
    shadowRadius: 12,
    elevation: 10,
  },
  title: {
    color: "rgba(255,255,255,0.5)",
    fontSize: 10,
    letterSpacing: 2,
    textTransform: "uppercase",
    marginBottom: 4,
  },
  bodyWrap: {
    maxHeight: 100,
    overflow: "hidden",
  },
});
