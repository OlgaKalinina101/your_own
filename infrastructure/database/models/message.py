"""SQLAlchemy ORM model for the `messages` table."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from infrastructure.database.engine import Base


class Message(Base):
    __tablename__ = "messages"

    # ── identity ──────────────────────────────────────────────────────────────
    message_id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pair_id         = Column(UUID(as_uuid=True), nullable=False, index=True)
    # pair_id is shared between the user message and its assistant reply.
    # All retrieval operates on pairs: fetch both rows WHERE pair_id = X.

    account_id      = Column(String(128), nullable=False, index=True)
    conversation_id = Column(String(256), nullable=True, index=True)
    # conversation_id — original ChatGPT conversation ID (for deduplication)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # ── core content ──────────────────────────────────────────────────────────
    role = Column(String(16), nullable=False)   # "user" | "assistant"
    text = Column(Text, nullable=False)
    message_kind = Column(String(16), nullable=False, default="chunk", index=True)
    source = Column(String(16), nullable=False, default="import", index=True)
    chunk_index = Column(Integer, nullable=True)

    # ── semantic metadata ─────────────────────────────────────────────────────
    focus_point = Column(ARRAY(Text), nullable=True)  # lemmatised keywords

    # ── memory & reflection ───────────────────────────────────────────────────
    memory  = Column(Text, nullable=True)    # extracted fact about the user
    impressive = Column(Integer, nullable=True)  # importance/weight score
    frequency  = Column(Integer, nullable=True)  # recall count
    last_used  = Column(DateTime(timezone=True), nullable=True)
    insight = Column(Text, nullable=True)    # assistant's own note

    # ── emotional context ─────────────────────────────────────────────────────
    user_mood           = Column(String(64), nullable=True)
    assistant_mood      = Column(String(64), nullable=True)
    assistant_intensity = Column(Float, nullable=True)   # 0.0–1.0
    emoji               = Column(String(8), nullable=True)

    # ── pgvector embedding ────────────────────────────────────────────────────
    # Stored as Text in ORM; the DB column is vector(384).
    # Embeddings are inserted via raw SQL with explicit ::vector cast.
    embedding = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "message_id":           str(self.message_id),
            "pair_id":              str(self.pair_id),
            "account_id":           self.account_id,
            "conversation_id":      self.conversation_id,
            "created_at":           self.created_at.isoformat(),
            "role":                 self.role,
            "text":                 self.text,
            "message_kind":         self.message_kind,
            "source":               self.source,
            "chunk_index":          self.chunk_index,
            "focus_point":          self.focus_point,
            "memory":               self.memory,
            "impressive":           self.impressive,
            "frequency":            self.frequency,
            "last_used":            self.last_used.isoformat() if self.last_used else None,
            "insight":              self.insight,
            "user_mood":            self.user_mood,
            "assistant_mood":       self.assistant_mood,
            "assistant_intensity":  self.assistant_intensity,
            "emoji":                self.emoji,
        }
