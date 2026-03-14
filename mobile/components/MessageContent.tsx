/**
 * Parses assistant messages into structured parts (text, skill badges,
 * saved facts, generated images) and renders each with native components.
 *
 * Mirrors the web frontend's MarkdownMessage parsing logic.
 */
import React, { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Dimensions,
  Image,
  Platform,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import Markdown from "react-native-markdown-display";
import { getBackendUrl } from "@/lib/api";

// ── Types ─────────────────────────────────────────────────────────────────────

type SkillKind = "search" | "web" | "save";

interface SkillCommand {
  kind: SkillKind;
  argument: string;
}

interface SavedFact {
  category: string;
  impressive: number;
  text: string;
}

interface GeneratedImage {
  path: string;
  model: string;
  prompt: string;
}

type Part =
  | { type: "text"; text: string }
  | { type: "skill"; cmd: SkillCommand }
  | { type: "saved_fact"; fact: SavedFact }
  | { type: "generated_image"; image: GeneratedImage }
  | { type: "generating_image"; prompt: string };

// ── Parser ────────────────────────────────────────────────────────────────────

const ALL_COMMANDS_RE =
  /\[(SAVE_MEMORY|SEARCH_MEMORIES|WEB_SEARCH|GENERATE_IMAGE|SCHEDULE_MESSAGE):\s*(.*?)\]|\[SAVED_FACT:\s*(.*?)\s*\|\s*(\d)\s*\|\s*(.*?)\]|\[GENERATED_IMAGE:\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\]/gs;

const KIND_MAP: Record<string, SkillKind> = {
  SAVE_MEMORY: "save",
  SEARCH_MEMORIES: "search",
  WEB_SEARCH: "web",
};

const PARTIAL_PREFIXES = [
  "[SAVE_MEMORY:",
  "[SEARCH_MEMORIES:",
  "[WEB_SEARCH:",
  "[SAVED_FACT:",
  "[GENERATED_IMAGE:",
  "[GENERATE_IMAGE:",
  "[SCHEDULE_MESSAGE:",
];

function trimPartialCommand(text: string): string {
  const tail = text.slice(-30);
  const bracketIdx = tail.lastIndexOf("[");
  if (bracketIdx < 0) return text;
  const afterBracket = tail.slice(bracketIdx);
  for (const prefix of PARTIAL_PREFIXES) {
    if (prefix.startsWith(afterBracket) && afterBracket !== prefix) {
      return text.slice(0, text.length - (tail.length - bracketIdx)).trimEnd();
    }
  }
  return text;
}

function parseContent(rawContent: string, isStreaming: boolean): Part[] {
  const content = isStreaming ? trimPartialCommand(rawContent) : rawContent;
  const parts: Part[] = [];
  let lastIndex = 0;
  const seenImagePaths = new Set<string>();

  for (const match of content.matchAll(ALL_COMMANDS_RE)) {
    const before = content.slice(lastIndex, match.index!);
    if (before) parts.push({ type: "text", text: before });

    if (match[1]) {
      if (match[1] === "SAVE_MEMORY" || match[1] === "SCHEDULE_MESSAGE") {
        lastIndex = match.index! + match[0].length;
        continue;
      }
      if (match[1] === "GENERATE_IMAGE") {
        const raw = match[2] ? match[2].trim() : "";
        const pipeIdx = raw.indexOf("|");
        const prompt = pipeIdx >= 0 ? raw.slice(pipeIdx + 1).trim() : raw;
        parts.push({ type: "generating_image", prompt });
        lastIndex = match.index! + match[0].length;
        continue;
      }
      const kind = KIND_MAP[match[1]] ?? "search";
      parts.push({
        type: "skill",
        cmd: { kind, argument: match[2] ? match[2].trim() : "" },
      });
    } else if (match[3] !== undefined) {
      parts.push({
        type: "saved_fact",
        fact: {
          category: match[3].trim(),
          impressive: parseInt(match[4], 10) || 0,
          text: match[5].trim(),
        },
      });
    } else if (match[6] !== undefined) {
      const imgPath = match[6].trim();
      if (seenImagePaths.has(imgPath)) {
        lastIndex = match.index! + match[0].length;
        continue;
      }
      seenImagePaths.add(imgPath);
      parts.push({
        type: "generated_image",
        image: {
          path: imgPath,
          model: match[7].trim(),
          prompt: match[8].trim(),
        },
      });
    }
    lastIndex = match.index! + match[0].length;
  }

  const tail = content.slice(lastIndex);
  if (tail) parts.push({ type: "text", text: tail });
  return parts;
}

// ── Badge Components ──────────────────────────────────────────────────────────

const BADGE_LABELS: Record<SkillKind, string> = {
  save: "saved to memory",
  search: "searched memories",
  web: "web search",
};

const BADGE_ICONS: Record<SkillKind, string> = {
  save: "✦",
  search: "◉",
  web: "⊕",
};

function SkillBadge({ cmd }: { cmd: SkillCommand }) {
  if (cmd.kind === "save") return null;
  return (
    <View style={s.badgeRow}>
      <Text style={s.badgeIcon}>{BADGE_ICONS[cmd.kind]}</Text>
      <Text style={s.badgeLabel}>{BADGE_LABELS[cmd.kind]}</Text>
      {cmd.argument ? (
        <Text style={s.badgeArg} numberOfLines={1}>{cmd.argument}</Text>
      ) : null}
    </View>
  );
}

function SavedFactCard({ fact }: { fact: SavedFact }) {
  return (
    <View style={s.factRow}>
      <Text style={s.factIcon}>✦</Text>
      <View style={s.factBody}>
        <View style={s.factHeader}>
          <Text style={s.factCategory}>{fact.category}</Text>
          {fact.impressive >= 3 && (
            <Text style={s.factStars}>{"★".repeat(Math.min(fact.impressive, 4))}</Text>
          )}
        </View>
        <Text style={s.factText}>{fact.text}</Text>
      </View>
    </View>
  );
}

function GeneratingShimmer({ prompt }: { prompt: string }) {
  const [dots, setDots] = useState(".");

  useEffect(() => {
    const interval = setInterval(() => {
      setDots(prev => prev.length >= 3 ? "." : prev + ".");
    }, 500);
    return () => clearInterval(interval);
  }, []);

  return (
    <View style={s.shimmerRow}>
      <ActivityIndicator size="small" color="rgba(255,180,50,0.7)" />
      <View style={s.shimmerBody}>
        <Text style={s.shimmerLabel}>generating image{dots}</Text>
        {prompt ? <Text style={s.shimmerPrompt} numberOfLines={2}>{prompt}</Text> : null}
      </View>
    </View>
  );
}

function GeneratedImageCard({ image, backendUrl }: { image: GeneratedImage; backendUrl: string }) {
  const src = image.path.startsWith("http") ? image.path : `${backendUrl}${image.path}`;
  const modelName = image.model.split("/").pop() ?? image.model;
  const promptPreview = image.prompt.length > 60 ? image.prompt.slice(0, 57) + "..." : image.prompt;

  const screenW = Dimensions.get("window").width;
  const imgWidth = Math.min(screenW * 0.7, 300);

  return (
    <View style={[s.imageCard, { width: imgWidth }]}>
      <Image
        source={{
          uri: src,
          headers: { "ngrok-skip-browser-warning": "true" },
        }}
        style={{ width: imgWidth, height: imgWidth, backgroundColor: "rgba(255,255,255,0.03)" }}
        resizeMode="cover"
      />
      <View style={s.imageFooter}>
        <Text style={s.imageModel}>{modelName}</Text>
        <Text style={s.imageDot}>·</Text>
        <Text style={s.imagePrompt} numberOfLines={1}>{promptPreview}</Text>
      </View>
    </View>
  );
}

// ── Markdown Styles ───────────────────────────────────────────────────────────

const mdStyles = StyleSheet.create({
  body: { color: "rgba(255,255,255,0.8)", fontSize: 15, lineHeight: 22 },
  paragraph: { marginTop: 0, marginBottom: 6 },
  strong: { fontWeight: "600" as const, color: "rgba(255,255,255,0.9)" },
  em: { fontStyle: "italic" as const },
  link: { color: "rgba(100,180,255,0.9)" },
  blockquote: {
    borderLeftWidth: 2,
    borderLeftColor: "rgba(255,255,255,0.2)",
    paddingLeft: 12,
    marginLeft: 0,
    marginVertical: 6,
  },
  code_inline: {
    backgroundColor: "rgba(255,255,255,0.08)",
    color: "rgba(255,200,100,0.9)",
    fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace",
    fontSize: 13,
    paddingHorizontal: 4,
    borderRadius: 3,
  },
  fence: {
    backgroundColor: "rgba(255,255,255,0.06)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.1)",
    padding: 12,
    marginVertical: 6,
  },
  code_block: {
    fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace",
    fontSize: 12,
    color: "rgba(255,255,255,0.75)",
  },
  list_item: { marginBottom: 4 },
  bullet_list: { marginVertical: 4 },
  ordered_list: { marginVertical: 4 },
  heading1: { color: "#fff", fontSize: 20, fontWeight: "600" as const, marginBottom: 8, marginTop: 12 },
  heading2: { color: "#fff", fontSize: 18, fontWeight: "500" as const, marginBottom: 6, marginTop: 10 },
  heading3: { color: "rgba(255,255,255,0.9)", fontSize: 16, fontWeight: "500" as const, marginBottom: 4, marginTop: 8 },
  hr: { backgroundColor: "rgba(255,255,255,0.15)", height: 1, marginVertical: 12 },
});

// ── Main Component ────────────────────────────────────────────────────────────

interface MessageContentProps {
  content: string;
  role: "user" | "assistant";
  isStreaming?: boolean;
  showCursor?: boolean;
}

const MessageContent = React.memo(function MessageContent({
  content,
  role,
  isStreaming = false,
  showCursor = false,
}: MessageContentProps) {
  const [backendUrl, setBackendUrl] = useState("");

  useEffect(() => {
    getBackendUrl().then(setBackendUrl);
  }, []);

  const parts = useMemo(
    () => role === "assistant" ? parseContent(content, isStreaming) : [],
    [content, role, isStreaming],
  );

  if (role === "user") {
    return <Text style={s.userText}>{content}</Text>;
  }

  if (!content && isStreaming) {
    return <Text style={s.assistantText}>…</Text>;
  }

  return (
    <View>
      {parts.map((part, i) => {
        switch (part.type) {
          case "skill":
            return <SkillBadge key={`sk-${i}`} cmd={part.cmd} />;
          case "saved_fact":
            return <SavedFactCard key={`sf-${i}`} fact={part.fact} />;
          case "generated_image":
            return <GeneratedImageCard key={`gi-${i}`} image={part.image} backendUrl={backendUrl} />;
          case "generating_image":
            return <GeneratingShimmer key={`gs-${i}`} prompt={part.prompt} />;
          case "text":
            return part.text.trim() ? (
              <Markdown key={`md-${i}`} style={mdStyles}>{part.text}</Markdown>
            ) : null;
          default:
            return null;
        }
      })}
      {showCursor && <Text style={s.cursor}>▋</Text>}
    </View>
  );
});

export default MessageContent;

// ── Styles ────────────────────────────────────────────────────────────────────

const s = StyleSheet.create({
  userText: { color: "rgba(255,255,255,0.9)", fontSize: 15, lineHeight: 22, fontWeight: "300" },
  assistantText: { color: "rgba(255,255,255,0.8)", fontSize: 15, lineHeight: 22, fontWeight: "300" },
  cursor: { color: "rgba(255,255,255,0.6)", fontSize: 14 },

  // Skill badges
  badgeRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.08)",
    backgroundColor: "rgba(255,255,255,0.02)",
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginVertical: 8,
    alignSelf: "flex-start",
  },
  badgeIcon: { color: "rgba(255,255,255,0.3)", fontSize: 11 },
  badgeLabel: {
    color: "rgba(255,255,255,0.42)",
    fontSize: 9,
    letterSpacing: 2,
    textTransform: "uppercase",
  },
  badgeArg: {
    color: "rgba(255,255,255,0.3)",
    fontSize: 11,
    maxWidth: 200,
  },

  // Saved fact
  factRow: {
    flexDirection: "row",
    gap: 8,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.08)",
    backgroundColor: "rgba(255,255,255,0.02)",
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginVertical: 8,
    alignSelf: "flex-start",
  },
  factIcon: { color: "rgba(255,255,255,0.4)", fontSize: 11, marginTop: 1 },
  factBody: { flex: 1 },
  factHeader: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 2 },
  factCategory: {
    color: "rgba(255,255,255,0.55)",
    fontSize: 9,
    letterSpacing: 2,
    textTransform: "uppercase",
  },
  factStars: { color: "rgba(255,255,255,0.3)", fontSize: 10 },
  factText: {
    color: "rgba(255,255,255,0.45)",
    fontSize: 12,
    fontStyle: "italic",
    lineHeight: 17,
  },

  // Generating shimmer
  shimmerRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.1)",
    backgroundColor: "rgba(255,255,255,0.03)",
    paddingHorizontal: 14,
    paddingVertical: 10,
    marginVertical: 8,
    alignSelf: "flex-start",
  },
  shimmerBody: { flex: 1 },
  shimmerLabel: {
    color: "rgba(255,255,255,0.4)",
    fontSize: 10,
    letterSpacing: 3,
    textTransform: "uppercase",
  },
  shimmerPrompt: {
    color: "rgba(255,255,255,0.2)",
    fontSize: 11,
    marginTop: 2,
    lineHeight: 15,
  },

  // Generated image
  imageCard: {
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.1)",
    backgroundColor: "rgba(255,255,255,0.02)",
    marginVertical: 8,
    overflow: "hidden",
  },
  imageFooter: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderTopWidth: 1,
    borderTopColor: "rgba(255,255,255,0.08)",
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  imageModel: {
    color: "rgba(255,255,255,0.45)",
    fontSize: 9,
    letterSpacing: 2,
    textTransform: "uppercase",
  },
  imageDot: { color: "rgba(255,255,255,0.15)", fontSize: 10 },
  imagePrompt: {
    color: "rgba(255,255,255,0.35)",
    fontSize: 10,
    fontStyle: "italic",
    flex: 1,
  },
});
