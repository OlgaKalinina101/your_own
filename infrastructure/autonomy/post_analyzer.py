"""Post-dialogue continuity engine — runs in background after every chat exchange.

After the assistant response is saved to DB, this module:
  1. Builds a prompt from the recent message history, identity excerpt,
     current workbench, and today's sent/scheduled pushes.
  2. Asks the configured model to either SKIP (nothing noteworthy) or
     write a brief inner-journal entry.
  3. Parses [SCHEDULE_MESSAGE: ...] commands and creates autonomy tasks,
     logging them on the workbench (identical to reflection_engine).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from infrastructure.autonomy import identity_memory as identity
from infrastructure.autonomy import workbench as wb
from infrastructure.autonomy.cmd_parser import (
    CancelAllScheduled,
    CancelMessage,
    ParsedCommand,
    RescheduleMessage,
    RewriteMessage,
    ScheduleMessage,
    SendMessage,
    parse_commands,
    strip_commands,
)
from infrastructure.autonomy.helpers import detect_lang, get_ai_name, make_llm_client, save_push_message
from infrastructure.llm.prompt_loader import get_prompt
from infrastructure.settings_store import now_local_str

from infrastructure.logging.logger import setup_logger

logger = setup_logger("autonomy.post_analyzer")

_PROMPTS = "infrastructure/autonomy/prompts/post_analyzer.md"


def _format_history(
    recent_pairs: list[dict],
    current_user_text: str,
    current_assistant_text: str,
) -> str:
    from infrastructure.settings_store import get_user_tz
    user_tz = get_user_tz()

    lines: list[str] = []
    for p in recent_pairs:
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
    if current_user_text.strip():
        lines.append(f"User: {current_user_text}")
    if current_assistant_text.strip():
        lines.append(f"Assistant: {current_assistant_text}")
    return "\n".join(lines)




def _identity_excerpt(account_id: str) -> str:
    content = identity.read(account_id)
    if not content:
        return "(не заполнено)"
    return content


async def _build_pending_pushes_block(account_id: str) -> str:
    """Build a block showing today's sent pushes + pending scheduled messages."""
    from infrastructure.settings_store import get_user_tz
    user_tz = get_user_tz()
    lines: list[str] = []

    try:
        from infrastructure.database.engine import get_db_session
        from infrastructure.database.models.message import Message
        from sqlalchemy import select, desc
        from datetime import timezone as _tz

        today_local = datetime.now(user_tz).replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_local.astimezone(_tz.utc)

        async with get_db_session() as db:
            result = await db.execute(
                select(Message)
                .where(
                    Message.account_id == account_id,
                    Message.role == "assistant",
                    Message.source == "push",
                    Message.message_kind == "canonical",
                    Message.created_at >= today_start_utc,
                )
                .order_by(desc(Message.created_at))
                .limit(10)
            )
            sent_today = result.scalars().all()

        if sent_today:
            lines.append("Сообщения, которые ты уже отправил ей сегодня:")
            for m in sent_today:
                ts = "?"
                if m.created_at:
                    try:
                        ts = m.created_at.astimezone(user_tz).strftime("%H:%M")
                    except Exception:
                        ts = m.created_at.strftime("%H:%M")
                lines.append(f"  - [{ts}] «{m.text}»")
    except Exception as exc:
        logger.warning("[post_analyzer] failed to load sent pushes: %s", exc)

    try:
        from infrastructure.database.engine import get_db_session
        from infrastructure.autonomy.task_queue import get_pending_tasks

        async with get_db_session() as db:
            tasks = await get_pending_tasks(db, account_id)
        if tasks:
            lines.append("Запланированные сообщения (ещё не отправлены):")
            for t in tasks:
                try:
                    pd = json.loads(t.payload)
                    msg = pd.get("message", str(t.payload))
                except (json.JSONDecodeError, TypeError):
                    msg = str(t.payload)
                ts = "?"
                if t.scheduled_at:
                    try:
                        ts = t.scheduled_at.astimezone(user_tz).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        ts = t.scheduled_at.strftime("%Y-%m-%d %H:%M")
                lines.append(f"  - [{ts}] «{msg}»")
    except Exception as exc:
        logger.warning("[post_analyzer] failed to load pending tasks: %s", exc)

    if not lines:
        return ""

    lines.append(
        "Не дублируй. Если хочешь — запланируй что-то новое, но не повторяй то, что уже сказал.\n"
        "Если хочешь отменить все запланированные разом — [CANCEL_ALL_SCHEDULED]\n"
        "Ты сможешь переписать эти сообщения или отменить их в момент отправки — тогда ты увидишь весь свой журнал и само сообщение. Не переживай о них сейчас."
    )
    return "\n".join(lines)


