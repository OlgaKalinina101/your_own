"""
Sentence embedding helper.

Uses paraphrase-multilingual-MiniLM-L12-v2 (384-dim).
Trained on 50+ languages including Russian — significantly better than
all-MiniLM-L6-v2 for morphologically rich languages (RU, UK, etc.).
Same vector dimension (384), so no DB schema changes needed.

The model is loaded once at module import time and reused for all
batches. Encoding is synchronous (CPU) — call from a thread pool
when used inside async handlers.
"""
from __future__ import annotations

import logging
from typing import Sequence

from settings import settings

logger = logging.getLogger(__name__)

MODEL_NAME = settings.EMBEDDING_MODEL_NAME

_model = None


def _load_model():
    global _model
    if _model is not None:
        return _model
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
        logger.info("[embedder] model loaded: %s (384-dim)", MODEL_NAME)
    except Exception as exc:
        logger.warning("[embedder] SentenceTransformer not available: %s", exc)
        _model = None
    return _model


def embed_texts(texts: Sequence[str]) -> list[list[float] | None]:
    """
    Return a list of 384-dim float vectors, one per input text.
    Returns None in place of any vector if the model is unavailable.
    """
    model = _load_model()
    if model is None:
        return [None] * len(texts)

    try:
        vecs = model.encode(list(texts), show_progress_bar=False, convert_to_numpy=True)
        return [v.tolist() for v in vecs]
    except Exception as exc:
        logger.warning("[embedder] encode failed: %s", exc)
        return [None] * len(texts)


def embed_one(text: str) -> list[float] | None:
    result = embed_texts([text])
    return result[0] if result else None
