"""Reads and writes for the ``channel_messages`` table — the group chat."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database.models.channel_message import ChannelMessage

_INSERT = (
    "INSERT INTO channel_messages ("
    "  id, account_id, channel, chat_id, message_id,"
    "  sender_id, sender_name, is_owner, is_self, reply_to_message_id,"
    "  text, created_at, embedding"
    ") VALUES ("
    "  :id, :account_id, :channel, :chat_id, :message_id,"
    "  :sender_id, :sender_name, :is_owner, :is_self, :reply_to_message_id,"
    "  :text, :created_at, {emb_expr}"
    ") ON CONFLICT (chat_id, message_id) DO NOTHING"
)


def _vector_literal(embedding) -> str | None:
    if embedding is None:
        return None
    if isinstance(embedding, list):
        return "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"
    return str(embedding)


class ChannelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── write ──────────────────────────────────────────────────────────────────

    async def save_many(self, rows: list[ChannelMessage]) -> int:
        """Insert *rows*, skipping any Telegram message already stored.

        Raw SQL for the same reason ``MessageRepository`` uses it: the
        embedding column is ``vector`` and the ORM type is ``Text``, so a bound
        ``None`` would arrive as varchar and be refused. Returns how many rows
        were actually new.
        """
        if not rows:
            return 0
        written = 0
        for row in rows:
            emb = _vector_literal(row.embedding)
            result = await self._session.execute(
                text(_INSERT.format(emb_expr="cast(:embedding as vector)" if emb else "NULL")),
                {
                    "id": str(row.id),
                    "account_id": row.account_id,
                    "channel": row.channel or "telegram",
                    "chat_id": row.chat_id,
                    "message_id": row.message_id,
                    "sender_id": row.sender_id,
                    "sender_name": row.sender_name or "",
                    "is_owner": bool(row.is_owner),
                    "is_self": bool(row.is_self),
                    "reply_to_message_id": row.reply_to_message_id,
                    "text": row.text,
                    "created_at": row.created_at,
                    "embedding": emb,
                },
            )
            written += result.rowcount or 0
        await self._session.commit()
        return written

    # ── read ───────────────────────────────────────────────────────────────────

    async def get_recent(
        self,
        account_id: str,
        chat_id: str,
        limit: int = 30,
        before: Optional[datetime] = None,
    ) -> list[ChannelMessage]:
        """The last *limit* messages of one room, oldest first."""
        q = (
            select(ChannelMessage)
            .where(ChannelMessage.account_id == account_id)
            .where(ChannelMessage.chat_id == chat_id)
            .order_by(ChannelMessage.created_at.desc(), ChannelMessage.message_id.desc())
        )
        if before is not None:
            q = q.where(ChannelMessage.created_at < before)
        rows = (await self._session.execute(q.limit(limit))).scalars().all()
        return list(reversed(rows))

    async def get_since(
        self,
        account_id: str,
        chat_id: str,
        since: datetime,
        limit: int = 200,
    ) -> list[ChannelMessage]:
        """Everything said in the room after *since*, oldest first."""
        q = (
            select(ChannelMessage)
            .where(ChannelMessage.account_id == account_id)
            .where(ChannelMessage.chat_id == chat_id)
            .where(ChannelMessage.created_at > since)
            .order_by(ChannelMessage.created_at.asc(), ChannelMessage.message_id.asc())
            .limit(limit)
        )
        return list((await self._session.execute(q)).scalars().all())

    async def count_since(self, account_id: str, chat_id: str, since: datetime) -> int:
        q = (
            select(func.count())
            .where(ChannelMessage.account_id == account_id)
            .where(ChannelMessage.chat_id == chat_id)
            .where(ChannelMessage.created_at > since)
        )
        return int((await self._session.execute(q)).scalar_one())

    async def list_senders(self, account_id: str, chat_id: Optional[str] = None) -> list[dict]:
        """Everyone the bot has seen speak, for the settings page to pick from.

        Grouped by sender id; the name shown is the most recent one they used.
        His own messages are left out — he is not a candidate for "who is she".
        """
        q = (
            select(
                ChannelMessage.sender_id,
                func.count().label("messages"),
                func.max(ChannelMessage.created_at).label("last_seen"),
            )
            .where(ChannelMessage.account_id == account_id)
            .where(ChannelMessage.is_self.is_(False))
            .group_by(ChannelMessage.sender_id)
            .order_by(func.count().desc())
        )
        if chat_id:
            q = q.where(ChannelMessage.chat_id == chat_id)
        rows = (await self._session.execute(q)).all()

        out: list[dict] = []
        for sender_id, count, last_seen in rows:
            name_q = (
                select(ChannelMessage.sender_name)
                .where(ChannelMessage.account_id == account_id)
                .where(ChannelMessage.sender_id == sender_id)
                .order_by(ChannelMessage.created_at.desc())
                .limit(1)
            )
            name = (await self._session.execute(name_q)).scalar_one_or_none() or ""
            out.append({
                "sender_id": sender_id,
                "sender_name": name,
                "messages": int(count),
                "last_seen": last_seen.isoformat() if last_seen else None,
            })
        return out

    async def list_chats(self, account_id: str) -> list[dict]:
        """Every room the bot has stored messages from."""
        q = (
            select(
                ChannelMessage.chat_id,
                func.count().label("messages"),
                func.max(ChannelMessage.created_at).label("last_seen"),
            )
            .where(ChannelMessage.account_id == account_id)
            .group_by(ChannelMessage.chat_id)
            .order_by(func.max(ChannelMessage.created_at).desc())
        )
        rows = (await self._session.execute(q)).all()
        return [
            {
                "chat_id": chat_id,
                "messages": int(count),
                "last_seen": last_seen.isoformat() if last_seen else None,
            }
            for chat_id, count, last_seen in rows
        ]
