"""
POST /api/memory/import   — upload conversations.json, stream progress via SSE
GET  /api/memory/stats    — return message count / pair count for an account

Storage model
─────────────
Each user+assistant exchange (ParsedPair) produces N + M rows:
  • one row per sentence from the user message   (role="user")
  • one row per sentence from the assistant reply (role="assistant")

Every row gets:
  embedding   = vector(384) of that sentence   ← searched by K-NN
  focus_point = meaningful tokens               ← keyword boost
  pair_id     = shared UUID linking the pair

This mirrors the Kotlin SemanticSearchUtil design:
  K-NN on embedding + keyword boost on focus_point tokens.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database.engine import get_db
from infrastructure.database.models.message import Message
from infrastructure.database.repositories.message_repo import MessageRepository
from infrastructure.memory.chatgpt_parser import parse_conversations_bytes, ParsedPair
from infrastructure.memory.embedder import embed_texts
from infrastructure.memory.focus_point import extract_focus_fast, split_to_sentences

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory"])

BATCH_SIZE = 20   # pairs per DB flush

_embed_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embedder")
_cpu_executor   = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cpu")


# ── Row builder ───────────────────────────────────────────────────────────────

def _pair_to_rows(pair: ParsedPair, account_id: str) -> list[Message]:
    """
    Convert one ParsedPair into rows — one row per sentence for both roles.
    All rows share the same pair_id so they can be fetched together.
    Embeddings are set to None here; filled in by _fill_embeddings().
    """
    pair_id = uuid.uuid4()
    rows: list[Message] = []

    for text, created_at, role in (
        (pair.user_text,      pair.user_created_at,      "user"),
        (pair.assistant_text, pair.assistant_created_at, "assistant"),
    ):
        if not text:
            continue
        for sentence in split_to_sentences(text, min_len=15):
            rows.append(Message(
                message_id=uuid.uuid4(),
                pair_id=pair_id,
                account_id=account_id,
                conversation_id=pair.conversation_id or None,
                created_at=created_at,
                role=role,
                text=sentence,
                message_kind="chunk",
                source="import",
                chunk_index=len(rows),
                focus_point=extract_focus_fast(sentence) or None,
                embedding=None,
            ))

    return rows


def _fill_embeddings(rows: list[Message]) -> None:
    """Compute embeddings for all rows in-place (runs in thread pool)."""
    if not rows:
        return
    vecs = embed_texts([r.text for r in rows])
    for row, vec in zip(rows, vecs):
        row.embedding = vec


# ── Import endpoint ───────────────────────────────────────────────────────────

@router.post("/import")
async def import_chatgpt(
    file: UploadFile = File(...),
    account_id: str = Form("default"),
    text_language: str = Form("ru"),
    db: AsyncSession = Depends(get_db),
):
    """
    Stream SSE: {"total": N, "done": M, "stage": "..."}  …  {"finished": true}
    """
    raw  = await file.read()
    loop = asyncio.get_event_loop()

    async def event_stream():
        yield f"data: {json.dumps({'total': 0, 'done': 0, 'stage': 'parsing'})}\n\n"

        try:
            pairs = await loop.run_in_executor(_cpu_executor, parse_conversations_bytes, raw)
        except Exception as exc:
            logger.exception("[memory/import] parse error: %s", exc)
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            return

        total = len(pairs)
        yield f"data: {json.dumps({'total': total, 'done': 0, 'stage': 'parsed'})}\n\n"

        repo = MessageRepository(db)
        done = 0

        for i in range(0, total, BATCH_SIZE):
            batch = pairs[i : i + BATCH_SIZE]
            yield f"data: {json.dumps({'total': total, 'done': done, 'stage': 'embedding'})}\n\n"

            try:
                rows: list[Message] = []
                for pair in batch:
                    rows.extend(_pair_to_rows(pair, account_id))

                await loop.run_in_executor(_embed_executor, _fill_embeddings, rows)

                await repo.bulk_save(rows)
                done = min(i + BATCH_SIZE, total)
                yield f"data: {json.dumps({'total': total, 'done': done, 'stage': 'saving'})}\n\n"

            except Exception as exc:
                logger.exception("[memory/import] batch %d error: %s", i, exc)
                yield f"data: {json.dumps({'error': str(exc), 'batch': i})}\n\n"
                return

        yield f"data: {json.dumps({'total': total, 'done': total, 'finished': True})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Stats endpoint ────────────────────────────────────────────────────────────

@router.get("/stats")
async def memory_stats(
    account_id: str = "default",
    db: AsyncSession = Depends(get_db),
):
    repo = MessageRepository(db)
    pair_count = await repo.count_pairs(account_id, source="import")
    chunk_count = await repo.count_rows(account_id, source="import")
    return {
        "account_id": account_id,
        "pair_count": pair_count,
        "sentence_count": chunk_count,
    }
