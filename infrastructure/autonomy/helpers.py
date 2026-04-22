"""Shared helpers for the autonomy subsystem.

Consolidates small utilities that were previously duplicated across
post_analyzer, reflection_engine, workbench_rotator, scheduled_push,
and memory/key_info.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime

from infrastructure.settings_store import DEFAULT_MODEL, local_to_utc

logger = logging.getLogger("autonomy.helpers")


def _parse_local_ts(ts_str: str) -> datetime:
    """Parse a 'YYYY-MM-DD HH:MM' string as user-local time, return UTC-aware datetime."""
    naive = datetime.strptime(ts_str.strip(), "%Y-%m-%d %H:%M")
    return local_to_utc(naive)


def get_ai_name() -> str:
    from infrastructure.settings_store import load_settings
    return load_settings().get("ai_name", "") or "AI"


def make_llm_client(api_key: str):
    """Build an LLMClient using the model from current settings."""
    from infrastructure.llm.client import LLMClient
    from infrastructure.settings_store import load_settings
    s = load_settings()
    return LLMClient(api_key=api_key, model=s.get("model", DEFAULT_MODEL))


def detect_lang(text: str) -> str:
    """Return 'ru' if *text* contains Cyrillic characters, else 'en'."""
    return "ru" if re.search(r"[А-Яа-яЁё]", text or "") else "en"


async def save_push_message(*, account_id: str, text: str) -> None:
    """Persist a sent push as an assistant message visible in chat history."""
    from infrastructure.database.engine import get_db_session
    from infrastructure.memory.live_store import build_canonical_row
    from infrastructure.database.repositories.message_repo import MessageRepository

    row = build_canonical_row(
        pair_id=uuid.uuid4(),
        account_id=account_id,
        role="assistant",
        text=text,
        source="push",
    )
    async with get_db_session() as db:
        await MessageRepository(db).bulk_save([row])


# ── Autonomy command execution helpers ───────────────────────────────────────
# Shared by post_analyzer and reflection_engine.


async def send_push_and_save(
    *,
    account_id: str,
    text: str,
    lang: str,
    log_prefix: str = "autonomy",
) -> None:
    """Send a push notification and persist the message."""
    from infrastructure.pushy.client import get_client

    client = get_client()
    if client:
        await client.send(title=get_ai_name(), body=text)
        logger.info("[%s:%s] sent push: %s", log_prefix, account_id, text[:80])
    else:
        logger.warning("[%s] SEND_MESSAGE: Pushy not configured", log_prefix)
    await save_push_message(account_id=account_id, text=text)


async def schedule_message(
    *,
    account_id: str,
    ts_str: str,
    text: str,
    lang: str,
    source: str,
    log_prefix: str = "autonomy",
) -> None:
    """Parse timestamp (user-local), cancel duplicates, create a scheduled task in UTC."""
    from infrastructure.database.engine import get_db_session
    from infrastructure.autonomy.task_queue import cancel_duplicate_scheduled, create_task
    from infrastructure.database.models.autonomy_task import TriggerType

    scheduled_at = _parse_local_ts(ts_str)
    async with get_db_session() as db:
        await cancel_duplicate_scheduled(db, account_id, scheduled_at, source)
        payload = json.dumps({"message": text.strip(), "source": source})
        await create_task(
            db,
            account_id=account_id,
            trigger_type=TriggerType.TIME,
            payload=payload,
            scheduled_at=scheduled_at,
        )
    logger.info("[%s:%s] scheduled message at %s", log_prefix, account_id, ts_str.strip())


async def cancel_all_messages(
    *,
    account_id: str,
    log_prefix: str = "autonomy",
) -> int:
    """Cancel all pending scheduled tasks. Returns count cancelled."""
    from infrastructure.database.engine import get_db_session
    from infrastructure.autonomy.task_queue import cancel_all_pending

    async with get_db_session() as db:
        count = await cancel_all_pending(db, account_id)
    logger.info("[%s:%s] CANCEL_ALL_SCHEDULED cancelled=%d", log_prefix, account_id, count)
    return count


async def cancel_message(
    *,
    account_id: str,
    ts_str: str,
    lang: str,
    log_prefix: str = "autonomy",
) -> bool:
    """Cancel a scheduled task by timestamp (user-local). Returns True if found."""
    from infrastructure.database.engine import get_db_session
    from infrastructure.autonomy.task_queue import cancel_task_by_time

    scheduled_at = _parse_local_ts(ts_str)
    async with get_db_session() as db:
        found = await cancel_task_by_time(db, account_id, scheduled_at)
    logger.info("[%s:%s] CANCEL_MESSAGE %s found=%s", log_prefix, account_id, ts_str, found)
    return found


async def reschedule_message(
    *,
    account_id: str,
    old_ts_str: str,
    new_ts_str: str,
    lang: str,
    log_prefix: str = "autonomy",
) -> bool:
    """Reschedule a task from old time to new time (both user-local). Returns True if found."""
    from infrastructure.database.engine import get_db_session
    from infrastructure.autonomy.task_queue import reschedule_task

    old_dt = _parse_local_ts(old_ts_str)
    new_dt = _parse_local_ts(new_ts_str)
    async with get_db_session() as db:
        found = await reschedule_task(db, account_id, old_dt, new_dt)
    logger.info("[%s:%s] RESCHEDULE_MESSAGE %s -> %s found=%s", log_prefix, account_id, old_ts_str.strip(), new_ts_str.strip(), found)
    return found


async def rewrite_message(
    *,
    account_id: str,
    ts_str: str,
    new_text: str,
    lang: str,
    log_prefix: str = "autonomy",
) -> bool:
    """Rewrite a scheduled task's text (timestamp in user-local). Returns True if found."""
    from infrastructure.database.engine import get_db_session
    from infrastructure.autonomy.task_queue import rewrite_task

    scheduled_at = _parse_local_ts(ts_str)
    async with get_db_session() as db:
        found = await rewrite_task(db, account_id, scheduled_at, new_text.strip())
    logger.info("[%s:%s] REWRITE_MESSAGE %s found=%s", log_prefix, account_id, ts_str.strip(), found)
    return found
