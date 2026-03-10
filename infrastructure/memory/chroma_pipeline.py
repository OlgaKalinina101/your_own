"""
ChromaDB pipeline for semantic long-term memory (key facts).

Stores AI-extracted facts (via KEY_INFO_PROMPTS) as documents with metadata:
  account_id, category, impressive (1-4), frequency, last_used, created_at

Retrieval is multi-query with keyword boost, impressive boost, and recency penalty.
Ported from the Kotlin/Android victor_ai project PersonaEmbeddingPipeline.

NLP (lemmatisation, synonyms, stop-words) is delegated to focus_point.py which
supports both Russian (pymorphy3 + RuWordNet) and English (NLTK WordNet).
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Chroma client (lazy singleton) ────────────────────────────────────────────

_chroma_client = None
_chroma_collection = None


def _get_client():
    global _chroma_client
    if _chroma_client is not None:
        return _chroma_client
    try:
        import chromadb
        from settings import settings
        _chroma_client = chromadb.PersistentClient(path=settings.VECTOR_STORE_DIR)
        logger.info("[chroma] client initialised at %s", settings.VECTOR_STORE_DIR)
    except Exception as exc:
        logger.warning("[chroma] client init failed: %s", exc)
        _chroma_client = None
    return _chroma_client


def _get_collection():
    global _chroma_collection
    if _chroma_collection is not None:
        return _chroma_collection
    client = _get_client()
    if client is None:
        return None
    try:
        from settings import settings
        _chroma_collection = client.get_or_create_collection(
            name=settings.CHROMA_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("[chroma] collection '%s' ready", settings.CHROMA_COLLECTION_NAME)
    except Exception as exc:
        logger.warning("[chroma] collection init failed: %s", exc)
        _chroma_collection = None
    return _chroma_collection


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_metadata(**kwargs) -> dict:
    """Strip None values — ChromaDB rejects None in metadata."""
    return {k: v for k, v in kwargs.items() if v is not None}


# ── Pipeline ──────────────────────────────────────────────────────────────────

class ChromaMemoryPipeline:
    """
    Semantic long-term memory backed by ChromaDB.

    Each document is an AI-extracted fact like:
        "Работа: Начальница жёсткая, придиралась."

    Metadata: account_id, category, impressive (1-4),
              frequency, last_used, created_at.
    """

    def __init__(self) -> None:
        pass

    # ── Write ──────────────────────────────────────────────────────────────────

    DEDUP_DISTANCE_THRESHOLD = 0.35  # cosine distance; below = "potentially same fact"

    def find_similar(
        self,
        account_id: str,
        memory: str,
    ) -> Optional[dict]:
        """
        Find the most similar existing fact. Returns dict with
        id, text, metadata, distance — or None if nothing close enough.
        """
        from infrastructure.memory.embedder import embed_one
        col = _get_collection()
        if col is None:
            return None

        embedding = embed_one(memory)
        if embedding is None:
            return None

        try:
            existing = col.query(
                query_embeddings=[embedding],
                n_results=1,
                where={"account_id": account_id},
                include=["documents", "metadatas", "distances"],
            )
            if existing and existing["ids"] and existing["ids"][0]:
                distance = existing["distances"][0][0]
                if distance < self.DEDUP_DISTANCE_THRESHOLD:
                    return {
                        "id": existing["ids"][0][0],
                        "text": existing["documents"][0][0],
                        "metadata": existing["metadatas"][0][0],
                        "distance": distance,
                    }
        except Exception as exc:
            logger.warning("[chroma] find_similar failed: %s", exc)
        return None

    def add_entry(
        self,
        account_id: str,
        memory: str,
        category: str,
        impressive: int = 1,
        external_id: Optional[str] = None,
    ) -> str:
        """Store one fact (no dedup — caller handles dedup via find_similar + AI)."""
        from infrastructure.memory.embedder import embed_one
        col = _get_collection()
        if col is None:
            logger.warning("[chroma] add_entry skipped — collection unavailable")
            return external_id or str(uuid.uuid4())

        embedding = embed_one(memory)
        if embedding is None:
            logger.warning("[chroma] add_entry skipped — embedding unavailable")
            return external_id or str(uuid.uuid4())

        doc_id = external_id or str(uuid.uuid4())
        metadata = _safe_metadata(
            account_id=account_id,
            category=category,
            impressive=impressive,
            frequency=0,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        col.add(
            documents=[memory],
            embeddings=[embedding],
            metadatas=[metadata],
            ids=[doc_id],
        )
        logger.info("[chroma] saved fact id=%s cat=%s impressive=%d", doc_id, category, impressive)
        return doc_id

    def delete_entry(self, doc_id: str) -> None:
        """Delete a fact by ID."""
        col = _get_collection()
        if col is None:
            return
        try:
            col.delete(ids=[doc_id])
            logger.info("[chroma] deleted fact id=%s", doc_id)
        except Exception as exc:
            logger.warning("[chroma] delete_entry failed for %s: %s", doc_id, exc)

    # ── Read ───────────────────────────────────────────────────────────────────

    def query_similar_multi(
        self,
        account_id: str,
        message: str,
        top_k: int = 5,
        per_query_k: int = 3,
        days_cutoff: int = 2,
    ) -> list[dict]:
        """
        Multi-query search: splits message into sentences, searches each,
        deduplicates, applies keyword/impressive/recency boosts, returns top_k.
        """
        keywords = self._extract_keywords(message)
        norm_query = re.sub(r"[^\w\s]", " ", message.lower()).strip()

        queries = [message]
        if len(message) > 80:
            queries.extend(self._split_to_sentences(message)[:4])

        all_results: dict[str, dict] = {}
        for q in queries:
            for r in self._query_similar(account_id, q, per_query_k, days_cutoff):
                if r["id"] not in all_results or r["score"] < all_results[r["id"]]["score"]:
                    all_results[r["id"]] = r

        all_results = self._apply_keyword_boost(all_results, keywords)
        all_results = self._apply_exact_boost(all_results, norm_query, keywords)
        all_results = self._apply_impressive_boost(all_results)
        all_results = self._apply_recency_boost(all_results)

        sorted_results = sorted(all_results.values(), key=lambda x: x["score"])
        top = sorted_results[:top_k]
        for r in top:
            logger.info(
                "[chroma] fact score=%.3f imp=%s text=%s",
                r["score"],
                r.get("metadata", {}).get("impressive", "?"),
                r["text"][:80],
            )
        return top

    MAX_DISTANCE = 0.65

    def _query_similar(
        self,
        account_id: str,
        query: str,
        top_k: int = 3,
        days_cutoff: int = 2,
    ) -> list[dict]:
        from infrastructure.memory.embedder import embed_one
        col = _get_collection()
        if col is None:
            return []

        embedding = embed_one(query)
        if embedding is None:
            return []

        try:
            results = col.query(
                query_embeddings=[embedding],
                n_results=top_k * 2,
                where={"account_id": account_id},
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            logger.warning("[chroma] query failed: %s", exc)
            return []

        from datetime import timezone as _tz
        threshold = datetime.now(_tz.utc) - timedelta(days=days_cutoff)
        filtered: list[dict] = []

        for res_id, doc, meta, score in zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            if score > self.MAX_DISTANCE:
                logger.debug("[chroma] skip id=%s dist=%.3f > %.2f text=%s", res_id, score, self.MAX_DISTANCE, doc[:60])
                continue

            created_str = meta.get("created_at") or meta.get("last_used")
            if created_str:
                try:
                    dt = datetime.fromisoformat(created_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=_tz.utc)
                    if dt >= threshold:
                        continue
                except ValueError:
                    pass

            filtered.append({
                "id": res_id,
                "text": doc,
                "metadata": meta,
                "score": round(score, 3),
            })
            if len(filtered) >= top_k:
                break

        return filtered

    # ── Usage tracking ─────────────────────────────────────────────────────────

    def update_usage(self, doc_id: str) -> None:
        """Increment frequency and update last_used for a retrieved fact."""
        col = _get_collection()
        if col is None:
            return
        try:
            result = col.get(ids=[doc_id], include=["embeddings", "documents", "metadatas"])
            if not result or not result["ids"]:
                return
            old_meta = result["metadatas"][0]
            old_emb  = result["embeddings"][0]
            doc      = result["documents"][0]
            col.delete(ids=[doc_id])
            new_meta = old_meta.copy()
            new_meta["frequency"] = int(old_meta.get("frequency", 0)) + 1
            new_meta["last_used"] = datetime.now(timezone.utc).isoformat()
            col.add(documents=[doc], embeddings=[old_emb], metadatas=[new_meta], ids=[doc_id])
            logger.debug("[chroma] updated usage for %s", doc_id)
        except Exception as exc:
            logger.warning("[chroma] update_usage failed for %s: %s", doc_id, exc)

    # ── Boost helpers ──────────────────────────────────────────────────────────

    def _apply_keyword_boost(self, results: dict, keywords: set[str], boost: float = 0.25) -> dict:
        for r in results.values():
            text_lemmas = self._extract_lemmas(r["text"])
            matched = keywords & text_lemmas
            if matched:
                r["score"] = max(0.01, r["score"] - len(matched) * boost)
            for kw in keywords:
                if kw in r["text"].lower():
                    r["score"] = max(0.01, r["score"] - boost)
        return results

    def _apply_exact_boost(self, results: dict, norm_query: str, query_tokens: set[str]) -> dict:
        for r in results.values():
            norm_text = re.sub(r"[^\w\s]", " ", r["text"].lower()).strip()
            text_tokens = set(norm_text.split())
            if norm_query == norm_text:
                r["score"] = max(0.01, r["score"] - 0.15)
            if query_tokens and query_tokens.issubset(text_tokens):
                r["score"] = max(0.01, r["score"] - 0.10)
        return results

    def _apply_impressive_boost(self, results: dict) -> dict:
        for r in results.values():
            try:
                imp = int(r.get("metadata", {}).get("impressive", 0))
            except (ValueError, TypeError):
                imp = 0
            if imp >= 4:
                r["score"] = max(0.01, r["score"] - 0.12)
            elif imp == 3:
                r["score"] = max(0.01, r["score"] - 0.05)
        return results

    def _apply_recency_boost(self, results: dict) -> dict:
        now = datetime.now()
        for r in results.values():
            try:
                imp = int(r.get("metadata", {}).get("impressive", 0))
            except (ValueError, TypeError):
                imp = 0
            if imp >= 4:
                continue
            date_str = r.get("metadata", {}).get("last_used") or r.get("metadata", {}).get("created_at")
            if not date_str:
                continue
            try:
                mem_dt = datetime.fromisoformat(date_str.replace("+00:00", "").replace("Z", "")).replace(tzinfo=None)
                days_ago = (now - mem_dt).days
                if days_ago > 60:
                    r["score"] += min(0.1, (days_ago - 60) * 0.001)
            except Exception:
                pass
        return results

    # ── NLP helpers (delegated to focus_point.py for RU+EN) ─────────────────

    @staticmethod
    def _split_to_sentences(message: str) -> list[str]:
        from infrastructure.memory.focus_point import split_to_sentences
        return [s for s in split_to_sentences(message, min_len=25)]

    @staticmethod
    def _extract_keywords(message: str, expand_synonyms: bool = True) -> set[str]:
        from infrastructure.memory.focus_point import FocusPointPipeline, detect_language
        lang = detect_language(message)
        pipeline = FocusPointPipeline(language=lang, expand_synonyms=expand_synonyms)
        return set(pipeline.extract(message))

    @staticmethod
    def _extract_lemmas(text: str) -> set[str]:
        from infrastructure.memory.focus_point import FocusPointPipeline, detect_language
        lang = detect_language(text)
        pipeline = FocusPointPipeline(language=lang, expand_synonyms=False)
        return set(pipeline.extract(text))


# ── Module-level singleton ─────────────────────────────────────────────────────

_pipeline: Optional[ChromaMemoryPipeline] = None


def get_chroma_pipeline() -> ChromaMemoryPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = ChromaMemoryPipeline()
    return _pipeline
