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
  canAttachFiles,
  acceptedDescription,
  canSend,
  streaming,
  backendUrl,
  onPickImages,
  onPickFiles,
  onRemoveAttachment,
  onSend,
  onStop,
}: {
  input: string;
  onChangeInput: (value: string) => void;
  attachments: DraftAttachment[];
  /** Whether this model takes anything at all. */
  canAttach: boolean;
  /** Whether it takes something the camera roll does not hold. */
  canAttachFiles: boolean;
  /** "фото, PDF, аудио" — the screen reader's label for the two icons. */
  acceptedDescription: string;
  canSend: boolean;
  streaming: boolean;
  backendUrl: string;
  onPickImages: () => void;
  onPickFiles: () => void;
  onRemoveAttachment: (attachmentId: string) => void;
  onSend: () => void;
  onStop: () => void;
}) {
  const full = attachments.length >= 4;
  return (
    <>
      <ChatAttachmentStrip
        attachments={attachments}
        backendUrl={backendUrl}
        onRemove={onRemoveAttachment}
      />
      <View style={s.row}>
        {/* Two doors, because Android keeps photographs and files in separate
            places and a picker that opens on the wrong one is a dead end. The
            paperclip is hidden on a model that reads nothing but pictures, so
            it never opens a browser whose every result would be dropped. */}
        {canAttach ? (
          <TouchableOpacity
            style={s.attachBtn}
            onPress={onPickImages}
            accessibilityLabel={`Фото. Эта модель читает: ${acceptedDescription}`}
            disabled={full}
          >
            <Text style={[s.attachIcon, full && s.attachDisabled]}>⊕</Text>
          </TouchableOpacity>
        ) : null}
        {canAttach && canAttachFiles ? (
          <TouchableOpacity
            style={s.attachBtn}
            onPress={onPickFiles}
            accessibilityLabel={`Файл. Эта модель читает: ${acceptedDescription}`}
            disabled={full}
          >
            <Text style={[s.attachIcon, full && s.attachDisabled]}>🖇</Text>
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
