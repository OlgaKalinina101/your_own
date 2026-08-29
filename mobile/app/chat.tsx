import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Animated, BackHandler, FlatList, Platform, Pressable, StyleSheet, Text, View, type ScrollViewProps } from "react-native";
import { Stack, useFocusEffect, useRouter } from "expo-router";
import { HeaderBackButton } from "@react-navigation/elements";
import {
  KeyboardChatScrollView,
  KeyboardStickyView,
  type KeyboardChatScrollViewProps,
} from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import ChatComposer from "@/components/ChatComposer";
import ChatMessageBubble from "@/components/ChatMessageBubble";
import { WorkbenchDotsBtn, WorkbenchBar } from "@/components/WorkbenchTicker";
import type { Message } from "@/lib/types";
import { useChatController } from "@/lib/useChatController";

type ChatScrollRef = React.ElementRef<typeof KeyboardChatScrollView>;

const ChatScrollView = React.forwardRef<
  ChatScrollRef,
  ScrollViewProps & KeyboardChatScrollViewProps
>(({ inverted, ...props }, ref) => {
  const { bottom } = useSafeAreaInsets();
  return (
    <KeyboardChatScrollView
      ref={ref}
      inverted={inverted}
      automaticallyAdjustContentInsets={false}
      contentInsetAdjustmentBehavior="never"
      keyboardDismissMode="interactive"
      offset={bottom}
      {...props}
    />
  );
});

export default function ChatScreen() {
  const router = useRouter();
  const [workbenchOpen, setWorkbenchOpen] = useState(false);
  const {
    aiName,
    attachments,
    backendUrl,
    canAttach,
    canSend,
    errorNotice,
    historyError,
    initialLoaded,
    input,
    loadingHistory,
    reversedMessages,
    streaming,
    workbenchText,
    refreshSettings,
    refreshWorkbench,
    reloadHistory,
    setInput,
    pickImages,
    removeAttachment,
    sendMessage,
    stopStreaming,
    loadMore,
  } = useChatController();

  const goBack = useCallback(() => {
    if (router.canGoBack()) {
      router.back();
    } else {
      router.replace("/dashboard");
    }
  }, [router]);

  // Android hardware back — single press always exits to dashboard
  useEffect(() => {
    if (Platform.OS !== "android") return;
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      goBack();
      return true;
    });
    return () => sub.remove();
  }, [goBack]);

  // Re-ask for the model and the name whenever this screen is being looked at.
  // Settings sits on top of it in the stack rather than replacing it, so coming
  // back from changing the model remounts nothing.
  useFocusEffect(refreshSettings);

  // Refresh workbench text each time the bar is opened
  useEffect(() => {
    if (workbenchOpen) refreshWorkbench();
  }, [workbenchOpen, refreshWorkbench]);

  // Ambient error fade
  const errorOpacity = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (errorNotice) {
      Animated.sequence([
        Animated.timing(errorOpacity, { toValue: 1, duration: 600, useNativeDriver: true }),
        Animated.delay(2800),
        Animated.timing(errorOpacity, { toValue: 0, duration: 600, useNativeDriver: true }),
      ]).start();
    }
  }, [errorNotice, errorOpacity]);

  const renderScrollComponent = useCallback(
    (props: ScrollViewProps) => <ChatScrollView {...props} />,
    [],
  );

  // Memoised so the bubbles' own React.memo can do its job: a new function
  // here every render is a new prop on every row, and the list re-rendered in
  // full on each frame of a stream.
  const renderItem = useCallback(
    ({ item, index }: { item: Message; index: number }) => (
      <ChatMessageBubble
        msg={item}
        isStreamingLast={streaming && index === 0}
        backendUrl={backendUrl}
      />
    ),
    [streaming, backendUrl],
  );

  if (!initialLoaded) {
    return (
      <View style={styles.root}>
        <Stack.Screen options={{ title: aiName }} />
        <ActivityIndicator color="#fff" style={{ marginTop: 40 }} />
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <Stack.Screen
        options={{
          title: aiName,
          headerLeft: () => (
            <HeaderBackButton onPress={goBack} tintColor="#fff" />
          ),
          headerRight: () => (
            <WorkbenchDotsBtn open={workbenchOpen} onPress={() => setWorkbenchOpen((value) => !value)} />
          ),
        }}
      />
      <WorkbenchBar open={workbenchOpen} text={workbenchText} />

      <FlatList
        data={reversedMessages}
        keyExtractor={(message) => message.id}
        renderItem={renderItem}
        inverted
        renderScrollComponent={renderScrollComponent}
        contentContainerStyle={styles.list}
        onEndReached={loadMore}
        onEndReachedThreshold={0.3}
        ListFooterComponent={loadingHistory ? <ActivityIndicator color="#fff" style={{ marginTop: 12 }} /> : null}
        ListEmptyComponent={
          <View style={styles.emptyWrap}>
            {historyError ? (
              /* An unreachable backend used to render as "start typing", which
                 is the one thing it does not mean. For a phone whose server is
                 a laptop that sleeps, this is the common case, not the edge. */
              <>
                <Text style={styles.emptyText}>{historyError}</Text>
                <Pressable onPress={reloadHistory} hitSlop={12} style={styles.retryBtn}>
                  <Text style={styles.retryText}>try again</Text>
                </Pressable>
              </>
            ) : (
              <Text style={styles.emptyText}>start typing</Text>
            )}
          </View>
        }
        keyboardShouldPersistTaps="handled"
      />

      <KeyboardStickyView style={styles.stickyInput}>
        {errorNotice ? (
          <Animated.View style={[styles.errorBanner, { opacity: errorOpacity }]}>
            <Text style={styles.errorText}>{errorNotice}</Text>
          </Animated.View>
        ) : null}
        <ChatComposer
          input={input}
          onChangeInput={setInput}
          attachments={attachments}
          canAttach={canAttach}
          canSend={canSend}
          streaming={streaming}
          backendUrl={backendUrl}
          onPickImages={pickImages}
          onRemoveAttachment={removeAttachment}
          onSend={sendMessage}
          onStop={stopStreaming}
        />
      </KeyboardStickyView>
    </View>
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
  retryBtn: { marginTop: 18, borderWidth: 1, borderColor: "rgba(255,255,255,0.2)", paddingHorizontal: 18, paddingVertical: 8 },
  retryText: {
    color: "rgba(255,255,255,0.55)",
    fontSize: 9,
    letterSpacing: 4,
    textTransform: "uppercase",
  },
  stickyInput: { backgroundColor: "#000" },
  errorBanner: {
    paddingHorizontal: 20,
    paddingVertical: 7,
  },
  errorText: {
    color: "rgba(255,255,255,0.25)",
    fontSize: 10,
    letterSpacing: 2,
    textTransform: "uppercase",
    textAlign: "center",
  },
});