async def _execute_command(cmd: ParsedCommand, *, account_id: str, lang: str) -> None:
    """Execute one parsed autonomy command."""
    from infrastructure.autonomy.helpers import (
        cancel_all_messages,
        send_push_and_save,
        schedule_message,
        cancel_message,
        reschedule_message,
        rewrite_message,
    )

    _log = "post_analyzer"

    if isinstance(cmd, CancelAllScheduled):
        try:
            await cancel_all_messages(account_id=account_id, log_prefix=_log)
        except Exception as exc:
            logger.warning("[post_analyzer] CANCEL_ALL_SCHEDULED failed: %s", exc)

    elif isinstance(cmd, SendMessage):
        try:
            await send_push_and_save(account_id=account_id, text=cmd.text, lang=lang, log_prefix=_log)
        except Exception as exc:
            logger.warning("[post_analyzer] SEND_MESSAGE failed: %s", exc)

    elif isinstance(cmd, ScheduleMessage):
        try:
            await schedule_message(
                account_id=account_id, ts_str=cmd.ts_str, text=cmd.text,
                lang=lang, source="postanalysis", log_prefix=_log,
            )
        except ValueError:
            logger.warning("[post_analyzer] bad SCHEDULE_MESSAGE ts: %r", cmd.ts_str)
        except Exception as exc:
            logger.warning("[post_analyzer] SCHEDULE_MESSAGE failed: %s", exc)

    elif isinstance(cmd, CancelMessage):
        try:
            await cancel_message(account_id=account_id, ts_str=cmd.ts_str, lang=lang, log_prefix=_log)
        except ValueError:
            logger.warning("[post_analyzer] bad CANCEL_MESSAGE ts: %r", cmd.ts_str)
        except Exception as exc:
            logger.warning("[post_analyzer] CANCEL_MESSAGE failed: %s", exc)

    elif isinstance(cmd, RescheduleMessage):
        try:
            await reschedule_message(
                account_id=account_id, old_ts_str=cmd.old_ts_str,
                new_ts_str=cmd.new_ts_str, lang=lang, log_prefix=_log,
            )
        except ValueError:
            logger.warning("[post_analyzer] bad RESCHEDULE_MESSAGE: %r -> %r", cmd.old_ts_str, cmd.new_ts_str)
        except Exception as exc:
            logger.warning("[post_analyzer] RESCHEDULE_MESSAGE failed: %s", exc)

    elif isinstance(cmd, RewriteMessage):
        try:
            await rewrite_message(
                account_id=account_id, ts_str=cmd.ts_str,
                new_text=cmd.new_text, lang=lang, log_prefix=_log,
            )
        except ValueError:
            logger.warning("[post_analyzer] bad REWRITE_MESSAGE ts: %r", cmd.ts_str)
        except Exception as exc:
            logger.warning("[post_analyzer] REWRITE_MESSAGE failed: %s", exc)


async def run_post_analysis(
    *,
    account_id: str,
    recent_pairs: list[dict],
    current_user_text: str,
    current_assistant_text: str,
    api_key: str,
) -> None:
    """Run post-dialogue analysis for one completed exchange.

    Fires in the background after the chat stream ends — zero latency for the user.
    """
    ai_name = get_ai_name()
    now_str = now_local_str()

    message_history = _format_history(recent_pairs, current_user_text, current_assistant_text)
    lang = detect_lang(message_history)

    identity_text = _identity_excerpt(account_id)
    recent_wb = wb.get_recent_entries(account_id, max_entries=3, empty_label="(пусто)")
    pending_block = await _build_pending_pushes_block(account_id)

    system_prompt = get_prompt(_PROMPTS, lang=lang, section="system", ai_name=ai_name)
    user_prompt = get_prompt(
        _PROMPTS, lang=lang, section="user",
        ai_name=ai_name,
        message_history=message_history,
        current_time=now_str,
        identity_excerpt=identity_text,
        recent_workbench=recent_wb,
        pending_pushes_block=pending_block,
    )

    logger.info("[post_analyzer:%s] starting, lang=%s history_pairs=%d", account_id, lang, len(recent_pairs))

    client = make_llm_client(api_key)
    response = await client.complete(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1300,
        temperature=0.7,
    )
    if not response:
        logger.info("[post_analyzer:%s] empty LLM response", account_id)
        return

    if response.strip().upper() == "SKIP":
        logger.info("[post_analyzer:%s] SKIP", account_id)
        return

    commands = parse_commands(response)
    for cmd in commands:
        await _execute_command(cmd, account_id=account_id, lang=lang)

    clean_note = strip_commands(response)
    if clean_note:
        wb.append(account_id, clean_note)
        logger.info(
            "[post_analyzer:%s] wrote workbench note (%d chars): %s",
            account_id, len(clean_note), clean_note[:120],
        )
