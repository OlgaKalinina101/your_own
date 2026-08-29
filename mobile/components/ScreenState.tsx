/**
 * The three things a screen can be showing besides content.
 *
 * They exist as one component because the alternative is what was here before:
 * every screen inventing its own, and three of them inventing nothing at all —
 * `.catch(() => {})` and a black rectangle, which reads as "there is nothing"
 * when it means "I could not ask". On a phone whose backend is a laptop that
 * sleeps, those two are not close to the same thing.
 *
 * Deliberately quiet: the same dim uppercase the rest of the app uses for
 * anything that is not the content itself.
 */
import React from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

export function Loading() {
  return (
    <View style={s.wrap}>
      <ActivityIndicator color="rgba(255,255,255,0.5)" />
    </View>
  );
}

export function LoadFailed({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <View style={s.wrap}>
      <Text style={s.message}>{message}</Text>
      {onRetry ? (
        <Pressable onPress={onRetry} hitSlop={12} style={s.retryBtn}>
          <Text style={s.retryText}>try again</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

export function Empty({ text }: { text: string }) {
  return (
    <View style={s.wrap}>
      <Text style={s.label}>{text}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  wrap: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 32,
    paddingVertical: 40,
  },
  label: {
    color: "rgba(255,255,255,0.3)",
    textAlign: "center",
    fontSize: 12,
    letterSpacing: 4,
    textTransform: "uppercase",
  },
  // Not uppercase and not letterspaced: this one is a sentence from the server
  // ("the backend is up and the database is not"), and it is meant to be read.
  message: {
    color: "rgba(255,255,255,0.45)",
    textAlign: "center",
    fontSize: 13,
    lineHeight: 19,
    fontWeight: "300",
  },
  retryBtn: {
    marginTop: 20,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.2)",
    paddingHorizontal: 18,
    paddingVertical: 8,
  },
  retryText: {
    color: "rgba(255,255,255,0.55)",
    fontSize: 9,
    letterSpacing: 4,
    textTransform: "uppercase",
  },
});
