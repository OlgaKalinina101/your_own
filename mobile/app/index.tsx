/**
 * Connect screen — user enters the backend URL and auth token.
 * Auto-navigates to dashboard if stored credentials are valid.
 */
import { useRouter } from "expo-router";
import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import {
  getAuthToken,
  getBackendUrl,
  setAuthToken,
  setBackendUrl,
  testConnection,
} from "@/lib/api";

export default function ConnectScreen() {
  const router = useRouter();

  const [url, setUrl] = useState("http://localhost:8000");
  const [token, setToken] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    (async () => {
      const storedUrl = await getBackendUrl();
      const storedToken = await getAuthToken();
      if (storedUrl) setUrl(storedUrl);
      if (storedToken) setToken(storedToken);

      if (storedToken) {
        // Verify both connectivity AND token validity
        const err = await testConnection(storedUrl, storedToken);
        if (err === null) {
          router.replace("/dashboard");
          return;
        }
        // Token invalid or server unreachable — fall through to manual connect
        setError(`Auto-connect failed: ${err}`);
      }
      setChecking(false);
    })();
  }, []);

  const handleConnect = async () => {
    if (!url.trim()) {
      setError("Enter the backend URL");
      return;
    }
    if (!token.trim()) {
      setError("Enter the auth token");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const err = await testConnection(url.trim(), token.trim());
      if (err !== null) {
        setError(err);
        return;
      }
      await setBackendUrl(url.trim());
      await setAuthToken(token.trim());
      router.replace("/dashboard");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  if (checking) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color="#fff" />
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: "#000" }}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <ScrollView contentContainerStyle={styles.container}>
        <Text style={styles.title}>your own</Text>
        <Text style={styles.subtitle}>connect to backend</Text>

        <Text style={styles.label}>Backend URL</Text>
        <TextInput
          style={styles.input}
          value={url}
          onChangeText={setUrl}
          placeholder="http://192.168.1.x:8000"
          placeholderTextColor="rgba(255,255,255,0.25)"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
        />

        <Text style={styles.label}>Auth Token</Text>
        <TextInput
          style={styles.input}
          value={token}
          onChangeText={setToken}
          placeholder="paste from data/auth_token.txt"
          placeholderTextColor="rgba(255,255,255,0.25)"
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
        />

        {error && <Text style={styles.error}>{error}</Text>}

        <TouchableOpacity
          style={[styles.btn, loading && styles.btnDisabled]}
          onPress={handleConnect}
          disabled={loading}
        >
          {loading ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.btnText}>CONNECT</Text>
          )}
        </TouchableOpacity>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, backgroundColor: "#000", justifyContent: "center", alignItems: "center" },
  container: {
    flexGrow: 1,
    backgroundColor: "#000",
    paddingHorizontal: 32,
    paddingTop: 80,
    paddingBottom: 40,
  },
  title: {
    color: "#fff",
    fontSize: 28,
    fontWeight: "200",
    letterSpacing: 8,
    textTransform: "uppercase",
    marginBottom: 6,
  },
  subtitle: {
    color: "rgba(255,255,255,0.4)",
    fontSize: 11,
    letterSpacing: 4,
    textTransform: "uppercase",
    marginBottom: 48,
  },
  label: {
    color: "rgba(255,255,255,0.45)",
    fontSize: 9,
    letterSpacing: 4,
    textTransform: "uppercase",
    marginBottom: 8,
    marginTop: 24,
  },
  input: {
    borderBottomWidth: 1,
    borderBottomColor: "rgba(255,255,255,0.2)",
    color: "#fff",
    fontSize: 15,
    paddingVertical: 10,
    fontWeight: "300",
  },
  error: {
    color: "rgba(255,80,80,0.9)",
    fontSize: 12,
    marginTop: 16,
  },
  btn: {
    marginTop: 40,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.3)",
    paddingVertical: 14,
    alignItems: "center",
  },
  btnDisabled: { opacity: 0.5 },
  btnText: {
    color: "rgba(255,255,255,0.7)",
    fontSize: 10,
    letterSpacing: 5,
    textTransform: "uppercase",
  },
});
