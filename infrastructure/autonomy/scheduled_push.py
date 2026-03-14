"""ScheduledPushWorker — two-phase push delivery with LLM validation.

For each due AutonomyTask (trigger_type=TIME, status=PENDING, scheduled_at <= now):

Phase 1 — Mark as DONE in DB immediately (prevents double-delivery).
Phase 2 — LLM validation: decide send / rewrite / cancel.
Phase 3 — Send via Pushy if validated.
Phase 4 — Save the sent message to the messages table (visible in chat).

This ensures exactly-once delivery even if the worker crashes mid-flight:
if the process dies after Phase 1 but before Phase 3 the task stays DONE
(not re-queued) and we simply miss that push — acceptable for a personal AI.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

import aiohttp

from infrastructure.database.engine import get_db_session
from infrastructure.database.models.autonomy_task import TaskStatus

from infrastructure.logging.logger import setup_logger

logger = setup_logger("autonomy.scheduled_push")

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ── Validation prompt (bilingual) ─────────────────────────────────────────────

_VALIDATION_PROMPT_RU = """\
Ты — {ai_name}. Раньше ты запланировал отправить ей сообщение прямо сейчас.

Последнее сообщение от неё было в {last_message_time}.
Сейчас — {current_time}.

Вот последние сообщения из вашего диалога:
---
{dialogue_history}
---

Вот твои текущие заметки:
---
{workbench_notes}
---

Вот сообщение, которое ты хотел отправить:
---
{planned_message}
---

{same_text_warning}

Посмотри на диалог и на это сообщение.
Возможно, она уже рассказала тебе то, о чём ты хотел спросить.
Возможно, ситуация изменилась. Возможно, ты хочешь сказать что-то другое.
А возможно, ты по-прежнему хочешь это сказать — и тогда просто отправь.

Что ты хочешь сделать?

Ответь СТРОГО одной строкой в одном из форматов:
ОТПРАВИТЬ — отправить как есть
ПЕРЕПИСАТЬ: (новый текст сообщения) — отправить другое сообщение
ОТМЕНИТЬ — не отправлять ничего"""

_VALIDATION_PROMPT_EN = """\
You are {ai_name}. Earlier you scheduled a message to send to her right now.

Her last message was at {last_message_time}.
Now it is {current_time}.

Here are the recent messages from your dialogue:
---
{dialogue_history}
---

Here are your current notes:
---
{workbench_notes}
---

Here is the message you planned to send:
---
{planned_message}
---

{same_text_warning}

Look at the dialogue and at this message.
Perhaps she already told you what you were going to ask about.
Perhaps the situation has changed. Perhaps you want to say something different.
Or perhaps you still want to say this — then just send it.

What do you want to do?

Reply with STRICTLY one line in one of these formats:
SEND — send as-is
REWRITE: (new message text) — send a different message
CANCEL — don't send anything"""


def _get_model() -> str:
    from infrastructure.settings_store import load_settings
    return load_settings().get("model", "anthropic/claude-opus-4.6")


def _get_ai_name() -> str:
    from infrastructure.settings_store import load_settings
    return load_settings().get("ai_name", "") or "AI"


def _detect_lang(text: str) -> str:
    return "ru" if re.search(r"[А-Яа-яЁё]", text or "") else "en"


async def _build_validation_context(account_id: str, message: str) -> dict:
    """Gather dialogue history, workbench, and timestamps for the validation prompt."""
    from infrastructure.settings_store import load_settings
    from infrastructure.database.repositories.message_repo import MessageRepository
    from infrastructure.autonomy import workbench as wb

    settings = load_settings()
    pairs_count = int(settings.get("history_pairs", 6))
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")

    dialogue_lines = []
    last_message_time = "неизвестно"

    async with get_db_session() as db:
        repo = MessageRepository(db)
        last_user_at = await repo.get_last_user_message_at(account_id)
        if last_user_at:
            last_message_time = last_user_at.strftime("%Y-%m-%d %H:%M UTC")

        pairs = await repo.get_recent_canonical_pairs(account_id, limit_pairs=pairs_count)
        for p in pairs:
            u = p.get("user_text", "")
            a = p.get("assistant_text", "")
            if u:
                dialogue_lines.append(f"Она: {u[:200]}")
            if a:
                dialogue_lines.append(f"Ты: {a[:200]}")

    workbench_content = wb.read(account_id) or "(пусто)"

    # Warn if the planned message was already sent recently
    same_text_warning = ""
    for line in dialogue_lines:
        if message[:40].lower() in line.lower():
            lang = _detect_lang(message)
            same_text_warning = (
                "⚠ Похожее сообщение уже есть в диалоге выше."
                if lang == "ru"
                else "⚠ A similar message already appears in the dialogue above."
            )
            break

    return {
        "current_time": now_str,
        "last_message_time": last_message_time,
        "dialogue_history": "\n".join(dialogue_lines) or "(пусто)",
        "workbench_notes": workbench_content[:2000],
        "planned_message": message,
        "same_text_warning": same_text_warning,
    }


