/**
 * Settings screen — manages server connection and AI settings.
 */
import { useRouter } from "expo-router";
import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import {
  clearAuth,
  getAuthToken,
  getBackendUrl,
  loadSettings,
  registerPushyToken,
  saveSettings,
  setAuthToken,
  setBackendUrl,
  testConnection,
} from "@/lib/api";
import type { Settings } from "@/lib/types";
import { getStoredDeviceToken, setupPushNotifications } from "@/lib/push";

function Row({ label, value, onChangeText, secure = false, placeholder = "", editable = true }: {
  label: string;
  value: string;
  onChangeText: (v: string) => void;
  secure?: boolean;
  placeholder?: string;
  editable?: boolean;
}) {
  return (
    <View style={sty.row}>
      <Text style={sty.rowLabel}>{label}</Text>
      <TextInput
        style={[sty.input, !editable && { opacity: 0.5 }]}
        value={value}
        onChangeText={onChangeText}
        secureTextEntry={secure}
        placeholder={placeholder}
        placeholderTextColor="rgba(255,255,255,0.2)"
        autoCapitalize="none"
        autoCorrect={false}
        editable={editable}
      />
    </View>
  );
}

export default function SettingsScreen() {
  const router = useRouter();

  // Connection
  const [serverUrl, setServerUrl] = useState("");
  const [authToken, setAuthTokenVal] = useState("");
  const [connected, setConnected] = useState<boolean | null>(null);
  const [connecting, setConnecting] = useState(false);

  // AI
  const [aiName, setAiName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");

  // Pushy
  const [pushyApiKey, setPushyApiKey] = useState("");
  const [deviceToken, setDeviceToken] = useState("");

  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const fetchRemoteSettings = async () => {
    try {
      const s = await loadSettings();
      if (s.ai_name) setAiName(s.ai_name);
      if (s.openrouter_api_key) setApiKey(s.openrouter_api_key);
      if (s.model) setModel(s.model);
      if (s.pushy_api_key) setPushyApiKey(s.pushy_api_key);
      if (s.pushy_device_token) setDeviceToken(s.pushy_device_token);
      setConnected(true);

      // Sync local device token to backend if it wasn't saved yet
      const localToken = await getStoredDeviceToken();
      if (localToken && !s.pushy_device_token) {
        registerPushyToken(localToken).catch(() => {});
      }
    } catch (err) {
      console.warn("[settings] loadSettings error:", err);
      setConnected(false);
    }
  };

  useEffect(() => {
    (async () => {
      const url = await getBackendUrl();
      const token = await getAuthToken();
      if (url) setServerUrl(url);
      if (token) setAuthTokenVal(token);
      const dt = await getStoredDeviceToken();
      if (dt) setDeviceToken(dt);

      if (token) {
        await fetchRemoteSettings();
      } else {
        setConnected(false);
      }
    })();
  }, []);

  const handleConnect = async () => {
    if (!serverUrl.trim() || !authToken.trim()) {
      Alert.alert("Missing fields", "Enter both URL and auth token.");
      return;
    }
    setConnecting(true);
    const err = await testConnection(serverUrl.trim(), authToken.trim());
    setConnecting(false);

    if (err !== null) {
      Alert.alert("Connection failed", err);
      setConnected(false);
      return;
    }

    await setBackendUrl(serverUrl.trim());
    await setAuthToken(authToken.trim());
    await fetchRemoteSettings();

    // Re-register push token now that auth is available
    setupPushNotifications().catch(() => {});

    Alert.alert("Connected", "Backend connection saved.");
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const localToken = await getStoredDeviceToken();
      await saveSettings({
        ai_name: aiName || undefined,
        openrouter_api_key: apiKey || undefined,
        model: model || undefined,
        ...(pushyApiKey ? { pushy_api_key: pushyApiKey } : {}),
        ...(localToken ? { pushy_device_token: localToken } : {}),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      Alert.alert("Save failed", String(err));
    } finally {
      setSaving(false);
    }
  };

  const handleDisconnect = async () => {
    Alert.alert("Disconnect", "Clear saved connection?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Disconnect",
        style: "destructive",
        onPress: async () => {
          await clearAuth();
          router.replace("/");
        },
      },
    ]);
  };

  return (
    <SafeAreaView style={sty.root}>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={sty.container}>

          {/* Connection */}
          <Text style={sty.section}>Server Connection</Text>
          {connected !== null && (
            <Text style={[sty.badge, connected ? sty.badgeOk : sty.badgeFail]}>
              {connected ? "connected" : "disconnected"}
            </Text>
          )}
          <Row label="Server URL" value={serverUrl} onChangeText={setServerUrl} placeholder="http://192.168.x.x:8000" />
          <Row label="Auth Token" value={authToken} onChangeText={setAuthTokenVal} secure placeholder="paste auth token" />
          <TouchableOpacity style={sty.btn} onPress={handleConnect} disabled={connecting}>
            {connecting ? (
              <ActivityIndicator color="rgba(255,255,255,0.6)" />
            ) : (
              <Text style={sty.btnText}>CONNECT</Text>
            )}
          </TouchableOpacity>

          <View style={sty.divider} />

          {/* AI */}
          <Text style={sty.section}>AI Settings</Text>
          <Row label="AI Name" value={aiName} onChangeText={setAiName} placeholder="How your AI introduces itself" />
          <Row label="OpenRouter API Key" value={apiKey} onChangeText={setApiKey} secure placeholder="sk-or-..." />
          <Row label="Model" value={model} onChangeText={setModel} placeholder="anthropic/claude-opus-4.6" />

          <View style={sty.divider} />

          {/* Pushy */}
          <Text style={sty.section}>Push Notifications</Text>
          <Row label="Pushy Secret API Key" value={pushyApiKey} onChangeText={setPushyApiKey} secure placeholder="Your Pushy API key" />
          <View style={sty.row}>
            <Text style={sty.rowLabel}>Device Token</Text>
            <Text style={sty.deviceToken} numberOfLines={1}>{deviceToken || "(not registered)"}</Text>
          </View>

          <View style={sty.divider} />

          <TouchableOpacity style={sty.savebtn} onPress={handleSave} disabled={saving || !connected}>
            {saving ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={[sty.savebtnText, !connected && { opacity: 0.3 }]}>{saved ? "SAVED" : "SAVE"}</Text>
            )}
          </TouchableOpacity>

          <TouchableOpacity style={sty.disconnectBtn} onPress={handleDisconnect}>
            <Text style={sty.disconnectText}>DISCONNECT</Text>
          </TouchableOpacity>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const sty = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#000" },
  container: { paddingHorizontal: 24, paddingTop: 24, paddingBottom: 60 },
  section: {
    color: "rgba(255,255,255,0.45)",
    fontSize: 9,
    letterSpacing: 4,
    textTransform: "uppercase",
    marginBottom: 16,
    marginTop: 8,
  },
  badge: {
    fontSize: 9,
    letterSpacing: 3,
    textTransform: "uppercase",
    marginBottom: 12,
  },
  badgeOk: { color: "rgba(80,200,100,0.8)" },
  badgeFail: { color: "rgba(220,80,80,0.8)" },
  row: { marginBottom: 20 },
  rowLabel: {
    color: "rgba(255,255,255,0.35)",
    fontSize: 9,
    letterSpacing: 3,
    textTransform: "uppercase",
    marginBottom: 8,
  },
  input: {
    borderBottomWidth: 1,
    borderBottomColor: "rgba(255,255,255,0.15)",
    color: "#fff",
    fontSize: 14,
    paddingVertical: 8,
    fontWeight: "300",
  },
  deviceToken: {
    color: "rgba(255,255,255,0.4)",
    fontSize: 11,
    fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace",
  },
  divider: { height: 1, backgroundColor: "rgba(255,255,255,0.08)", marginVertical: 24 },
  btn: {
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.2)",
    paddingVertical: 12,
    alignItems: "center",
    marginTop: 8,
  },
  btnText: { color: "rgba(255,255,255,0.6)", fontSize: 9, letterSpacing: 4, textTransform: "uppercase" },
  savebtn: {
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.3)",
    paddingVertical: 14,
    alignItems: "center",
    marginTop: 8,
  },
  savebtnText: { color: "rgba(255,255,255,0.7)", fontSize: 9, letterSpacing: 5, textTransform: "uppercase" },
  disconnectBtn: { marginTop: 16, alignItems: "center", paddingVertical: 12 },
  disconnectText: { color: "rgba(220,80,80,0.6)", fontSize: 9, letterSpacing: 4, textTransform: "uppercase" },
});
