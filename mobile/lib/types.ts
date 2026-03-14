/** Shared types between API layer and screens. */

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  imageUrl?: string;
  imageUrls?: string[];
  createdAt?: string;
  pairId?: string;
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
}

export interface HistoryResponse {
  pairs: HistoryPair[];
  next_before?: string | null;
  has_more: boolean;
}

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
}
