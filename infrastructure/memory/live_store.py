from __future__ import annotations

import uuid
from datetime import datetime

from infrastructure.database.models.message import Message
from infrastructure.memory.embedder import embed_texts
from infrastructure.memory.focus_point import extract_focus_fast, split_to_sentences


def now_local() -> datetime:
    """Return current server time (naive, no timezone)."""
    return datetime.now()


now_utc = now_local


def build_canonical_row(
    *,
    pair_id: uuid.UUID,
    account_id: str,
    role: str,
    text: str,
    created_at: datetime | None = None,
    source: str = "chat",
    image_urls: list[str] | None = None,
) -> Message:
    return Message(
        message_id=uuid.uuid4(),
        pair_id=pair_id,
        account_id=account_id,
        created_at=created_at or now_local(),
        role=role,
        text=text,
        message_kind="canonical",
        source=source,
        chunk_index=None,
        focus_point=None,
        embedding=None,
        image_urls=image_urls,
    )


def build_chunk_rows(
    *,
    pair_id: uuid.UUID,
    account_id: str,
    role: str,
    text: str,
    created_at: datetime | None = None,
) -> list[Message]:
    rows: list[Message] = []
    timestamp = created_at or now_local()
    for idx, sentence in enumerate(split_to_sentences(text, min_len=15)):
        rows.append(
            Message(
                message_id=uuid.uuid4(),
                pair_id=pair_id,
                account_id=account_id,
                created_at=timestamp,
                role=role,
                text=sentence,
                message_kind="chunk",
                source="chat",
                chunk_index=idx,
                focus_point=extract_focus_fast(sentence) or None,
                embedding=None,
            )
        )
    return rows


def fill_chunk_embeddings(rows: list[Message]) -> None:
    chunk_rows = [row for row in rows if row.message_kind == "chunk"]
    if not chunk_rows:
        return
    vecs = embed_texts([row.text for row in chunk_rows])
    for row, vec in zip(chunk_rows, vecs):
        row.embedding = vec
