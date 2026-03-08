"""create messages table

Revision ID: 0001
Revises:
Create Date: 2026-03-07
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector extension is installed by setup.js separately before this runs.
    # We enable it here via psql (not alembic) to avoid blocking table creation.
    # The embedding column starts as TEXT and is upgraded to vector(384) by
    # migration 0003 once pgvector is confirmed present.
    op.create_table(
        "messages",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("focus_point", postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column("memory", sa.Text, nullable=True),
        sa.Column("impressive", sa.Integer, nullable=True),
        sa.Column("frequency", sa.Integer, nullable=True),
        sa.Column("last_used", sa.DateTime(timezone=True), nullable=True),
        sa.Column("insight", sa.Text, nullable=True),
        sa.Column("user_mood", sa.String(64), nullable=True),
        sa.Column("assistant_mood", sa.String(64), nullable=True),
        sa.Column("assistant_intensity", sa.Float, nullable=True),
        sa.Column("emoji", sa.String(8), nullable=True),
        # TEXT until pgvector is available; upgraded to vector(384) in migration 0003
        sa.Column("embedding", sa.Text, nullable=True),
    )

    op.create_index("ix_messages_account_id", "messages", ["account_id"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])


def downgrade() -> None:
    op.drop_table("messages")
