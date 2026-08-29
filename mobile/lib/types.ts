/** Shared types between API layer and screens. */

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

export type AttachmentStatus = "uploading" | "uploaded" | "failed";

export interface DraftAttachment {
  id: string;
  localUri: string;
  mimeType: string;
  fileName: string;
  serverUrl?: string;
  status: AttachmentStatus;
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
  user_image_urls?: string[] | null;
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
