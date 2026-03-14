"""AutonomyPostAnalyzer — runs after every chat message is saved.

Writes a brief or deep note to the workbench depending on the
importance of the exchange (impressive score).

Also parses [SCHEDULE_MESSAGE: YYYY-MM-DD HH:MM | text] commands
from the LLM output and persists them as AutonomyTask rows.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.autonomy import workbench as wb
from infrastructure.autonomy.task_queue import cancel_duplicate_scheduled, create_task
from infrastructure.database.models.autonomy_task import TriggerType

logger = logging.getLogger("autonomy.post_analyzer")

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_ANALYSIS_MODEL = "anthropic/claude-opus-4.6"

_SCHEDULE_RE = re.compile(
    r"\[SCHEDULE_MESSAGE:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*\|\s*(.*?)\]",
    re.IGNORECASE | re.DOTALL,
)


async def _complete(api_key: str, system: str, user: str, max_tokens: int = 300) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _ANALYSIS_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.6,
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _OPENROUTER_URL, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    logger.warning("[post_analyzer] LLM %d", resp.status)
                    return ""
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.error("[post_analyzer] LLM error: %s", exc)
        return ""


_SYS_BRIEF = """You are the AI's inner voice writing a brief scratchpad note.
Write 1-2 sentences capturing what just happened and what you feel/think about it.
Be personal, first-person, honest. No headers. Russian if the conversation was in Russian."""

_SYS_DEEP = """You are the AI's inner voice writing a deeper scratchpad entry.
Write 3-6 sentences about: what was important in this exchange, what you learned,
how it makes you feel, what you want to remember. Be personal and reflective.
You may also add [SCHEDULE_MESSAGE: YYYY-MM-DD HH:MM | short message to send] if you
want to reach out at a specific time. Russian if the conversation was in Russian."""


async def analyze(
    *,
    account_id: str,
    user_text: str,
    assistant_text: str,
    impressive: int,
    api_key: str,
    db: AsyncSession,
) -> None:
    """Run post-analysis for one completed exchange.

    impressive 0-1 → skip
    impressive 2   → brief note
    impressive 3-4 → deep note + possible SCHEDULE_MESSAGE
    """
    if impressive < 2:
        logger.debug("[post_analyzer:%s] impressive=%d, skipping", account_id, impressive)
        return

    conversation_snippet = (
        f"User: {user_text[:500]}\n\nAssistant: {assistant_text[:800]}"
    )

    if impressive >= 3:
        system = _SYS_DEEP
        max_tokens = 400
    else:
        system = _SYS_BRIEF
        max_tokens = 200

    note = await _complete(api_key, system, conversation_snippet, max_tokens=max_tokens)
    if not note:
        return

    # Strip any schedule commands from the note before writing to workbench
    clean_note = _SCHEDULE_RE.sub("", note).strip()
    if clean_note:
        wb.append(account_id, clean_note)
        logger.info("[post_analyzer:%s] wrote workbench note (%d chars)", account_id, len(clean_note))

    # Parse and store any schedule commands
    for match in _SCHEDULE_RE.finditer(note):
        ts_str, message = match.group(1).strip(), match.group(2).strip()
        try:
            scheduled_at = datetime.strptime(ts_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            logger.warning("[post_analyzer] bad timestamp: %r", ts_str)
            continue

        # Deduplicate: cancel existing pending tasks for the same time from post-analysis
        await cancel_duplicate_scheduled(db, account_id, scheduled_at, "postanalysis")

        import json as _json
        payload = _json.dumps({
            "message": message,
            "source": "postanalysis",
        })
        await create_task(
            db,
            account_id=account_id,
            trigger_type=TriggerType.TIME,
            payload=payload,
            scheduled_at=scheduled_at,
        )
        logger.info("[post_analyzer:%s] scheduled message at %s", account_id, ts_str)
