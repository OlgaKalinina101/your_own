"""rebuild HNSW index with tuned parameters and partial filter

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-09
"""
from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_embedding_hnsw")

    op.execute("""
        CREATE INDEX ix_messages_embedding_hnsw
        ON messages USING hnsw (embedding vector_cosine_ops)
        WITH (m = 24, ef_construction = 128)
        WHERE embedding IS NOT NULL AND message_kind = 'chunk'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_embedding_hnsw")

    op.execute("""
        CREATE INDEX ix_messages_embedding_hnsw
        ON messages USING hnsw (embedding vector_cosine_ops)
    """)
