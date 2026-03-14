from __future__ import annotations

import uuid
from datetime import datetime, timezone

from infrastructure.database.models.message import Message
from infrastructure.memory.embedder import embed_texts
from infrastructure.memory.focus_point import extract_focus_fast, split_to_sentences


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def build_canonical_row(
    *,
    pair_id: uuid.UUID,
    account_id: str,
    role: str,
    text: str,
    created_at: datetime | None = None,
    source: str = "chat",
) -> Message:
    return Message(
        message_id=uuid.uuid4(),
        pair_id=pair_id,
        account_id=account_id,
        created_at=created_at or now_utc(),
        role=role,
        text=text,
        message_kind="canonical",
        source=source,
        chunk_index=None,
        focus_point=None,
        embedding=None,
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
    timestamp = created_at or now_utc()
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
