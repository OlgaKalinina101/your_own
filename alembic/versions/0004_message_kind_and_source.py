"""add message_kind, source and chunk_index to messages

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-07
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("message_kind", sa.String(length=16), nullable=False, server_default="chunk"),
    )
    op.add_column(
        "messages",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="import"),
    )
    op.add_column(
        "messages",
        sa.Column("chunk_index", sa.Integer(), nullable=True),
    )

    op.create_index("ix_messages_message_kind", "messages", ["message_kind"], unique=False)
    op.create_index("ix_messages_source", "messages", ["source"], unique=False)
    op.create_index(
        "ix_messages_account_source_kind_created_at",
        "messages",
        ["account_id", "source", "message_kind", "created_at"],
        unique=False,
    )

    op.alter_column("messages", "message_kind", server_default=None)
    op.alter_column("messages", "source", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_messages_account_source_kind_created_at", table_name="messages")
    op.drop_index("ix_messages_source", table_name="messages")
    op.drop_index("ix_messages_message_kind", table_name="messages")
    op.drop_column("messages", "chunk_index")
    op.drop_column("messages", "source")
    op.drop_column("messages", "message_kind")
