"""drop unused columns and redundant indexes

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-09
"""
from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_DROP_COLUMNS = [
    "memory",
    "impressive",
    "frequency",
    "last_used",
    "insight",
    "user_mood",
    "assistant_mood",
    "assistant_intensity",
]

_DROP_INDEXES = [
    "ix_messages_message_kind",
    "ix_messages_source",
    "ix_messages_account_id",
    "ix_messages_conversation_id",
]


def upgrade() -> None:
    for idx in _DROP_INDEXES:
        op.drop_index(idx, table_name="messages", if_exists=True)

    for col in _DROP_COLUMNS:
        op.drop_column("messages", col)


def downgrade() -> None:
    import sqlalchemy as sa

    op.add_column("messages", sa.Column("assistant_intensity", sa.Float, nullable=True))
    op.add_column("messages", sa.Column("assistant_mood", sa.String(64), nullable=True))
    op.add_column("messages", sa.Column("user_mood", sa.String(64), nullable=True))
    op.add_column("messages", sa.Column("insight", sa.Text, nullable=True))
    op.add_column("messages", sa.Column("last_used", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("frequency", sa.Integer, nullable=True))
    op.add_column("messages", sa.Column("impressive", sa.Integer, nullable=True))
    op.add_column("messages", sa.Column("memory", sa.Text, nullable=True))

    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_account_id", "messages", ["account_id"])
    op.create_index("ix_messages_source", "messages", ["source"])
    op.create_index("ix_messages_message_kind", "messages", ["message_kind"])
