"""add pair_id and conversation_id to messages

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-07
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pair_id — shared UUID for user+assistant pair
    # Existing rows get a unique UUID each (they have no pair yet)
    op.add_column(
        "messages",
        sa.Column(
            "pair_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,   # nullable during migration, filled below
        ),
    )

    # Back-fill: give every existing row its own pair_id
    op.execute("UPDATE messages SET pair_id = gen_random_uuid() WHERE pair_id IS NULL")

    # Now make it non-nullable
    op.alter_column("messages", "pair_id", nullable=False)

    op.create_index("ix_messages_pair_id", "messages", ["pair_id"])

    # conversation_id — original ChatGPT conversation ID
    op.add_column(
        "messages",
        sa.Column("conversation_id", sa.String(256), nullable=True),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_conversation_id", "messages")
    op.drop_column("messages", "conversation_id")
    op.drop_index("ix_messages_pair_id", "messages")
    op.drop_column("messages", "pair_id")
