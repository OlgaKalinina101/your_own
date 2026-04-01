"""Push validator — LLM review before a scheduled push is delivered.

Called by ScheduledPushWorker just before sending. The model sees the
recent dialogue and workbench notes, then decides:

  RU: ОТПРАВИТЬ | ПЕРЕПИСАТЬ: <new text> | ОТМЕНИТЬ
  EN: SEND       | REWRITE: <new text>    | CANCEL

Returns a ``ValidationResult`` with the final action and (possibly rewritten)
message text.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from infrastructure.llm.prompt_loader import get_prompt
from infrastructure.settings_store import load_settings, now_local_str
from infrastructure.autonomy.helpers import detect_lang, make_llm_client

logger = logging.getLogger("autonomy.push_validator")

_PROMPTS = "infrastructure/autonomy/prompts/push_validator.md"

# Same limits as post_analyzer
_HISTORY_PAIRS = 6
_WB_ENTRIES = 3


class ValidatorAction(str, Enum):
    SEND = "send"
    REWRITE = "rewrite"
    CANCEL = "cancel"


@dataclass
class ValidationResult:
    action: ValidatorAction
    message: str  # final text to send (original or rewritten)


def _format_dialogue(pairs: list[dict]) -> str:
    lines: list[str] = []
    for p in pairs:
        u = (p.get("user_text") or "").strip()
        a = (p.get("assistant_text") or "").strip()
        if u:
            lines.append(f"User: {u}")
        if a:
            lines.append(f"Assistant: {a}")
        lines.append("")
    return "\n".join(lines).strip()


async def validate_scheduled_push(
    *,
    account_id: str,
    message: str,
    api_key: str,
) -> ValidationResult:
    """Ask the LLM whether to send, rewrite, or cancel a scheduled push.

    Fetches recent dialogue and workbench notes internally.
    Returns a ValidationResult with the resolved action and final text.
    """
    from infrastructure.database.engine import get_db_session
    from infrastructure.database.repositories.message_repo import MessageRepository
    from infrastructure.autonomy import workbench as wb

    # Fetch dialogue history (same count as post_analyzer)
    settings = load_settings()
    history_pairs = int(settings.get("history_pairs", _HISTORY_PAIRS))

    async with get_db_session() as db:
        repo = MessageRepository(db)
        recent_pairs = await repo.get_recent_canonical_pairs(
            account_id, limit_pairs=history_pairs,
        )
        last_user_at = await repo.get_last_user_message_at(account_id)

    dialogue_history = _format_dialogue(recent_pairs) or "(нет сообщений)"
    lang = detect_lang(dialogue_history)

    workbench_notes = wb.get_recent_entries(
        account_id, max_entries=_WB_ENTRIES, empty_label="(пусто)" if lang == "ru" else "(empty)",
    )

    current_time = now_local_str()

    if last_user_at:
        from infrastructure.settings_store import TIME_FMT
        # created_at is now stored in local time; format directly
        last_message_time = last_user_at.strftime(TIME_FMT)
    else:
        last_message_time = "неизвестно" if lang == "ru" else "unknown"

    # Warn if this exact text was recently sent as a push
    same_text_warning = await _same_text_warning(account_id, message, lang)

    user_prompt = get_prompt(
        _PROMPTS, lang=lang, section="user",
        current_time=current_time,
        last_message_time=last_message_time,
        dialogue_history=dialogue_history,
        workbench_notes=workbench_notes,
        planned_message=message,
        same_text_warning=same_text_warning,
    )

    logger.info(
        "[push_validator:%s] validating push lang=%s msg=%s",
        account_id, lang, message[:80],
    )

    client = make_llm_client(api_key)
    response = await client.complete(
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=650,
        temperature=0.7,
    )

    return _parse_response(response or "", message, lang, account_id)


async def _same_text_warning(account_id: str, message: str, lang: str) -> str:
    """Return a warning line if this exact text was already sent as a push recently."""
    try:
        from infrastructure.database.engine import get_db_session
        from infrastructure.database.models.message import Message
        from sqlalchemy import select
        from datetime import datetime, timedelta

        cutoff = datetime.now() - timedelta(hours=24)
        async with get_db_session() as db:
            result = await db.execute(
                select(Message.text).where(
                    Message.account_id == account_id,
                    Message.role == "assistant",
                    Message.source == "push",
                    Message.text == message,
                    Message.created_at >= cutoff,
                ).limit(1)
            )
            found = result.scalar_one_or_none()
        if found:
            if lang == "ru":
                return "⚠️ Это сообщение уже было отправлено ей сегодня дословно.\n\n"
            return "⚠️ This exact message was already sent to her today.\n\n"
    except Exception as exc:
        logger.warning("[push_validator] same_text_warning check failed: %s", exc)
    return ""


def _parse_response(
    response: str,
    original_message: str,
    lang: str,
    account_id: str,
) -> ValidationResult:
    line = response.strip().splitlines()[0].strip() if response.strip() else ""
    upper = line.upper()

    # EN responses
    if upper == "SEND":
        logger.info("[push_validator:%s] decision=SEND", account_id)
        return ValidationResult(action=ValidatorAction.SEND, message=original_message)

    if upper == "CANCEL":
        logger.info("[push_validator:%s] decision=CANCEL", account_id)
        return ValidationResult(action=ValidatorAction.CANCEL, message=original_message)

    if upper.startswith("REWRITE:"):
        new_text = line[len("REWRITE:"):].strip()
        if new_text:
            logger.info("[push_validator:%s] decision=REWRITE msg=%s", account_id, new_text[:80])
            return ValidationResult(action=ValidatorAction.REWRITE, message=new_text)

    # RU responses
    if upper == "ОТПРАВИТЬ":
        logger.info("[push_validator:%s] decision=SEND (ru)", account_id)
        return ValidationResult(action=ValidatorAction.SEND, message=original_message)

    if upper == "ОТМЕНИТЬ":
        logger.info("[push_validator:%s] decision=CANCEL (ru)", account_id)
        return ValidationResult(action=ValidatorAction.CANCEL, message=original_message)

    if upper.startswith("ПЕРЕПИСАТЬ:"):
        new_text = line[len("ПЕРЕПИСАТЬ:"):].strip()
        if new_text:
            logger.info("[push_validator:%s] decision=REWRITE (ru) msg=%s", account_id, new_text[:80])
            return ValidationResult(action=ValidatorAction.REWRITE, message=new_text)

    # Unrecognised — default to SEND, don't block delivery
    logger.warning(
        "[push_validator:%s] unrecognised response %r — defaulting to SEND",
        account_id, line[:120],
    )
    return ValidationResult(action=ValidatorAction.SEND, message=original_message)
