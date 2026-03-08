"""upgrade embedding column from TEXT to vector(384)

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-07

Requires pgvector extension to be installed before running.
setup.js calls ensurePgVector() + CREATE EXTENSION before alembic runs.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable extension (no-op if already enabled)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Drop old text column and replace with proper vector type
    op.drop_column("messages", "embedding")
    op.execute(
        "ALTER TABLE messages ADD COLUMN embedding vector(384)"
    )

    # GIN/HNSW index for fast approximate nearest-neighbour search
    op.execute(
        "CREATE INDEX ix_messages_embedding_hnsw "
        "ON messages USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_embedding_hnsw")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE messages ADD COLUMN embedding TEXT")
