"""
ChromaDB pipeline for semantic long-term memory (key facts).

Stores AI-extracted facts (via KEY_INFO_PROMPTS) as documents with metadata:
  account_id, category, impressive (1-4), frequency, last_used, created_at

Retrieval is multi-query with keyword boost, impressive boost, and recency penalty.
Ported from the Kotlin/Android victor_ai project PersonaEmbeddingPipeline.
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


def _get_morph():
    try:
        import pymorphy3
        return pymorphy3.MorphAnalyzer()
    except Exception:
        return None


def _get_ruwordnet():
    try:
        from ruwordnet import RuWordNet
        return RuWordNet()
    except Exception:
        return None


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
        self._morph = _get_morph()
        self._wn = None   # lazy, only loaded if needed
        self._wn_loaded = False

    def _ruwordnet(self):
        if not self._wn_loaded:
            self._wn = _get_ruwordnet()
            self._wn_loaded = True
        return self._wn

    # ── Write ──────────────────────────────────────────────────────────────────

    DEDUP_DISTANCE_THRESHOLD = 0.35  # cosine distance; below = "same fact"

    def add_entry(
        self,
        account_id: str,
        memory: str,
        category: str,
        impressive: int = 1,
        external_id: Optional[str] = None,
    ) -> str:
        """
        Store one fact with deduplication.

        Before saving, queries Chroma for the most similar existing fact.
        If cosine distance < DEDUP_DISTANCE_THRESHOLD:
          - If the new fact is more impressive or longer, replace the old one.
          - Otherwise skip (return old ID).
        Returns the document ID.
        """
        from infrastructure.memory.embedder import embed_one
        col = _get_collection()
        if col is None:
            logger.warning("[chroma] add_entry skipped — collection unavailable")
            return external_id or str(uuid.uuid4())

        embedding = embed_one(memory)
        if embedding is None:
            logger.warning("[chroma] add_entry skipped — embedding unavailable")
            return external_id or str(uuid.uuid4())

        # ── Dedup check ───────────────────────────────────────────────────────
        try:
            existing = col.query(
                query_embeddings=[embedding],
                n_results=1,
                where={"account_id": account_id},
                include=["documents", "metadatas", "distances"],
            )
            if existing and existing["ids"] and existing["ids"][0]:
                old_id = existing["ids"][0][0]
                old_doc = existing["documents"][0][0]
                old_meta = existing["metadatas"][0][0]
                distance = existing["distances"][0][0]

                if distance < self.DEDUP_DISTANCE_THRESHOLD:
                    old_imp = int(old_meta.get("impressive", 1))
                    new_is_better = (
                        impressive > old_imp
                        or (impressive == old_imp and len(memory) > len(old_doc))
                    )
                    if new_is_better:
                        col.delete(ids=[old_id])
                        logger.info(
                            "[chroma] dedup: replacing old fact id=%s (dist=%.3f, imp %d→%d)",
                            old_id, distance, old_imp, impressive,
                        )
                    else:
                        logger.info(
                            "[chroma] dedup: skipping — existing fact id=%s is good enough (dist=%.3f)",
                            old_id, distance,
                        )
                        return old_id
        except Exception as exc:
            logger.warning("[chroma] dedup check failed (proceeding with save): %s", exc)

        # ── Save ──────────────────────────────────────────────────────────────
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

        queries = [message]
        if len(message) > 80:
            queries.extend(self._split_to_sentences(message)[:4])

        all_results: dict[str, dict] = {}
        for q in queries:
            for r in self._query_similar(account_id, q, per_query_k, days_cutoff):
                if r["id"] not in all_results or r["score"] < all_results[r["id"]]["score"]:
                    all_results[r["id"]] = r

        all_results = self._apply_keyword_boost(all_results, keywords)
        all_results = self._apply_impressive_boost(all_results)
        all_results = self._apply_recency_boost(all_results)

        sorted_results = sorted(all_results.values(), key=lambda x: x["score"])
        return sorted_results[:top_k]

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

    # ── NLP helpers ───────────────────────────────────────────────────────────

    def _split_to_sentences(self, message: str) -> list[str]:
        parts = re.split(r"[.!?]+", message)
        return [s.strip() for s in parts if len(s.strip()) > 25]

    def _normalize_word(self, word: str) -> str:
        if self._morph:
            try:
                return self._morph.parse(word)[0].normal_form
            except Exception:
                pass
        return word

    def _get_synonyms(self, word: str) -> set[str]:
        wn = self._ruwordnet()
        if not wn:
            return set()
        synonyms: set[str] = set()
        try:
            for synset in wn.get_synsets(word)[:3]:
                for sense in synset.senses:
                    lemma = sense.name.lower()
                    if lemma != word:
                        synonyms.add(lemma)
                for hypo in synset.hyponyms[:5]:
                    for sense in hypo.senses[:3]:
                        synonyms.add(sense.name.lower())
        except Exception:
            pass
        return synonyms

    _STOP = frozenset({
        "а", "и", "в", "на", "с", "у", "к", "о", "из", "за", "по", "от", "до",
        "что", "как", "это", "так", "ты", "я", "мы", "он", "она", "они", "вы",
        "не", "да", "но", "же", "ли", "бы", "то", "ещё", "еще", "уже", "вот",
        "все", "всё", "мне", "меня", "тебе", "тебя", "нам", "нас", "мой", "твой",
        "если", "когда", "чтобы", "потому", "очень", "только", "просто", "прям",
        "хочешь", "хочу", "могу", "можешь", "буду", "будет", "есть", "был", "была",
        "опять", "снова", "теперь", "сейчас", "тоже", "также", "быть", "этот",
    })

    def _extract_keywords(self, message: str, expand_synonyms: bool = True) -> set[str]:
        clean = re.sub(r"[^\w\s]", " ", message.lower())
        words = clean.split()
        keywords: set[str] = set()
        base_lemmas: list[str] = []
        for w in words:
            if len(w) > 3 and w not in self._STOP:
                lemma = self._normalize_word(w)
                if lemma not in self._STOP:
                    keywords.add(lemma)
                    base_lemmas.append(lemma)
        if expand_synonyms:
            for lemma in base_lemmas:
                keywords.update(self._get_synonyms(lemma))
        return keywords

    def _extract_lemmas(self, text: str) -> set[str]:
        clean = re.sub(r"[^\w\s]", " ", text.lower())
        return {self._normalize_word(w) for w in clean.split() if len(w) > 3}


# ── Module-level singleton ─────────────────────────────────────────────────────

_pipeline: Optional[ChromaMemoryPipeline] = None


def get_chroma_pipeline() -> ChromaMemoryPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = ChromaMemoryPipeline()
    return _pipeline
