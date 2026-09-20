"""SQLAlchemy ORM model for the ``channel_messages`` table.

A second conversation store, deliberately apart from ``messages``.

``messages`` holds *the* dialogue: pairs of one question and one answer, one
person on each side, and everything downstream — history, reflection timing,
the post-dialogue journal — assumes that shape. A group chat has neither pairs
nor two sides: five people talk, he answers some of them, and most of what is
said is not addressed to him at all. Forcing that into pair rows would mean
inventing pairs, and every reader of ``messages`` would then have to know
which rows are real.

So the group lives here: one row per message, whoever sent it, including his
own. What he is shown of it, and what it is allowed to change in him, is
decided by the readers — see ``infrastructure/telegram``.
"""
from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Boolean, Column, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from infrastructure.database.engine import Base


class ChannelMessage(Base):
    __tablename__ = "channel_messages"
    __table_args__ = (
        # Long polling can hand the same update twice across a restart; the
        # Telegram id is the identity, not our uuid.
        UniqueConstraint("chat_id", "message_id", name="uq_channel_messages_chat_message"),
    )

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(String(128), nullable=False, index=True)

    # Which transport this came over. Only "telegram" today; the column is here
    # so a second one does not need a second table.
    channel    = Column(String(16), nullable=False, default="telegram")
    chat_id    = Column(String(64), nullable=False, index=True)
    message_id = Column(BigInteger, nullable=False)

    sender_id   = Column(String(64), nullable=False)
    sender_name = Column(String(256), nullable=False, default="")
    # The two people in the room he must never confuse with the others.
    is_owner = Column(Boolean, nullable=False, default=False)   # her
    is_self  = Column(Boolean, nullable=False, default=False)   # him

    reply_to_message_id = Column(BigInteger, nullable=True)

    text       = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)

    # vector(384); written through raw SQL with a ::vector cast, like messages.
    embedding = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "account_id": self.account_id,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "is_owner": self.is_owner,
            "is_self": self.is_self,
            "reply_to_message_id": self.reply_to_message_id,
            "text": self.text,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
