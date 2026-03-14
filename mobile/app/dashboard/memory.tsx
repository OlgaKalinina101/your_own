/**
 * Memory screen — shows saved facts from Chroma.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  SafeAreaView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { apiFetch } from "@/lib/api";

interface Fact {
  id: string;
  text: string;
  category: string;
  impressive: number;
  frequency: number;
  created_at?: string;
}

export default function MemoryScreen() {
  const [allFacts, setAllFacts] = useState<Fact[]>([]);
  const [displayFacts, setDisplayFacts] = useState<Fact[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const loadFacts = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await apiFetch("/api/chroma/facts?account_id=default");
      if (!res.ok) {
        throw new Error(res.status === 401
          ? "Auth failed — check token in Settings"
          : `HTTP ${res.status}`);
      }
      const data = await res.json() as Fact[];
      setAllFacts(data);
      setDisplayFacts(data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setLoadError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadFacts();
  }, []);

  const handleSearch = () => {
    const q = query.trim().toLowerCase();
    if (!q) {
      setDisplayFacts(allFacts);
      return;
    }
    const filtered = allFacts.filter(f =>
      f.text.toLowerCase().includes(q) ||
      f.category.toLowerCase().includes(q)
    );
    setDisplayFacts(filtered);
  };

  const renderFact = ({ item }: { item: Fact }) => (
    <View style={sty.factCard}>
      <View style={sty.factHeader}>
        <Text style={sty.factCategory}>{item.category || "memory"}</Text>
        {item.impressive > 0 && (
          <Text style={sty.factStars}>{"★".repeat(Math.min(item.impressive, 4))}</Text>
        )}
      </View>
      <Text style={sty.factText}>{item.text}</Text>
    </View>
  );

  return (
    <SafeAreaView style={sty.root}>
      <View style={sty.searchRow}>
        <TextInput
          style={sty.searchInput}
          value={query}
          onChangeText={(text) => {
            setQuery(text);
            if (!text.trim()) setDisplayFacts(allFacts);
          }}
          placeholder="search memories..."
          placeholderTextColor="rgba(255,255,255,0.25)"
          onSubmitEditing={handleSearch}
          returnKeyType="search"
        />
        <TouchableOpacity onPress={handleSearch} style={sty.searchBtn}>
          <Text style={sty.searchBtnText}>SEARCH</Text>
        </TouchableOpacity>
      </View>

      {allFacts.length > 0 && (
        <Text style={sty.totalText}>
          {query.trim() ? `${displayFacts.length} / ${allFacts.length}` : `${allFacts.length}`} facts
        </Text>
      )}

      {loadError ? (
        <View style={sty.errorContainer}>
          <Text style={sty.errorText}>{loadError}</Text>
          <TouchableOpacity style={sty.retryBtn} onPress={loadFacts}>
            <Text style={sty.retryBtnText}>RETRY</Text>
          </TouchableOpacity>
        </View>
      ) : loading ? (
        <ActivityIndicator color="#fff" style={{ marginTop: 40 }} />
      ) : (
        <FlatList
          data={displayFacts}
          keyExtractor={(f) => f.id}
          renderItem={renderFact}
          contentContainerStyle={sty.list}
          ListEmptyComponent={
            <Text style={sty.emptyText}>
              {query.trim() ? "no matches" : "no memories yet"}
            </Text>
          }
        />
      )}
    </SafeAreaView>
  );
}

const sty = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#000" },
  searchRow: {
    flexDirection: "row",
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 8,
    gap: 12,
    borderBottomWidth: 1,
    borderBottomColor: "rgba(255,255,255,0.08)",
  },
  searchInput: {
    flex: 1,
    color: "#fff",
    fontSize: 14,
    fontWeight: "300",
    borderBottomWidth: 1,
    borderBottomColor: "rgba(255,255,255,0.15)",
    paddingVertical: 6,
  },
  searchBtn: { justifyContent: "center", paddingHorizontal: 8 },
  searchBtnText: { color: "rgba(255,255,255,0.5)", fontSize: 9, letterSpacing: 3, textTransform: "uppercase" },
  totalText: {
    color: "rgba(255,255,255,0.3)",
    fontSize: 9,
    letterSpacing: 3,
    textTransform: "uppercase",
    paddingHorizontal: 20,
    paddingTop: 10,
    paddingBottom: 4,
  },
  list: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 40 },
  factCard: {
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.1)",
    padding: 14,
    marginBottom: 10,
  },
  factHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 6,
  },
  factCategory: {
    color: "rgba(255,255,255,0.4)",
    fontSize: 9,
    letterSpacing: 3,
    textTransform: "uppercase",
  },
  factStars: {
    color: "rgba(255,255,255,0.35)",
    fontSize: 10,
  },
  factText: {
    color: "rgba(255,255,255,0.75)",
    fontSize: 14,
    lineHeight: 20,
    fontWeight: "300",
  },
  emptyText: {
    color: "rgba(255,255,255,0.3)",
    textAlign: "center",
    marginTop: 40,
    fontSize: 11,
    letterSpacing: 3,
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
