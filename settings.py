"""Settings decided when the app is deployed, read from ``.env``.

There are two configuration modules and the difference between them is who
decides and when:

* **this one** — where the database is, which embedding model, which Chroma
  collections, and the *bounds* on what the user is allowed to choose. Changing
  any of it needs a restart, and changing the embedding model or a collection
  name needs a re-index on top. Nobody edits this from the UI.
* :mod:`infrastructure.settings_store` — what the person using it decides while
  it runs: API keys, the model, temperature, timezone, reflection timing, which
  skills are on. Written through the REST API, in force on the next request.

``history_pairs`` and ``memory_cutoff_days`` appear in both, which looks like
duplication and is not: the value is the user's, the bounds are ours. The chat
endpoint clamps one with the other (``_clamp`` in ``api/chat.py``), so a stored
value outside the range is quietly corrected rather than honoured —
``tests/test_config_boundary.py`` keeps the two from drifting apart.
"""
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
    CHROMA_ARCHIVE_COLLECTION_NAME: str = "workbench_archive"


settings = Settings()
