"""add targeted partial and GIN indexes

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-09
"""
from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX ix_messages_chat_canonical
        ON messages (account_id, created_at DESC, pair_id)
        WHERE source = 'chat' AND message_kind = 'canonical'
    """)

    op.execute("""
        CREATE INDEX ix_messages_focus_point_gin
        ON messages USING gin (focus_point)
        WHERE focus_point IS NOT NULL
    """)

    op.execute("""
        CREATE INDEX ix_messages_pair_render
        ON messages (pair_id, account_id)
        WHERE message_kind IN ('canonical', 'chunk')
    """)


def downgrade() -> None:
    op.drop_index("ix_messages_pair_render", table_name="messages")
    op.drop_index("ix_messages_focus_point_gin", table_name="messages")
    op.drop_index("ix_messages_chat_canonical", table_name="messages")
