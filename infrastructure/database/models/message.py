"""SQLAlchemy ORM model for the `messages` table."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from infrastructure.database.engine import Base


class Message(Base):
    __tablename__ = "messages"

    # ── identity ──────────────────────────────────────────────────────────────
    message_id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pair_id         = Column(UUID(as_uuid=True), nullable=False, index=True)

    account_id      = Column(String(128), nullable=False)
    conversation_id = Column(String(256), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # ── core content ──────────────────────────────────────────────────────────
    role = Column(String(16), nullable=False)
    text = Column(Text, nullable=False)
    message_kind = Column(String(16), nullable=False, default="chunk")
    source = Column(String(16), nullable=False, default="import")
    chunk_index = Column(Integer, nullable=True)

    # ── semantic metadata ─────────────────────────────────────────────────────
    focus_point = Column(ARRAY(Text), nullable=True)
    emoji       = Column(String(8), nullable=True)

    # ── pgvector embedding ────────────────────────────────────────────────────
    # DB column is vector(384); inserted via raw SQL with ::vector cast.
    embedding = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "message_id":       str(self.message_id),
            "pair_id":          str(self.pair_id),
            "account_id":       self.account_id,
            "conversation_id":  self.conversation_id,
            "created_at":       self.created_at.isoformat(),
            "role":             self.role,
            "text":             self.text,
            "message_kind":     self.message_kind,
            "source":           self.source,
            "chunk_index":      self.chunk_index,
            "focus_point":      self.focus_point,
            "emoji":            self.emoji,
        }
