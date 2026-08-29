/**
 * Composing the attachments on an outgoing message: picking files, pasting
 * them, holding them to a cap, turning them into previews.
 *
 * Deliberately *not* named `chatImages.ts`. The phone has a file by that name
 * and it does the opposite job — resolving a URL and its headers for display —
 * which on the desktop lives in `lib/media.ts`. Two files with one name and
 * opposite contents would be worse than two names, so:
 *
 *   composing an outgoing message   desktop: this file   phone: useChatController
 *   displaying a received image     desktop: lib/media   phone: lib/chatImages
 *
 * The split differs because the flows genuinely differ: the desktop posts the
 * files themselves in a FormData, the phone uploads first and sends URLs (hence
 * its `DraftAttachment` with an upload status, which has no desktop counterpart).
 */

export const MAX_CHAT_IMAGES = 8;

/**
 * How much of `selected` fits under the cap.
 *
 * Returns the overflow as well as the accepted files. The page currently drops
 * the overflow in silence — paste a ninth image and nothing happens, with no
 * word about why — and a caller cannot say anything about what it never learns.
 */
export function fitWithinCap<T>(
  currentCount: number,
  selected: readonly T[],
  max: number = MAX_CHAT_IMAGES,
): { accepted: T[]; rejected: number } {
  const remaining = Math.max(0, max - currentCount);
  const accepted = selected.slice(0, remaining);
  return { accepted, rejected: selected.length - accepted.length };
}

/** The minimum of a clipboard item this module needs; keeps the code testable. */
export interface ClipboardImageItem {
  type: string;
  getAsFile: () => File | null;
}

/** Image files out of a clipboard payload, in order, skipping everything else. */
export function imageFilesFromClipboard(
  items: Iterable<ClipboardImageItem>,
): File[] {
  return Array.from(items)
    .filter((item) => item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter((file): file is File => Boolean(file));
}

/** Drop one index from a list, without mutating it. */
export function removeAt<T>(list: readonly T[], index: number): T[] {
  return list.filter((_, current) => current !== index);
}

/** A `data:` URL for a picked file, for showing it before it is sent. */
export function readPreview(file: File): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("Failed to read image"));
    reader.readAsDataURL(file);
  });
}
