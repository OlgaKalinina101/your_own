"""CRUD helpers for the `messages` table."""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Iterable, Optional, Sequence

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database.models.message import Message


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── write ──────────────────────────────────────────────────────────────────

    async def save(self, msg: Message) -> Message:
        self._session.add(msg)
        await self._session.commit()
        await self._session.refresh(msg)
        return msg

    async def bulk_save(self, msgs: list[Message]) -> None:
        """
        Insert all messages via raw SQL so the embedding column always receives
        either NULL or an explicit `cast(:embedding as vector)` expression.
        Using session.add_all() for rows with embedding=None would still send
        the column as varchar (Text ORM type), which PostgreSQL rejects.
        """
        if not msgs:
            return

        _INSERT = (
            "INSERT INTO messages ("
            "  message_id, pair_id, account_id, conversation_id,"
            "  created_at, role, text, message_kind, source, chunk_index, focus_point,"
            "  memory, impressive, frequency, last_used,"
            "  insight, user_mood, assistant_mood, assistant_intensity,"
            "  emoji, embedding"
            ") VALUES ("
            "  :message_id, :pair_id, :account_id, :conversation_id,"
            "  :created_at, :role, :text, :message_kind, :source, :chunk_index, :focus_point,"
            "  :memory, :impressive, :frequency, :last_used,"
            "  :insight, :user_mood, :assistant_mood, :assistant_intensity,"
            "  :emoji, {emb_expr}"
            ") ON CONFLICT (message_id) DO NOTHING"
        )

        for m in msgs:
            if m.embedding is not None:
                emb_str = (
                    "[" + ",".join(f"{v:.8f}" for v in m.embedding) + "]"
                    if isinstance(m.embedding, list) else str(m.embedding)
                )
                emb_expr = "cast(:embedding as vector)"
            else:
                emb_str  = None
                emb_expr = "NULL"

            await self._session.execute(
                text(_INSERT.format(emb_expr=emb_expr)),
                {
                    "message_id":          str(m.message_id),
                    "pair_id":             str(m.pair_id),
                    "account_id":          m.account_id,
                    "conversation_id":     m.conversation_id,
                    "created_at":          m.created_at,
                    "role":                m.role,
                    "text":                m.text,
                    "message_kind":        m.message_kind,
                    "source":              m.source,
                    "chunk_index":         m.chunk_index,
                    "focus_point":         m.focus_point,
                    "memory":              m.memory,
                    "impressive":          m.impressive,
                    "frequency":           m.frequency,
                    "last_used":           m.last_used,
                    "insight":             m.insight,
                    "user_mood":           m.user_mood,
                    "assistant_mood":      m.assistant_mood,
                    "assistant_intensity": m.assistant_intensity,
                    "emoji":               m.emoji,
                    "embedding":           emb_str,
                },
            )

        await self._session.commit()

    # ── read ───────────────────────────────────────────────────────────────────

    async def get_by_id(self, message_id: uuid.UUID) -> Optional[Message]:
        return await self._session.get(Message, message_id)

    async def get_history(
        self,
        account_id: str,
        limit: int = 50,
        before: Optional[datetime] = None,
    ) -> Sequence[Message]:
        q = (
            select(Message)
            .where(Message.account_id == account_id)
            .order_by(Message.created_at.desc())
        )
        if before:
            q = q.where(Message.created_at < before)
        q = q.limit(limit)
        result = await self._session.execute(q)
        rows = result.scalars().all()
        return list(reversed(rows))  # chronological order

    async def get_recent_canonical_pairs(
        self,
        account_id: str,
        limit_pairs: int,
        exclude_pair_ids: Optional[Iterable[uuid.UUID]] = None,
    ) -> list[dict]:
        pair_stmt = (
            select(Message.pair_id)
            .where(Message.account_id == account_id)
            .where(Message.source == "chat")
            .where(Message.message_kind == "canonical")
        )
        if exclude_pair_ids:
            pair_stmt = pair_stmt.where(Message.pair_id.notin_(list(exclude_pair_ids)))
        pair_stmt = (
            pair_stmt
            .group_by(Message.pair_id)
            .order_by(func.max(Message.created_at).desc())
            .limit(limit_pairs)
        )
        pair_ids = [row[0] for row in (await self._session.execute(pair_stmt)).all()]
        if not pair_ids:
            return []

        return await self.get_pairs_render_data(account_id, pair_ids)

    async def get_canonical_pairs_page(
        self,
        account_id: str,
        limit_pairs: int,
        before: Optional[datetime] = None,
    ) -> tuple[list[dict], Optional[datetime], bool]:
        pair_stmt = (
            select(
                Message.pair_id,
                func.max(Message.created_at).label("pair_created_at"),
            )
            .where(Message.account_id == account_id)
            .where(Message.source == "chat")
            .where(Message.message_kind == "canonical")
            .group_by(Message.pair_id)
        )
        if before is not None:
            pair_stmt = pair_stmt.having(func.max(Message.created_at) < before)

        pair_rows = (
            await self._session.execute(
                pair_stmt
                .order_by(func.max(Message.created_at).desc())
                .limit(limit_pairs + 1)
            )
        ).all()

        has_more = len(pair_rows) > limit_pairs
        pair_rows = pair_rows[:limit_pairs]
        pair_ids = [row.pair_id for row in pair_rows]
        if not pair_ids:
            return [], None, False

        rendered = await self.get_pairs_render_data(account_id, pair_ids)
        created_at_map = {row.pair_id: row.pair_created_at for row in pair_rows}
        for item in rendered:
            item["pair_created_at"] = created_at_map.get(item["pair_id"])

        rendered.reverse()
        next_before = created_at_map.get(pair_ids[-1]) if has_more and pair_ids else None
        return rendered, next_before, has_more

    async def get_pairs_render_data(
        self,
        account_id: str,
        pair_ids: Sequence[uuid.UUID],
    ) -> list[dict]:
        if not pair_ids:
            return []

        rows = (
            await self._session.execute(
                select(Message)
                .where(Message.account_id == account_id)
                .where(Message.pair_id.in_(pair_ids))
                .order_by(Message.created_at.asc(), Message.role.asc(), Message.chunk_index.asc())
            )
        ).scalars().all()

        grouped: dict[uuid.UUID, dict] = defaultdict(
            lambda: {
                "pair_id": None,
                "created_at": None,
                "user_canonical": None,
                "assistant_canonical": None,
                "user_chunks": [],
                "assistant_chunks": [],
            }
        )
        for row in rows:
            entry = grouped[row.pair_id]
            entry["pair_id"] = row.pair_id
            entry["created_at"] = entry["created_at"] or row.created_at
            if row.message_kind == "canonical":
                entry[f"{row.role}_canonical"] = row.text
            else:
                entry[f"{row.role}_chunks"].append((row.chunk_index or 0, row.text))

        position = {pair_id: idx for idx, pair_id in enumerate(pair_ids)}
        result: list[dict] = []
        for pair_id, entry in grouped.items():
            user_text = entry["user_canonical"] or " ".join(
                text for _, text in sorted(entry["user_chunks"], key=lambda item: item[0])
            )
            assistant_text = entry["assistant_canonical"] or " ".join(
                text for _, text in sorted(entry["assistant_chunks"], key=lambda item: item[0])
            )
            result.append({
                "pair_id": pair_id,
                "created_at": entry["created_at"],
                "user_text": user_text.strip(),
                "assistant_text": assistant_text.strip(),
            })

        result.sort(key=lambda item: position.get(item["pair_id"], 0))
        return result

    async def count_rows(
        self,
        account_id: str,
        source: Optional[str] = None,
    ) -> int:
        stmt = select(func.count()).where(Message.account_id == account_id)
        if source is not None:
            stmt = stmt.where(Message.source == source)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def count_pairs(
        self,
        account_id: str,
        source: Optional[str] = None,
    ) -> int:
        stmt = select(func.count(func.distinct(Message.pair_id))).where(Message.account_id == account_id)
        if source is not None:
            stmt = stmt.where(Message.source == source)
        result = await self._session.execute(stmt)
        return result.scalar_one()
