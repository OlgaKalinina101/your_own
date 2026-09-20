import React from "react";
import {
  ActivityIndicator,
  Image,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";

import { buildChatImageSource } from "@/lib/chatImages";
import { kindOf } from "@/lib/modelInputs";
import type { DraftAttachment } from "@/lib/types";

/** The four-letter badge on a non-image attachment: "PDF", "MP3", "TXT". */
function shortType(attachment: DraftAttachment): string {
  const fromName = attachment.fileName.includes(".")
    ? attachment.fileName.split(".").pop() ?? ""
    : "";
  const label = fromName || attachment.mimeType.split("/").pop() || "file";
  return label.slice(0, 4).toUpperCase();
}

export default function ChatAttachmentStrip({
  attachments,
  backendUrl,
  onRemove,
}: {
  attachments: DraftAttachment[];
  backendUrl: string;
  onRemove: (attachmentId: string) => void;
}) {
  if (!attachments.length) return null;

  return (
    <View style={s.strip}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.scroll}>
        {attachments.map((attachment) => {
          // Only a picture has a picture to show. A PDF or a recording rendered
          // through <Image> is a grey square that never loads, so it gets its
          // type and its name instead — which is also what tells two apart.
          const isImage = kindOf(attachment.mimeType) === "image";
          const source = isImage
            ? buildChatImageSource(attachment.serverUrl ?? attachment.localUri, backendUrl)
            : null;
          return (
            <View key={attachment.id} style={s.wrap}>
              {source ? (
                <Image source={source} style={s.thumb} resizeMode="cover" />
              ) : (
                <View style={[s.thumb, s.fallback]}>
                  <Text style={s.fallbackText}>{shortType(attachment)}</Text>
                  <Text style={s.fallbackName} numberOfLines={1}>
                    {attachment.fileName}
                  </Text>
                </View>
              )}
              {attachment.status === "uploading" ? (
                <View style={s.uploading}>
                  <ActivityIndicator size="small" color="#fff" />
                </View>
              ) : null}
              <TouchableOpacity style={s.remove} onPress={() => onRemove(attachment.id)}>
                <Text style={s.removeText}>×</Text>
              </TouchableOpacity>
            </View>
          );
        })}
      </ScrollView>
    </View>
  );
}

const s = StyleSheet.create({
  strip: {
    borderTopWidth: 1,
    borderTopColor: "rgba(255,255,255,0.08)",
    paddingVertical: 8,
  },
  scroll: { paddingHorizontal: 16, gap: 8 },
  wrap: { position: "relative" },
  thumb: {
    width: 64,
    height: 64,
    borderRadius: 2,
    backgroundColor: "rgba(255,255,255,0.04)",
  },
  fallback: {
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.08)",
  },
  fallbackText: {
    color: "rgba(255,255,255,0.55)",
    fontSize: 9,
    letterSpacing: 1,
    textTransform: "uppercase",
  },
  fallbackName: {
    color: "rgba(255,255,255,0.35)",
    fontSize: 7,
    marginTop: 2,
    paddingHorizontal: 3,
  },
  remove: {
    position: "absolute",
    top: -6,
    right: -6,
    width: 18,
    height: 18,
    borderRadius: 9,
    backgroundColor: "rgba(0,0,0,0.8)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.3)",
    alignItems: "center",
    justifyContent: "center",
  },
  removeText: { color: "#fff", fontSize: 11, lineHeight: 14 },
  uploading: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(0,0,0,0.5)",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 2,
  },
});
