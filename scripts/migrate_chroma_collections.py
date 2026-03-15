"""Migrate legacy Chroma collections into the current project's store.

Mappings:
  - old `key_info`     -> new `key_info`
  - old `victor_notes` -> new `workbench_archive`

The legacy store may contain richer or slightly different metadata. This script
normalises the fields expected by the current project while preserving extra
metadata when it is safe to do so.
"""
from __future__ import annotations

import argparse
from typing import Any

import chromadb


DEFAULT_OLD_PATH = r"C:\Users\User\PycharmProjects\Victor_AI_Core\infrastructure\vector_store"
DEFAULT_NEW_PATH = r"C:\Users\User\PycharmProjects\your_own\infrastructure\vector_store"


def _safe_meta(**kwargs: Any) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


def _normalise_key_info(meta: dict[str, Any], account_id: str) -> dict[str, Any]:
    impressive = meta.get("impressive", 1)
    frequency = meta.get("frequency", 0)
    try:
        impressive = int(impressive)
    except (TypeError, ValueError):
        impressive = 1
    try:
        frequency = int(frequency)
    except (TypeError, ValueError):
        frequency = 0

    return _safe_meta(
        account_id=account_id,
        category=meta.get("category") or "",
        subcategory=meta.get("subcategory"),
        impressive=max(1, min(4, impressive)),
        frequency=max(0, frequency),
        last_used=meta.get("last_used"),
        created_at=meta.get("created_at"),
        has_critical=meta.get("has_critical"),
        mood=meta.get("mood"),
        mood_level=meta.get("mood_level"),
        source=meta.get("source"),
    )


def _normalise_archive(meta: dict[str, Any], account_id: str) -> dict[str, Any]:
    return _safe_meta(
        account_id=account_id,
        source=meta.get("source") or "workbench",
        created_at=meta.get("created_at") or meta.get("last_used"),
    )


def _iter_collection_rows(collection, where: dict[str, Any] | None, batch_size: int):
    offset = 0
    while True:
        batch = collection.get(
            where=where,
            include=["documents", "metadatas", "embeddings"],
            limit=batch_size,
            offset=offset,
        )
        ids = batch.get("ids") or []
        if not ids:
            return
        yield batch
        offset += len(ids)


def _ensure_collection(client, name: str):
    return client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def _delete_target_account_rows(target_collection, account_id: str) -> None:
    target_collection.delete(where={"account_id": account_id})


def migrate_collection(
    src_collection,
    dst_collection,
    *,
    source_account_id: str | None,
    target_account_id: str | None,
    batch_size: int,
    normalise_meta,
) -> int:
    where = {"account_id": source_account_id} if source_account_id else None
    migrated = 0

    for batch in _iter_collection_rows(src_collection, where=where, batch_size=batch_size):
        ids = batch.get("ids")
        docs = batch.get("documents")
        metas = batch.get("metadatas")
        embs = batch.get("embeddings")

        if ids is None:
            ids = []
        if docs is None:
            docs = []
        if metas is None:
            metas = []
        if embs is None:
            embs = []

        out_ids: list[str] = []
        out_docs: list[str] = []
        out_metas: list[dict[str, Any]] = []
        out_embs: list[list[float]] = []

        for doc_id, doc, meta, emb in zip(ids, docs, metas, embs):
            meta = meta or {}
            row_account_id = meta.get("account_id") or source_account_id or "default"
            if source_account_id and row_account_id != source_account_id:
                continue
            if not doc or emb is None:
                continue

            out_ids.append(doc_id)
            out_docs.append(doc)
            out_metas.append(normalise_meta(meta, target_account_id or row_account_id))
            out_embs.append(emb)

        if not out_ids:
            continue

        dst_collection.upsert(
            ids=out_ids,
            documents=out_docs,
            metadatas=out_metas,
            embeddings=out_embs,
        )
        migrated += len(out_ids)

    return migrated


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate legacy Chroma collections.")
    parser.add_argument("--old-path", default=DEFAULT_OLD_PATH)
    parser.add_argument("--new-path", default=DEFAULT_NEW_PATH)
    parser.add_argument(
        "--source-account-id",
        default=None,
        help="Optional source account_id filter in the legacy store.",
    )
    parser.add_argument(
        "--target-account-id",
        default=None,
        help="Optional target account_id override in the new store.",
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument(
        "--replace-target",
        action="store_true",
        help="Delete existing target rows for the selected target account before import.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    old_client = chromadb.PersistentClient(path=args.old_path)
    new_client = chromadb.PersistentClient(path=args.new_path)

    src_key_info = old_client.get_collection(name="key_info")
    src_archive = old_client.get_collection(name="victor_notes")

    dst_key_info = _ensure_collection(new_client, "key_info")
    dst_archive = _ensure_collection(new_client, "workbench_archive")

    target_account_id = args.target_account_id or args.source_account_id
    if args.replace_target and target_account_id:
        _delete_target_account_rows(dst_key_info, target_account_id)
        _delete_target_account_rows(dst_archive, target_account_id)

    facts_count = migrate_collection(
        src_key_info,
        dst_key_info,
        source_account_id=args.source_account_id,
        target_account_id=target_account_id,
        batch_size=args.batch_size,
        normalise_meta=_normalise_key_info,
    )
    archive_count = migrate_collection(
        src_archive,
        dst_archive,
        source_account_id=args.source_account_id,
        target_account_id=target_account_id,
        batch_size=args.batch_size,
        normalise_meta=_normalise_archive,
    )

    scope = f"{args.source_account_id or 'ALL'} -> {target_account_id or 'same'}"
    print(
        f"Migrated account={scope}: "
        f"key_info={facts_count}, workbench_archive={archive_count}"
    )


if __name__ == "__main__":
    main()
