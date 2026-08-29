import type { ImageSourcePropType } from "react-native";

import { peekAuthToken } from "@/lib/api";

const NGROK_IMAGE_HEADERS = { "ngrok-skip-browser-warning": "true" };

/**
 * Headers for a remote image.
 *
 * `/api/generated_images`, `/api/user_uploads` and `/api/body` are behind the
 * token — they used to be unauthenticated static mounts, which meant anyone who
 * reached the port could enumerate them. Without the header every one of these
 * comes back 401 and the card falls through to "image unavailable".
 */
function remoteImageHeaders(): Record<string, string> {
  const token = peekAuthToken();
  return token
    ? { ...NGROK_IMAGE_HEADERS, Authorization: `Bearer ${token}` }
    : { ...NGROK_IMAGE_HEADERS };
}

function normalizeBackendUrl(backendUrl: string): string {
  return backendUrl.replace(/\/$/, "");
}

export function resolveChatImageUri(uri: string, backendUrl: string): string | null {
  if (!uri) return null;
  if (uri.startsWith("http://") || uri.startsWith("https://")) return uri;
  if (uri.startsWith("file://") || uri.startsWith("content://")) return uri;
  if (uri.startsWith("/")) {
    const normalized = normalizeBackendUrl(backendUrl);
    return normalized ? `${normalized}${uri}` : null;
  }
  return null;
}

export function buildChatImageSource(uri: string, backendUrl: string): ImageSourcePropType | null {
  const resolved = resolveChatImageUri(uri, backendUrl);
  if (!resolved) return null;
  if (resolved.startsWith("http://") || resolved.startsWith("https://")) {
    return { uri: resolved, headers: remoteImageHeaders() };
  }
  return { uri: resolved };
}
