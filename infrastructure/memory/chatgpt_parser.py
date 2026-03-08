"""Parser for ChatGPT's conversations.json export.

Each conversation is a tree of nodes (mapping dict).
We walk the tree in order and group messages into user+assistant *pairs*.
A pair is the unit of memory: one user message + the assistant reply that follows it.

If a user message has no assistant reply (e.g. last message), it is stored as
a half-pair (assistant text = "").

Export format (each item in the top-level list):
{
  "id": "...",
  "title": "...",
  "mapping": {
    "<node_id>": {
      "message": {
        "author": { "role": "user" | "assistant" | "system" | "tool" },
        "create_time": 1700000000.0,
        "content": { "content_type": "text", "parts": ["..."] }
      },
      "parent": "...",
      "children": ["..."]
    }
  }
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator


@dataclass
class ParsedPair:
    """One user+assistant exchange — the atomic unit of memory."""
    conversation_id:    str
    conversation_title: str

    user_text:          str
    user_created_at:    datetime

    assistant_text:     str          # empty string if no reply yet
    assistant_created_at: datetime   # same as user_created_at if no reply


def _extract_text(content: dict) -> str:
    if not content:
        return ""
    chunks = []
    for part in content.get("parts", []):
        if isinstance(part, str):
            chunks.append(part)
        elif isinstance(part, dict) and "text" in part:
            chunks.append(part["text"])
    return "\n".join(chunks).strip()


def _ts(ts: float | None) -> datetime:
    if ts is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _walk_conversation(mapping: dict) -> list[dict]:
    """Return message nodes in chronological order using an iterative stack (no recursion)."""
    roots = [n for n in mapping.values() if not n.get("parent")]
    if not roots:
        nodes = [n for n in mapping.values() if n.get("message")]
        nodes.sort(key=lambda n: n["message"].get("create_time") or 0)
        return nodes

    ordered: list[dict] = []
    # Iterative DFS with an explicit stack to avoid Python recursion limit
    stack = [roots[0]["id"]]
    visited: set[str] = set()

    while stack:
        node_id = stack.pop()
        if node_id in visited:
            continue
        visited.add(node_id)

        node = mapping.get(node_id)
        if not node:
            continue
        ordered.append(node)

        # Push children in reverse so left-most child is processed first
        for child_id in reversed(node.get("children", [])):
            if child_id not in visited:
                stack.append(child_id)

    return ordered


def _iter_pairs(
    conv_id: str,
    conv_title: str,
    mapping: dict,
) -> Iterator[ParsedPair]:
    """
    Walk the conversation tree and yield user+assistant pairs.

    Strategy:
      - Collect all user and assistant messages in order.
      - Match each user message with the assistant message that immediately follows it.
      - If a user turn is followed by another user turn (no assistant reply), pair
        it with an empty assistant text.
    """
    nodes = _walk_conversation(mapping)

    # Extract only user/assistant messages with non-empty text
    messages: list[tuple[str, str, datetime]] = []  # (role, text, created_at)
    for node in nodes:
        msg = node.get("message")
        if not msg:
            continue
        role = msg.get("author", {}).get("role", "")
        if role not in ("user", "assistant"):
            continue
        text = _extract_text(msg.get("content", {}))
        if not text:
            continue
        messages.append((role, text, _ts(msg.get("create_time"))))

    i = 0
    while i < len(messages):
        role, text, ts = messages[i]

        if role == "user":
            user_text, user_ts = text, ts
            # Look ahead for the assistant reply
            if i + 1 < len(messages) and messages[i + 1][0] == "assistant":
                _, asst_text, asst_ts = messages[i + 1]
                i += 2
            else:
                # No assistant reply yet
                asst_text, asst_ts = "", user_ts
                i += 1

            yield ParsedPair(
                conversation_id=conv_id,
                conversation_title=conv_title,
                user_text=user_text,
                user_created_at=user_ts,
                assistant_text=asst_text,
                assistant_created_at=asst_ts,
            )

        else:
            # Lone assistant message (no preceding user) — skip
            i += 1


def parse_conversations(data: list[dict]) -> Iterator[ParsedPair]:
    for conv in data:
        yield from _iter_pairs(
            conv_id=conv.get("id", ""),
            conv_title=conv.get("title", ""),
            mapping=conv.get("mapping", {}),
        )


def parse_conversations_bytes(raw: bytes) -> list[ParsedPair]:
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    return list(parse_conversations(data))
