import * as Clipboard from "expo-clipboard";
import React, { useCallback, useMemo, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";

import ChatMessageImages from "@/components/ChatMessageImages";
import ChatMessageMemory from "@/components/ChatMessageMemory";
import MessageContent from "@/components/MessageContent";
import { resolveChatImageUri } from "@/lib/chatImages";
import type { Message } from "@/lib/types";

function formatTime(iso?: string): string | null {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return null;
  }
}

export default React.memo(function ChatMessageBubble({
  msg,
  isStreamingLast,
  backendUrl,
}: {
  msg: Message;
  isStreamingLast: boolean;
  backendUrl: string;
}) {
  const isUser = msg.role === "user";
  const [copied, setCopied] = useState(false);

  const imageUrls = useMemo(
    () =>
      (msg.imageUrls ?? (msg.imageUrl ? [msg.imageUrl] : []))
        .filter((uri): uri is string => Boolean(resolveChatImageUri(uri, backendUrl))),
    [backendUrl, msg.imageUrl, msg.imageUrls],
  );
  const hasMemory = !isUser && (msg.chromaFacts?.length ?? 0) > 0;
  const timeLabel = useMemo(() => formatTime(msg.createdAt), [msg.createdAt]);
  const hasContent = Boolean(msg.content) || (msg.role === "assistant" && isStreamingLast);

  const handleCopy = useCallback(async () => {
    if (!msg.content) return;
    await Clipboard.setStringAsync(msg.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  }, [msg.content]);

  return (
    <View style={[s.wrap, isUser ? s.wrapRight : s.wrapLeft]}>
      <View style={[s.bubble, isUser ? s.bubbleUser : s.bubbleAssistant]}>
        <ChatMessageImages imageUrls={imageUrls} backendUrl={backendUrl} />
        {hasContent ? (
          <MessageContent
            content={msg.content}
            role={msg.role}
            isStreaming={isStreamingLast}
            showCursor={isStreamingLast && !isUser}
            backendUrl={backendUrl}
          />
        ) : null}
      </View>

      {/* Footer: time + copy */}
      <View style={[s.footer, isUser ? s.footerRight : s.footerLeft]}>
        {timeLabel ? (
          <Text style={s.time}>{timeLabel}</Text>
        ) : null}
        {msg.interrupted ? (
          // Why this reply is short. A field rather than text inside the
          // message, so the next sync with the server does not wipe it — see
          // lib/mergeMessages.ts.
          <Text style={s.interrupted}>
            {msg.interrupted === "stopped" ? "остановлено" : "оборвано"}
          </Text>
        ) : null}
        {hasContent && !isStreamingLast ? (
          <Pressable
            onPress={handleCopy}
            hitSlop={8}
            style={({ pressed }) => [s.copyBtn, pressed && s.copyBtnPressed]}
          >
            <Text style={[s.copyText, copied && s.copyTextDone]}>
              {copied ? "copied" : "copy"}
            </Text>
          </Pressable>
        ) : null}
      </View>

      {hasMemory ? <ChatMessageMemory chromaFacts={msg.chromaFacts ?? []} /> : null}
    </View>
  );
});

const s = StyleSheet.create({
  wrap: { marginBottom: 16, maxWidth: "85%" },
  wrapRight: { alignSelf: "flex-end" },
  wrapLeft: { alignSelf: "flex-start" },
  bubble: { borderRadius: 0, padding: 12 },
  bubbleUser: {
    backgroundColor: "rgba(255,255,255,0.06)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.12)",
  },
  bubbleAssistant: { backgroundColor: "transparent" },
  footer: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 5,
    gap: 10,
  },
  footerRight: { justifyContent: "flex-end" },
  footerLeft: { justifyContent: "flex-start" },
  time: {
    color: "rgba(255,255,255,0.2)",
    fontSize: 10,
    letterSpacing: 0.5,
    fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace",
  },
  interrupted: {
    color: "rgba(210,150,90,0.5)",
    fontSize: 10,
    letterSpacing: 1.5,
    textTransform: "uppercase",
  },
  copyBtn: { paddingVertical: 2 },
  copyBtnPressed: { opacity: 0.5 },
  copyText: {
    color: "rgba(255,255,255,0.2)",
    fontSize: 10,
    letterSpacing: 1.5,
    textTransform: "uppercase",
  },
  copyTextDone: {
    color: "rgba(120,220,120,0.5)",
  },
});
