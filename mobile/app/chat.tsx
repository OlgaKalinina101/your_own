/**
 * Chat screen with SSE streaming, inverted FlatList, and markdown rendering.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  SafeAreaView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
// Note: Markdown rendering is handled by MessageContent component
import { apiFetch, apiFetchStreaming } from "@/lib/api";
import type { HistoryPair, Message } from "@/lib/types";
import MessageContent from "@/components/MessageContent";

const HISTORY_BATCH = 25;

function makeId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function pairToMessages(pair: HistoryPair): Message[] {
  const out: Message[] = [];
  if (pair.user_text) {
    out.push({ id: `${pair.pair_id}-user`, role: "user", content: pair.user_text, pairId: pair.pair_id });
  }
  if (pair.assistant_text) {
    out.push({ id: `${pair.pair_id}-assistant`, role: "assistant", content: pair.assistant_text, pairId: pair.pair_id });
  }
  return out;
}

const MessageBubble = React.memo(function MessageBubble({
  msg,
  isStreamingLast,
}: {
  msg: Message;
  isStreamingLast: boolean;
}) {
  const isUser = msg.role === "user";
  return (
    <View style={[styles.bubbleWrap, isUser ? styles.bubbleWrapRight : styles.bubbleWrapLeft]}>
      <View style={[styles.bubble, isUser ? styles.bubbleUser : styles.bubbleAssistant]}>
        <MessageContent
          content={msg.content}
          role={msg.role}
          isStreaming={isStreamingLast}
          showCursor={isStreamingLast && !isUser}
        />
      </View>
    </View>
  );
});

export default function ChatScreen() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [cursor, setCursor] = useState<string | null>(null);
  const [initialLoaded, setInitialLoaded] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const chunkBufRef = useRef("");
  const rafRef = useRef<ReturnType<typeof requestAnimationFrame> | null>(null);
  const flatListRef = useRef<FlatList<Message>>(null);

  // Inverted FlatList: data must be newest-first
  const reversedMessages = useMemo(() => [...messages].reverse(), [messages]);

  const loadHistory = useCallback(async (before?: string | null) => {
    if (loadingHistory) return;
    setLoadingHistory(true);
    try {
      const params = new URLSearchParams({
        account_id: "default",
        limit_pairs: String(HISTORY_BATCH),
      });
      if (before) params.set("before", before);

      const res = await apiFetch(`/api/chat/history?${params}`);
      if (!res.ok) throw new Error(res.status === 401 ? "auth" : `${res.status}`);

      const data = await res.json() as { pairs: HistoryPair[]; next_before?: string | null; has_more: boolean };
      const loaded = data.pairs.flatMap(pairToMessages);

      setMessages(prev => before ? [...loaded, ...prev] : loaded);
      setCursor(data.next_before ?? null);
      setHasMore(Boolean(data.has_more));
      if (!before) setInitialLoaded(true);
    } catch (err) {
      console.warn("[chat] loadHistory error:", err);
      if (!before) setInitialLoaded(true);
    } finally {
      setLoadingHistory(false);
    }
  }, [loadingHistory]);

  useEffect(() => {
    void loadHistory(null);
  }, []);

  const flushChunkBuf = useCallback(() => {
    rafRef.current = null;
    const text = chunkBufRef.current;
    if (!text) return;
    chunkBufRef.current = "";
    setMessages(prev => {
      const updated = [...prev];
      const last = updated[updated.length - 1];
      if (!last || last.role !== "assistant") return prev;
      updated[updated.length - 1] = { ...last, content: last.content + text };
      return updated;
    });
  }, []);

  const scheduleFlush = useCallback(() => {
    if (rafRef.current === null) {
      rafRef.current = requestAnimationFrame(flushChunkBuf);
    }
  }, [flushChunkBuf]);

  const flushNow = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    flushChunkBuf();
  }, [flushChunkBuf]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || streaming) return;

    const userMsg: Message = { id: makeId("user"), role: "user", content: text };
    setMessages(prev => [...prev, userMsg, { id: makeId("assistant"), role: "assistant", content: "" }]);
    setInput("");
    setStreaming(true);

    try {
      abortRef.current = new AbortController();
      const params = new URLSearchParams();
      params.append("messages", JSON.stringify([...messages, userMsg].map(m => ({ role: m.role, content: m.content }))));
      params.append("web_search", "false");
      params.append("account_id", "default");

      const response = await apiFetchStreaming("/api/chat", {
        method: "POST",
        body: params.toString(),
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        signal: abortRef.current.signal,
      });

      if (!response.ok) {
        throw new Error(response.status === 401
          ? "Auth failed — reconnect in Settings"
          : `HTTP ${response.status}`);
      }
      if (!response.body) {
        throw new Error("Streaming not supported");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let sseBuffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = typeof value === "string" ? value : decoder.decode(value, { stream: true });
        sseBuffer += chunk;

        const events = sseBuffer.split("\n\n");
        sseBuffer = events.pop() ?? "";

        for (const event of events) {
          const eventType = event.split("\n").find(l => l.startsWith("event: "))?.slice(7).trim();
          const dataLines = event.split("\n").filter(l => l.startsWith("data: ")).map(l => l.slice(6));
          if (!dataLines.length) continue;
          const chunk = dataLines.join("\n");
          if (chunk === "[DONE]") break;

          if (eventType === "rewrite") {
            try {
              const { text: newText } = JSON.parse(chunk);
              flushNow();
              setMessages(prev => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                if (last?.role === "assistant") {
                  updated[updated.length - 1] = { ...last, content: newText };
                }
                return updated;
              });
            } catch { /* ignore */ }
            continue;
          }

          if (eventType && ["memory", "skill", "search_start", "search_results",
            "web_start", "web_done", "image_start", "image_ready"].includes(eventType)) {
            continue;
          }

          chunkBufRef.current += chunk;
          scheduleFlush();
        }
      }
      flushNow();
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      flushNow();
      const errMsg = err instanceof Error ? err.message : String(err);
      setMessages(prev => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          const display = errMsg.includes("Auth failed") ? errMsg : `[connection error: ${errMsg}]`;
          updated[updated.length - 1] = { ...last, content: display };
        }
        return updated;
      });
    } finally {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      chunkBufRef.current = "";
      setStreaming(false);
      abortRef.current = null;
    }
  };

  const handleStop = () => {
    abortRef.current?.abort();
    setStreaming(false);
  };

  const renderItem = ({ item, index }: { item: Message; index: number }) => (
    <MessageBubble
      msg={item}
      isStreamingLast={streaming && index === 0}
    />
  );

  if (!initialLoaded) {
    return (
      <SafeAreaView style={styles.root}>
        <ActivityIndicator color="#fff" style={{ marginTop: 40 }} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.root}>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior="padding"
        keyboardVerticalOffset={Platform.OS === "ios" ? 88 : 0}
      >
        <FlatList
          ref={flatListRef}
          data={reversedMessages}
          keyExtractor={m => m.id}
          renderItem={renderItem}
          inverted
          contentContainerStyle={styles.list}
          onEndReached={() => {
            if (hasMore && !loadingHistory) void loadHistory(cursor);
          }}
          onEndReachedThreshold={0.3}
          ListFooterComponent={loadingHistory ? <ActivityIndicator color="#fff" style={{ marginTop: 12 }} /> : null}
          ListEmptyComponent={
            <View style={styles.emptyWrap}>
              <Text style={styles.emptyText}>start typing</Text>
            </View>
          }
        />

        <View style={styles.inputRow}>
          <TextInput
            style={styles.input}
            value={input}
            onChangeText={setInput}
            placeholder="..."
            placeholderTextColor="rgba(255,255,255,0.3)"
            multiline
            onSubmitEditing={handleSend}
            blurOnSubmit={false}
          />
          <TouchableOpacity
            style={styles.sendBtn}
            onPress={streaming ? handleStop : handleSend}
            disabled={!streaming && !input.trim()}
          >
            <Text style={styles.sendBtnText}>{streaming ? "stop" : "send"}</Text>
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#000" },
  list: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 16 },
  emptyWrap: { flex: 1, justifyContent: "center", alignItems: "center", transform: [{ scaleY: -1 }] },
  emptyText: {
    color: "rgba(255,255,255,0.3)",
    textAlign: "center",
    fontSize: 12,
    letterSpacing: 4,
    textTransform: "uppercase",
  },
  bubbleWrap: { marginBottom: 16, maxWidth: "85%" },
  bubbleWrapRight: { alignSelf: "flex-end" },
  bubbleWrapLeft: { alignSelf: "flex-start" },
  bubble: { borderRadius: 0, padding: 12 },
  bubbleUser: { backgroundColor: "rgba(255,255,255,0.06)", borderWidth: 1, borderColor: "rgba(255,255,255,0.12)" },
  bubbleAssistant: { backgroundColor: "transparent" },
  inputRow: {
    flexDirection: "row",
    alignItems: "flex-end",
    borderTopWidth: 1,
    borderTopColor: "rgba(255,255,255,0.1)",
    paddingHorizontal: 16,
    paddingVertical: 10,
    gap: 12,
  },
  input: {
    flex: 1,
    color: "#fff",
    fontSize: 15,
    fontWeight: "300",
    minHeight: 36,
    maxHeight: 140,
    paddingVertical: 8,
    textAlignVertical: "top",
    borderBottomWidth: 1,
    borderBottomColor: "rgba(255,255,255,0.2)",
  },
  sendBtn: { paddingBottom: 6, minWidth: 40, alignItems: "flex-end" },
  sendBtnText: { color: "rgba(255,255,255,0.55)", fontSize: 9, letterSpacing: 4, textTransform: "uppercase" },
});
