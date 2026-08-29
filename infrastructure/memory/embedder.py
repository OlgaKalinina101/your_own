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

#: Why the last load attempt failed, or None if none has failed. Without this,
#: "not loaded" and "cannot be loaded" look identical from the outside, and the
#: difference is the whole point: the first is idle, the second means his
#: long-term recall has quietly dropped to keyword matching.
_load_error: str | None = None


def _load_model():
    global _model, _load_error
    if _model is not None:
        return _model
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
        _load_error = None
        logger.info("[embedder] model loaded: %s (384-dim)", MODEL_NAME)
    except Exception as exc:
        logger.warning("[embedder] SentenceTransformer not available: %s", exc)
        _model = None
        _load_error = str(exc)
    return _model


def status() -> tuple[str, str]:
    """(state, detail) — what is true about the model right now.

    Deliberately does not load anything: loading takes about a minute the first
    time, and this is read while he is mid-thought.
    """
    if _model is not None:
        return "loaded", MODEL_NAME
    if _load_error:
        return "failed", _load_error
    return "not_loaded", MODEL_NAME


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
