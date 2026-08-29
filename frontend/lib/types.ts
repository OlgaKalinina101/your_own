/**
 * Shapes the backend sends. Shared between the API layer and the screens.
 *
 * Deliberately a mirror of `mobile/lib/types.ts`: same names, same field order,
 * so the two files can be diffed line by line. The clients have no shared code
 * and drift between them is the quiet kind — it produces "works on the phone,
 * not on the desktop" with no merge conflict to notice. Four such divergences
 * had already accumulated when this file was written; two of them were a type
 * that existed on one side only.
 *
 * If you add a field here, add it there. Anything that legitimately exists on
 * one client only is marked below with the reason.
 */

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  imageUrl?: string;
  imageUrls?: string[];
  chromaFacts?: ChromaFact[];
  createdAt?: string;
  pairId?: string;
  /**
   * Why this reply is shorter than it should be.
   *
   * Local to this client and deliberately not part of the message text: the
   * server stores what was said, not why it stopped, and `mergeMessages` gives
   * the server the last word on content. A marker written into `content` would
   * be wiped by the next sync — this one survives it, the same way
   * `chromaFacts` does.
   */
  interrupted?: "connection" | "stopped";
}

export interface ChromaFact {
  id: string;
  text: string;
  category: string;
  impressive: number;
  time_label: string;
}

export interface HistoryPair {
  pair_id: string;
  created_at?: string | null;
  pair_created_at?: string | null;
  user_text: string;
  assistant_text: string;
  /** Sent by `api/chat.py`; the desktop used to drop it, losing images on reload. */
  user_image_urls?: string[] | null;
}

export interface HistoryResponse {
  pairs: HistoryPair[];
  next_before?: string | null;
  has_more: boolean;
}

/**
 * `GET /api/settings/raw` merges over the backend defaults, so every key is
 * present in practice; optional here because a client should not crash on a
 * server that is older or newer than it is.
 */
export interface Settings {
  ai_name?: string;
  model?: string;
  temperature?: number;
  top_p?: number;
  history_pairs?: number;
  memory_cutoff_days?: number;
  openrouter_api_key?: string;
  pushy_api_key?: string;
  pushy_device_token?: string;
  reflection_cooldown_hours?: number;
  reflection_interval_hours?: number;
  // Present in the backend's defaults and in `SettingsPatch`, but absent from
  // mobile/lib/types.ts — the phone has no screen for them yet.
  enabled_skills?: string[] | null;
  body_image_model?: string;
  user_timezone?: string;
  research_model?: string;
  research_web_engine?: string;
  research_max_attempts?: number;
}

/** `GET /api/settings/skills`. Desktop only — the phone has no skills screen. */
export interface SkillInfo {
  id: string;
  cmd_name: string;
  display: { en: string; ru: string };
  description: { en: string; ru: string };
  example: string | null;
  action_type: string;
  enabled: boolean;
}
