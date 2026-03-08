"""Application settings loaded from .env via pydantic-settings."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────────
    # Defaults work out-of-the-box after scripts/setup.js runs.
    # Override via DATABASE_URL in .env for external/managed Postgres.
    DATABASE_URL: str = "postgresql+asyncpg://postgres:1234@localhost:5432/your_own"

    # ── Embeddings ────────────────────────────────────────────────────────────
    EMBEDDING_MODEL_NAME: str = "paraphrase-multilingual-MiniLM-L12-v2"

    # ── Chat memory retrieval ────────────────────────────────────────────────
    CHAT_HISTORY_PAIRS_DEFAULT: int = 6
    CHAT_HISTORY_PAIRS_MIN: int = 1
    CHAT_HISTORY_PAIRS_MAX: int = 10

    MEMORY_CUTOFF_DAYS_DEFAULT: int = 2
    MEMORY_CUTOFF_DAYS_MIN: int = 1
    MEMORY_CUTOFF_DAYS_MAX: int = 10

    # ── ChromaDB ──────────────────────────────────────────────────────────────
    VECTOR_STORE_DIR: str = "infrastructure/vector_store"
    CHROMA_COLLECTION_NAME: str = "key_info"


settings = Settings()
