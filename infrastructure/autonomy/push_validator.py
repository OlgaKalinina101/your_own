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

from infrastructure.autonomy import context
from infrastructure.clock import format_local
from infrastructure.llm.prompt_loader import get_prompt
from infrastructure.settings_store import load_settings
from infrastructure.autonomy.helpers import detect_lang, make_llm_client

logger = logging.getLogger("autonomy.push_validator")

_PROMPTS = "infrastructure/autonomy/prompts/push_validator.md"

# Same dialogue depth as post_analyzer. How much of the workbench is shown is
# no longer decided here — see infrastructure/autonomy/context.py.
_HISTORY_PAIRS = 6


class ValidatorAction(str, Enum):
    SEND = "send"
    REWRITE = "rewrite"
    CANCEL = "cancel"


@dataclass
class ValidationResult:
    action: ValidatorAction
    message: str  # final text to send (original or rewritten)


def _format_dialogue(pairs: list[dict]) -> str:
    from infrastructure.clock import user_tz
    user_tz = user_tz()

    lines: list[str] = []
    for p in pairs:
        u = (p.get("user_text") or "").strip()
        a = (p.get("assistant_text") or "").strip()
        ts = ""
        created_at = p.get("created_at")
        if created_at:
            try:
                local_dt = created_at.astimezone(user_tz) if created_at.tzinfo else created_at
                ts = f"[{local_dt.strftime('%H:%M')}] "
            except Exception:
                pass
        if u:
            lines.append(f"{ts}User: {u}")
        if a:
            lines.append(f"{ts}Assistant: {a}")
        lines.append("")
    return "\n".join(lines).strip()


async def validate_scheduled_push(
    *,
    account_id: str,
    message: str,
    api_key: str,
) -> ValidationResult:
    """Ask the LLM whether to send, rewrite, or cancel a scheduled push.

    Fetches the recent dialogue itself; the shared state blocks come from
    the context registry.
    Returns a ValidationResult with the resolved action and final text.
    """
    from infrastructure.database.engine import get_db_session
    from infrastructure.database.repositories.message_repo import MessageRepository

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

    # The board and the clock arrive here now. This call decides whether to
    # interrupt her, and it used to make that call without knowing what was
    # still open between them or what hour it was on her side.
    state = context.build(
        context.Consumer.PUSH_VALIDATION,
        context.Request(account_id=account_id, lang=lang),
    )

    # Through the clock, not strftime: rows come back UTC-aware, and printing
    # one straight showed her last message four hours before it happened —
    # right next to a local "now" in the same prompt.
    last_message_time = format_local(
        last_user_at, empty="неизвестно" if lang == "ru" else "unknown",
    )

    # Warn if this exact text was recently sent as a push
    same_text_warning = await _same_text_warning(account_id, message, lang)

    user_prompt = get_prompt(
        _PROMPTS, lang=lang, section="user",
        last_message_time=last_message_time,
        dialogue_history=dialogue_history,
        planned_message=message,
        same_text_warning=same_text_warning,
        **state,
    )

    logger.info(
        "[push_validator:%s] validating push lang=%s msg=%s",
        account_id, lang, message[:80],
    )

    client = make_llm_client(api_key)
    response, finish_reason = await client.complete(
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=1200,
        temperature=0.7,
        return_meta=True,
    )

    # If the model's reply was cut off (hit max_tokens), a REWRITE would carry
    # a half-finished message — never send that. Fall back to the original text,
    # which is already complete and safe to deliver.
    if finish_reason == "length":
        logger.warning(
            "[push_validator:%s] validator reply truncated — sending ORIGINAL unchanged",
            account_id,
        )
        return ValidationResult(action=ValidatorAction.SEND, message=message)

    return _parse_response(response or "", message, lang, account_id)


async def _same_text_warning(account_id: str, message: str, lang: str) -> str:
    """Return a warning line if this exact text was already sent as a push recently."""
    try:
        from infrastructure.database.engine import get_db_session
        from infrastructure.database.models.message import Message
        from sqlalchemy import select
        from datetime import datetime, timedelta, timezone as _tz

        cutoff = datetime.now(_tz.utc) - timedelta(hours=24)
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
    stripped = response.strip()
    upper = stripped.upper()

    # Bare decisions — the keyword is the whole reply.
    if upper == "SEND" or upper == "ОТПРАВИТЬ":
        logger.info("[push_validator:%s] decision=SEND", account_id)
        return ValidationResult(action=ValidatorAction.SEND, message=original_message)

    if upper == "CANCEL" or upper == "ОТМЕНИТЬ":
        logger.info("[push_validator:%s] decision=CANCEL", account_id)
        return ValidationResult(action=ValidatorAction.CANCEL, message=original_message)

    # REWRITE: the new text is everything after the marker — including any line
    # breaks — so multi-line messages survive intact (was: only the first line).
    for marker in ("REWRITE:", "ПЕРЕПИСАТЬ:"):
        if upper.startswith(marker):
            new_text = stripped[len(marker):].strip()
            if new_text:
                logger.info("[push_validator:%s] decision=REWRITE msg=%s", account_id, new_text[:80])
                return ValidationResult(action=ValidatorAction.REWRITE, message=new_text)

    # Unrecognised — default to SEND, don't block delivery
    logger.warning(
        "[push_validator:%s] unrecognised response %r — defaulting to SEND",
        account_id, stripped[:120],
    )
    return ValidationResult(action=ValidatorAction.SEND, message=original_message)
