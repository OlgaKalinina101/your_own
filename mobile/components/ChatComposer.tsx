import React from "react";
import { StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";

import ChatAttachmentStrip from "@/components/ChatAttachmentStrip";
import type { DraftAttachment } from "@/lib/types";

/**
 * Memoised because it sits next to a stream.
 *
 * Every prop it takes is now stable while a reply arrives — `onSend` stopped
 * being rebuilt per frame when `messages` left its dependency list — so without
 * this the TextInput re-rendered dozens of times a second for no reason, and
 * with it the composer stands still.
 */
export default React.memo(function ChatComposer({
  input,
  onChangeInput,
  attachments,
  canAttach,
  canSend,
  streaming,
  backendUrl,
  onPickImages,
  onRemoveAttachment,
  onSend,
  onStop,
}: {
  input: string;
  onChangeInput: (value: string) => void;
  attachments: DraftAttachment[];
  canAttach: boolean;
  canSend: boolean;
  streaming: boolean;
  backendUrl: string;
  onPickImages: () => void;
  onRemoveAttachment: (attachmentId: string) => void;
  onSend: () => void;
  onStop: () => void;
}) {
  return (
    <>
      <ChatAttachmentStrip
        attachments={attachments}
        backendUrl={backendUrl}
        onRemove={onRemoveAttachment}
      />
      <View style={s.row}>
        {canAttach ? (
          <TouchableOpacity
            style={s.attachBtn}
            onPress={onPickImages}
            disabled={attachments.length >= 4}
          >
            <Text style={[s.attachIcon, attachments.length >= 4 && s.attachDisabled]}>⊕</Text>
          </TouchableOpacity>
        ) : null}
        <TextInput
          style={s.input}
          value={input}
          onChangeText={onChangeInput}
          placeholder="..."
          placeholderTextColor="rgba(255,255,255,0.3)"
          multiline
          onSubmitEditing={onSend}
          blurOnSubmit={false}
        />
        <TouchableOpacity style={s.sendBtn} onPress={streaming ? onStop : onSend} disabled={!streaming && !canSend}>
          <Text style={s.sendText}>{streaming ? "stop" : "send"}</Text>
        </TouchableOpacity>
      </View>
    </>
  );
});

const s = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "flex-end",
    borderTopWidth: 1,
    borderTopColor: "rgba(255,255,255,0.1)",
    paddingHorizontal: 16,
    paddingVertical: 10,
    gap: 12,
  },
  attachBtn: { paddingBottom: 6 },
  attachIcon: { color: "rgba(255,255,255,0.4)", fontSize: 22 },
  attachDisabled: { opacity: 0.3 },
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
  sendText: {
    color: "rgba(255,255,255,0.55)",
    fontSize: 9,
    letterSpacing: 4,
    textTransform: "uppercase",
  },
});
