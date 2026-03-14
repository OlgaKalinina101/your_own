/**
 * Soul screen — view and edit the AI's soul (system prompt).
 */
import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  SafeAreaView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { apiGet, apiPut } from "@/lib/api";

export default function SoulScreen() {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const loadSoul = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await apiGet<{ text: string }>("/api/settings/soul");
      setText(data.text ?? "");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setLoadError(msg.includes("401") ? "Auth failed — check token in Settings" : msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSoul();
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      await apiPut("/api/settings/soul", { text });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setLoadError(msg);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={sty.root}>
        <ActivityIndicator color="#fff" style={{ marginTop: 40 }} />
      </SafeAreaView>
    );
  }

  if (loadError) {
    return (
      <SafeAreaView style={sty.root}>
        <View style={sty.errorContainer}>
          <Text style={sty.errorText}>{loadError}</Text>
          <TouchableOpacity style={sty.retryBtn} onPress={loadSoul}>
            <Text style={sty.retryBtnText}>RETRY</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={sty.root}>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <View style={sty.container}>
          <Text style={sty.hint}>
            The AI's core personality and system prompt. Changes take effect immediately.
          </Text>
          <TextInput
            style={sty.editor}
            value={text}
            onChangeText={setText}
            multiline
            placeholder="Write the AI's soul here..."
            placeholderTextColor="rgba(255,255,255,0.2)"
            textAlignVertical="top"
            autoCapitalize="sentences"
          />
          <TouchableOpacity style={sty.saveBtn} onPress={handleSave} disabled={saving}>
            {saving ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={sty.saveBtnText}>{saved ? "SAVED" : "SAVE"}</Text>
            )}
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const sty = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#000" },
  container: { flex: 1, paddingHorizontal: 20, paddingTop: 16, paddingBottom: 24 },
  hint: {
    color: "rgba(255,255,255,0.3)",
    fontSize: 11,
    lineHeight: 16,
    marginBottom: 16,
    fontWeight: "300",
  },
  editor: {
    flex: 1,
    color: "rgba(255,255,255,0.85)",
    fontSize: 14,
    lineHeight: 22,
    fontWeight: "300",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.1)",
    padding: 16,
    marginBottom: 16,
  },
  saveBtn: {
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.3)",
    paddingVertical: 14,
    alignItems: "center",
  },
  saveBtnText: {
    color: "rgba(255,255,255,0.7)",
    fontSize: 9,
    letterSpacing: 5,
    textTransform: "uppercase",
  },
  errorContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    paddingHorizontal: 32,
  },
  errorText: {
    color: "rgba(255,80,80,0.8)",
    fontSize: 13,
    textAlign: "center",
    marginBottom: 24,
    lineHeight: 20,
  },
  retryBtn: {
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.2)",
    paddingVertical: 12,
    paddingHorizontal: 32,
  },
  retryBtnText: {
    color: "rgba(255,255,255,0.6)",
    fontSize: 9,
    letterSpacing: 4,
    textTransform: "uppercase",
  },
});
