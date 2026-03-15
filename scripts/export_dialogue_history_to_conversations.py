"""Export legacy dialogue_history rows into ChatGPT-style conversations.json.

Usage:
    python scripts/export_dialogue_history_to_conversations.py \
        --database-url "postgresql+psycopg2://..." \
        --output conversations.json

The export groups rows by (account_id, dialogue_id). If dialogue_id is empty,
the row is exported as its own one-message conversation to avoid merging
unrelated history.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", ""),
        help="SQLAlchemy database URL for the legacy PostgreSQL database.",
    )
    parser.add_argument(
        "--output",
        default="conversations.json",
        help="Where to write the exported ChatGPT-style JSON.",
    )
    parser.add_argument(
        "--account-id",
        default=None,
        help="Optional account_id filter to export one account only.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON with indentation.",
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("Pass --database-url or set DATABASE_URL.")
    return args


def _to_epoch_seconds(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _build_title(rows: list[dict[str, Any]], conv_key: str) -> str:
    for row in rows:
        text_value = (row["text"] or "").strip()
        if text_value:
            preview = " ".join(text_value.split())
            return preview[:80]
    return conv_key


def _message_node(node_id: str, parent_id: str | None, child_ids: list[str], row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node_id,
        "parent": parent_id,
        "children": child_ids,
        "message": {
            "id": node_id,
            "author": {"role": row["role"]},
            "create_time": _to_epoch_seconds(row["created_at"]),
            "content": {
                "content_type": "text",
                "parts": [row["text"]],
            },
        },
    }


def _conversation_from_rows(conv_key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    mapping: dict[str, Any] = {
        "root": {
            "id": "root",
            "parent": None,
            "children": [],
            "message": None,
        }
    }

    previous_id = "root"
    for index, row in enumerate(rows, start=1):
        node_id = f"msg-{index}-{row['id']}"
        mapping[node_id] = _message_node(node_id, previous_id, [], row)
        mapping[previous_id]["children"].append(node_id)
        previous_id = node_id

    return {
        "id": conv_key,
        "title": _build_title(rows, conv_key),
        "mapping": mapping,
    }


def export_conversations(database_url: str, account_id: str | None = None) -> list[dict[str, Any]]:
    engine = create_engine(database_url)
    query = """
        SELECT
            id,
            account_id,
            dialogue_id,
            role,
            text,
            created_at
        FROM dialogue_history
        WHERE role IN ('user', 'assistant')
          AND text IS NOT NULL
          AND BTRIM(text) <> ''
    """
    params: dict[str, Any] = {}
    if account_id:
        query += " AND account_id = :account_id"
        params["account_id"] = account_id
    query += " ORDER BY account_id, created_at, id"

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).mappings().all()

    for row in rows:
        row_dict = dict(row)
        dialogue_id = (row_dict.get("dialogue_id") or "").strip()
        account = (row_dict.get("account_id") or "unknown").strip() or "unknown"
        if dialogue_id:
            conv_key = f"{account}:{dialogue_id}"
        else:
            conv_key = f"{account}:row:{row_dict['id']}"
        grouped[conv_key].append(row_dict)

    return [
        _conversation_from_rows(conv_key, conversation_rows)
        for conv_key, conversation_rows in grouped.items()
    ]


def main() -> None:
    args = _parse_args()
    conversations = export_conversations(
        database_url=args.database_url,
        account_id=args.account_id,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(
            conversations,
            fh,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )

    print(f"Exported {len(conversations)} conversations to {output_path}")


if __name__ == "__main__":
    main()
