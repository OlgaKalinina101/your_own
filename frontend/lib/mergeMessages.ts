/**
 * Reconciling what this client shows with what the server stored.
 *
 * The client is a cache, not the original — a fact the desktop did not act on.
 * It fetched history once and then kept whatever it had for the life of the
 * process, so a conversation continued on the phone was invisible here until a
 * restart. And the conversation has three writers, not two: this client, the
 * other client, and the assistant, which persists messages on its own schedule
 * and notifies only Pushy.
 *
 * The trap that makes this non-trivial: a message sent from here exists twice.
 * Once optimistically, with a client-generated id (`user-<uuid>`), and once as
 * the server stored it (`<pair_id>-user`). Merging on id alone duplicates it.
 * So the join key is `pair_id` + role, which the client learns from the stream's
 * first frame and stamps onto its own copies.
 */

import type { Message } from "@/lib/types";

function key(message: Message): string | null {
  return message.pairId ? `${message.pairId}:${message.role}` : null;
}

/**
 * Fold `incoming` (the server's version) into `current` (what is on screen).
 *
 * Positions are preserved rather than re-sorted: a message being streamed right
 * now has no timestamp to sort by, and re-ordering the list under someone
 * mid-conversation is worse than a rare imperfect order.
 */
export function mergeMessages(current: Message[], incoming: Message[]): Message[] {
  if (incoming.length === 0) return current;

  const incomingByKey = new Map<string, Message>();
  for (const message of incoming) {
    const k = key(message);
    if (k) incomingByKey.set(k, message);
  }

  const used = new Set<string>();
  const merged = current.map((message) => {
    const k = key(message);
    if (!k) return message;
    const server = incomingByKey.get(k);
    if (!server) return message;
    used.add(k);
    // Server truth wins on content, but a local-only field the server does not
    // send would be lost by a plain replace. Two of them: the recalled-facts
    // panel arrives over the stream rather than in history, and `interrupted`
    // records why a reply stopped — a fact only the client that watched it stop
    // has. The server has the clipped text and no idea it is clipped.
    return {
      ...message,
      ...server,
      chromaFacts: server.chromaFacts ?? message.chromaFacts,
      interrupted: server.interrupted ?? message.interrupted,
    };
  });

  // Anything the server has that this client has never seen goes at the end;
  // history arrives oldest-first, and new pairs are newer than everything here.
  for (const message of incoming) {
    const k = key(message);
    if (k && used.has(k)) continue;
    if (k && merged.some((existing) => key(existing) === k)) continue;
    merged.push(message);
  }

  return merged;
}

/**
 * The timestamp to ask the server for changes after.
 *
 * The newest `createdAt` this client holds — not "now", which would skip
 * anything stored while the request was in flight, and not the oldest, which
 * would refetch the whole conversation every time.
 */
export function latestCursor(messages: readonly Message[]): string | null {
  let latest: string | null = null;
  for (const message of messages) {
    if (!message.createdAt) continue;
    if (latest === null || message.createdAt > latest) latest = message.createdAt;
  }
  return latest;
}
