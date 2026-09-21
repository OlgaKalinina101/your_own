"""Shared helpers for the autonomy subsystem.

Consolidates small utilities that were previously duplicated across
post_analyzer, reflection_engine, workbench_rotator, scheduled_push,
and memory/key_info.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from infrastructure.clock import local_to_utc
from infrastructure.events import publish_pairs_changed
from infrastructure import language
from infrastructure.settings_store import DEFAULT_MODEL

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
    """Language to write in, with *text* as the evidence.

    Every caller here is choosing a language to *write*, and every one of them
    can be handed nothing on a fresh instance: a dialogue that has not started,
    an identity file that does not exist yet, a night with no messages in it.
    Silence is not evidence of English, so in that case the answer comes from
    the soul prompt. See :mod:`infrastructure.language`.
    """
    return language.detect_or_soul(text)


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

    # The one case no client can find out about on its own: he wrote this, not
    # a person. Pushy reaches the phone; a desktop sitting open learned nothing.
    publish_pairs_changed(account_id=account_id, origin="assistant")


# ── Autonomy command execution helpers ───────────────────────────────────────
# Shared by post_analyzer and reflection_engine.


async def send_push_and_save(
    *,
    account_id: str,
    text: str,
    log_prefix: str = "autonomy",
) -> None:
    """Send a push notification and persist the message.

    There used to be a ``lang`` parameter here, and on the four helpers below.
    Every caller computed it and threaded it through; not one of the five ever
    read it. A parameter like that is worse than useless — it says the language
    of a message matters to sending it, and callers write code to honour that.
    """
    from infrastructure.pushy.client import get_client

    client = get_client()
    if client:
        await client.send(title=get_ai_name(), body=text)
        logger.info("[%s:%s] sent push: %s", log_prefix, account_id, text[:80])
    else:
        logger.warning("[%s] SEND_MESSAGE: Pushy not configured", log_prefix)
    await save_push_message(account_id=account_id, text=text)


async def send_to_chat(
    *,
    account_id: str,
    text: str,
    reply_to_message_id: int | None = None,
    log_prefix: str = "autonomy",
) -> bool:
    """Post a line into the group chat, and keep his copy of it.

    ``reply_to_message_id`` puts it under a particular message — the ids are
    the ``#numbers`` in every transcript he is shown.

    Returns ``False`` when there is no chat to post into — no token, no group
    chosen — so the caller can tell him in words rather than let the line
    vanish. Delivery errors raise: reflection has a next step to hear them in.
    """
    from infrastructure.database.engine import get_db_session
    from infrastructure.database.repositories.channel_repo import ChannelRepository
    from infrastructure.settings_store import load_settings
    from infrastructure.telegram import listener, responder
    from infrastructure.telegram.client import get_client

    clean = (text or "").strip()
    client = get_client()
    chat_id = str(load_settings().get("telegram_chat_id") or "").strip()
    if client is None or not chat_id or not clean:
        logger.warning("[%s:%s] SEND_TO_CHAT: chat not configured", log_prefix, account_id)
        return False

    sent = await client.send_message(chat_id, clean, reply_to_message_id=reply_to_message_id)
    logger.info("[%s:%s] said in the group: %s", log_prefix, account_id, clean[:80])

    bot = listener.read_state(account_id).get("bot") or {}
    row = responder.own_row(
        sent, account_id=account_id, chat_id=chat_id,
        bot_id=bot.get("id", ""), ai_name=get_ai_name(),
    )
    try:
        import asyncio

        await asyncio.get_running_loop().run_in_executor(None, listener.fill_embeddings, [row])
        async with get_db_session() as db:
            await ChannelRepository(db).save_many([row])
    except Exception as exc:
        logger.error("[%s:%s] sent to the group but could not store own row: %s", log_prefix, account_id, exc)
    return True


async def schedule_message(
    *,
    account_id: str,
    ts_str: str,
    text: str,
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
