"""backfill canonical rows for import pairs

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-09
"""
from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO messages (
            message_id, pair_id, account_id, created_at,
            role, text, message_kind, source, focus_point
        )
        SELECT
            gen_random_uuid(),
            pair_id,
            account_id,
            MIN(created_at),
            role,
            string_agg(text, ' ' ORDER BY chunk_index),
            'canonical',
            'import',
            NULL
        FROM messages
        WHERE source = 'import'
          AND message_kind = 'chunk'
          AND pair_id NOT IN (
              SELECT DISTINCT pair_id
              FROM messages
              WHERE source = 'import' AND message_kind = 'canonical'
          )
        GROUP BY pair_id, account_id, role
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM messages
        WHERE source = 'import' AND message_kind = 'canonical'
    """)
