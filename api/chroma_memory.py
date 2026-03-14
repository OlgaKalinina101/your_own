"""
GET  /api/chroma/facts          — list all facts for an account (with optional category filter)
DELETE /api/chroma/facts/{id}   — delete a fact by ID
PATCH  /api/chroma/facts/{id}   — update fact text, category, or impressive rating
GET  /api/chroma/categories     — list all distinct categories for an account
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from infrastructure.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chroma", tags=["chroma"], dependencies=[Depends(require_auth)])


def _pipeline():
    from infrastructure.memory.chroma_pipeline import get_chroma_pipeline
    return get_chroma_pipeline()


def _collection():
    from infrastructure.memory.chroma_pipeline import _get_collection
    return _get_collection()


# ── Schemas ───────────────────────────────────────────────────────────────────

class FactOut(BaseModel):
    id: str
    text: str
    category: str
    impressive: int
    frequency: int
    created_at: Optional[str]
    last_used: Optional[str]


class FactPatch(BaseModel):
    text: Optional[str] = None
    category: Optional[str] = None
    impressive: Optional[int] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_fact(doc_id: str, document: str, meta: dict) -> FactOut:
    try:
        impressive = int(meta.get("impressive", 1))
    except (ValueError, TypeError):
        impressive = 1
    try:
        frequency = int(meta.get("frequency", 0))
    except (ValueError, TypeError):
        frequency = 0
    return FactOut(
        id=doc_id,
        text=document,
        category=meta.get("category", ""),
        impressive=impressive,
        frequency=frequency,
        created_at=meta.get("created_at"),
        last_used=meta.get("last_used"),
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/facts", response_model=list[FactOut])
async def list_facts(
    account_id: str = Query("default"),
    category: Optional[str] = Query(None),
    sort: str = Query("created_at"),   # created_at | impressive | frequency
):
    col = _collection()
    if col is None:
        return []

    try:
        if category:
            where: dict = {"$and": [{"account_id": account_id}, {"category": category}]}
        else:
            where = {"account_id": account_id}

        result = col.get(where=where, include=["documents", "metadatas"])
    except Exception as exc:
        logger.warning("[chroma] list_facts failed: %s", exc)
        return []

    ids = result.get("ids") or []
    docs = result.get("documents") or []
    metas = result.get("metadatas") or []

    facts = [_row_to_fact(fid, doc, meta) for fid, doc, meta in zip(ids, docs, metas)]

    # Sort
    if sort == "impressive":
        facts.sort(key=lambda f: f.impressive, reverse=True)
    elif sort == "frequency":
        facts.sort(key=lambda f: f.frequency, reverse=True)
    else:
        facts.sort(key=lambda f: f.created_at or "", reverse=True)

    return facts


@router.get("/categories")
async def list_categories(account_id: str = Query("default")):
    col = _collection()
    if col is None:
        return {"categories": []}

    try:
        result = col.get(where={"account_id": account_id}, include=["metadatas"])
    except Exception as exc:
        logger.warning("[chroma] list_categories failed: %s", exc)
        return {"categories": []}

    metas = result.get("metadatas") or []
    cats = sorted({m.get("category", "") for m in metas if m.get("category")})
    return {"categories": cats}


@router.delete("/facts/{fact_id}", status_code=204)
async def delete_fact(fact_id: str, account_id: str = Query("default")):
    col = _collection()
    if col is None:
        raise HTTPException(status_code=503, detail="Chroma unavailable")

    try:
        existing = col.get(ids=[fact_id], include=["metadatas"])
        if not existing or not existing["ids"]:
            raise HTTPException(status_code=404, detail="Fact not found")
        meta = existing["metadatas"][0]
        if meta.get("account_id") != account_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        col.delete(ids=[fact_id])
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[chroma] delete_fact failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/facts/{fact_id}", response_model=FactOut)
async def update_fact(fact_id: str, body: FactPatch, account_id: str = Query("default")):
    col = _collection()
    if col is None:
        raise HTTPException(status_code=503, detail="Chroma unavailable")

    try:
        existing = col.get(ids=[fact_id], include=["embeddings", "documents", "metadatas"])
        if not existing or not existing["ids"]:
            raise HTTPException(status_code=404, detail="Fact not found")
        old_meta = existing["metadatas"][0]
        old_emb  = existing["embeddings"][0]
        old_doc  = existing["documents"][0]

        if old_meta.get("account_id") != account_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        new_doc = body.text if body.text is not None else old_doc
        new_meta = old_meta.copy()
        if body.category is not None:
            new_meta["category"] = body.category
        if body.impressive is not None:
            new_meta["impressive"] = max(1, min(4, body.impressive))

        # Re-embed only if text changed
        if body.text is not None and body.text != old_doc:
            from infrastructure.memory.embedder import embed_one
            new_emb = embed_one(new_doc) or old_emb
        else:
            new_emb = old_emb

        col.delete(ids=[fact_id])
        col.add(
            documents=[new_doc],
            embeddings=[new_emb],
            metadatas=[new_meta],
            ids=[fact_id],
        )
        return _row_to_fact(fact_id, new_doc, new_meta)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[chroma] update_fact failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