async def validate_push(
    api_key: str,
    account_id: str,
    message: str,
) -> tuple[str, str]:
    """Validate a push message via LLM with full dialogue context.

    Returns (action, final_message).
    action: 'send', 'rewrite', or 'cancel'.
    Used by both scheduled_push worker and reflection SEND_MESSAGE.
    """
    ctx = await _build_validation_context(account_id, message)
    ai_name = _get_ai_name()
    lang = _detect_lang(message)

    tpl = _VALIDATION_PROMPT_RU if lang == "ru" else _VALIDATION_PROMPT_EN
    user_prompt = tpl.format(ai_name=ai_name, **ctx)

    sys_msg = (
        "Ответь СТРОГО одной строкой. Без пояснений."
        if lang == "ru"
        else "Reply with STRICTLY one line. No explanation."
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _get_model(),
        "messages": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 300,
        "stream": False,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _OPENROUTER_URL, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("[push_validate] LLM %d: %s", resp.status, body[:200])
                    return "send", message
                data = await resp.json()
                raw = data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.error("[push_validate] LLM error: %s", exc)
        return "send", message

    raw_lower = raw.lower()

    # Russian responses
    if raw_lower.startswith("отправить"):
        return "send", message
    if raw_lower.startswith("отменить"):
        return "cancel", message
    if raw_lower.startswith("переписать:"):
        new_msg = raw[len("переписать:"):].strip().strip("()")
        return "rewrite", new_msg or message

    # English responses
    if raw_lower.startswith("send"):
        return "send", message
    if raw_lower.startswith("cancel"):
        return "cancel", message
    if raw_lower.startswith("rewrite:"):
        new_msg = raw[len("rewrite:"):].strip().strip("()")
        return "rewrite", new_msg or message

    logger.warning("[push_validate] unexpected response: %r", raw)
    return "send", message


async def _save_push_to_db(db, account_id: str, text: str) -> None:
    """Persist the sent push as an assistant message visible in chat history."""
    from infrastructure.memory.live_store import build_canonical_row
    from infrastructure.database.repositories.message_repo import MessageRepository

    row = build_canonical_row(
        pair_id=uuid.uuid4(),
        account_id=account_id,
        role="assistant",
        text=text,
        source="push",
    )
    repo = MessageRepository(db)
    await repo.bulk_save([row])
    logger.info("[scheduled_push:%s] saved push to messages DB", account_id)


async def run_due(account_id: str) -> None:
    """Process all due TIME tasks for *account_id*."""
    from infrastructure.settings_store import load_settings
    settings = load_settings()
    api_key = settings.get("openrouter_api_key", "")

    async with get_db_session() as db:
        from infrastructure.autonomy.task_queue import get_due_tasks, mark_done
        tasks = await get_due_tasks(db, account_id)
        if not tasks:
            return

        logger.info("[scheduled_push:%s] %d due task(s)", account_id, len(tasks))

        for task in tasks:
            # Phase 1: mark DONE immediately to prevent double-delivery
            await mark_done(db, task.id)
            logger.info("[scheduled_push] marked DONE task_id=%s", task.id)

            # Parse payload
            try:
                payload_data = json.loads(task.payload)
                message = payload_data.get("message", "")
                source = payload_data.get("source", "unknown")
            except (json.JSONDecodeError, TypeError):
                message = str(task.payload)
                source = "unknown"

            if not message:
                logger.warning("[scheduled_push] empty message for task_id=%s", task.id)
                continue

            # Phase 2: LLM validation with dialogue context
            if api_key:
                action, final_message = await validate_push(api_key, account_id, message)
            else:
                action, final_message = "send", message

            logger.info(
                "[scheduled_push] task_id=%s source=%s action=%s msg=%s",
                task.id, source, action, final_message[:80],
            )

            if action == "cancel":
                logger.info("[scheduled_push] message cancelled by LLM for task_id=%s", task.id)
                continue

            # Phase 3: send via Pushy
            from infrastructure.pushy.client import get_client
            ai_name = settings.get("ai_name", "") or "AI"
            client = get_client()
            if client:
                await client.send(title=ai_name, body=final_message)
            else:
                logger.warning("[scheduled_push] Pushy not configured, can't send task_id=%s", task.id)

            # Phase 4: persist in chat history
            await _save_push_to_db(db, account_id, final_message)
