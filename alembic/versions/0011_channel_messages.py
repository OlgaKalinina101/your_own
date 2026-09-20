"""create channel_messages table

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-20

The group chat he takes part in. Kept apart from ``messages`` on purpose:
that table is pairs between two people, this one is a room.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False, server_default="telegram"),
        sa.Column("chat_id", sa.String(64), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_id", sa.String(64), nullable=False),
        sa.Column("sender_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_self", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reply_to_message_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chat_id", "message_id", name="uq_channel_messages_chat_message"),
    )
    # Same embedding space as messages.embedding, for the search source that
    # lets him look the room up the way he looks up the dialogue.
    op.execute("ALTER TABLE channel_messages ADD COLUMN embedding vector(384)")
    op.create_index("ix_channel_messages_account_id", "channel_messages", ["account_id"])
    op.create_index("ix_channel_messages_chat_id", "channel_messages", ["chat_id"])
    op.create_index("ix_channel_messages_created_at", "channel_messages", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_channel_messages_created_at", table_name="channel_messages")
    op.drop_index("ix_channel_messages_chat_id", table_name="channel_messages")
    op.drop_index("ix_channel_messages_account_id", table_name="channel_messages")
    op.drop_table("channel_messages")
