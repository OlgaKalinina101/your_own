"""Post-dialogue continuity engine — runs in background after every chat exchange.

After the assistant response is saved to DB, this module:
  1. Builds a prompt from the recent message history, today's sent/scheduled
     pushes, and the shared state blocks from the context registry.
  2. Asks the configured model to either SKIP (nothing noteworthy) or
     write a brief inner-journal entry.
  3. Parses [SCHEDULE_MESSAGE: ...] commands and creates autonomy tasks,
     logging them on the workbench (identical to reflection_engine).
"""
from __future__ import annotations

import json

from infrastructure.autonomy import commands
from infrastructure.autonomy import workbench as wb
from infrastructure.autonomy.cmd_parser import (
    ParsedCommand,
    parse_commands,
    strip_commands,
)
from infrastructure.autonomy.helpers import detect_lang, get_ai_name, make_llm_client
from infrastructure.autonomy import context
from infrastructure.clock import format_local, now_local
from infrastructure.llm.prompt_loader import get_prompt

from infrastructure.logging.logger import setup_logger

logger = setup_logger("autonomy.post_analyzer")

# A reasoning model bills its thinking against max_tokens, and on the Claude Fable family
# thinking cannot be turned off. The budget has to cover the reasoning plus the
# journal entry plus any trailing command — at 2200 more than a third of these
# replies came back clipped.
ANALYSIS_MAX_TOKENS = 16000

_PROMPTS = "infrastructure/autonomy/prompts/post_analyzer.md"


# The block is shown to him, so it is written in his language. "Could not
# check" is a separate line from "nothing is scheduled" on purpose: he acts
# very differently on the two, and before this they looked identical.
_BLOCK_TEXT: dict[str, dict[str, str]] = {
    "ru": {
        "sent_header": "Сообщения, которые ты уже отправил ей сегодня:",
        "sent_unknown": "Не удалось проверить, что ты уже отправлял ей сегодня — считай, что список неизвестен, а не пуст.",
        "pending_header": "Запланированные сообщения (ещё не отправлены):",
        "pending_unknown": "Не удалось загрузить запланированные сообщения — они могут существовать, просто сейчас их не видно.",
        "trailer": (
            "Не дублируй. Если хочешь — запланируй что-то новое, но не повторяй то, что уже сказал.\n"
            "Если хочешь отменить все запланированные разом — [CANCEL_ALL_SCHEDULED]\n"
            "Ты сможешь переписать эти сообщения или отменить их в момент отправки — тогда ты увидишь весь свой журнал и само сообщение. Не переживай о них сейчас."
        ),
    },
    "en": {
        "sent_header": "Messages you have already sent her today:",
        "sent_unknown": "Could not check what you have already sent her today — treat this list as unknown, not empty.",
        "pending_header": "Scheduled messages (not sent yet):",
        "pending_unknown": "Could not load your scheduled messages — they may well exist, they are just not visible right now.",
        "trailer": (
            "Do not repeat yourself. Schedule something new if you want, but do not say again what you have already said.\n"
            "To cancel everything scheduled at once — [CANCEL_ALL_SCHEDULED]\n"
            "You will be able to rewrite or cancel these when they are about to go out — you will see your whole journal and the message itself then. Do not worry about them now."
        ),
    },
}


def _format_history(
    recent_pairs: list[dict],
    current_user_text: str,
    current_assistant_text: str,
) -> str:
    lines: list[str] = []
    for p in recent_pairs:
        u = (p.get("user_text") or "").strip()
        a = (p.get("assistant_text") or "").strip()
        created_at = p.get("created_at")
        # Not strftime: that prints UTC, which is the bug infrastructure.clock
        # exists for. There were three hand-rolled spellings of this in this
        # file, each with a different wrong fallback behind a bare except.
        ts = f"[{format_local(created_at, '%H:%M')}] " if created_at else ""
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




async def _build_pending_pushes_block(account_id: str, lang: str = "ru") -> str:
    """What he has already said today, and what is still queued.

    Both halves can fail on their own, and both used to fail into an empty
    block. Empty is not the same as "nothing is scheduled": the prompt then
    tells him not to repeat himself while hiding the very list he would check,
    and the duplicate goes to her. A lookup that did not happen now says so.
    """
    words = _BLOCK_TEXT.get(lang, _BLOCK_TEXT["en"])
    lines: list[str] = []

    try:
        from infrastructure.database.engine import get_db_session
        from infrastructure.database.models.message import Message
        from sqlalchemy import select, desc
        from datetime import timezone as _tz

        today_local = now_local().replace(hour=0, minute=0, second=0, microsecond=0)
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
            lines.append(words["sent_header"])
            for m in sent_today:
                ts = format_local(m.created_at, "%H:%M", empty="?")
                lines.append(f"  - [{ts}] «{m.text}»")
    except Exception as exc:
        logger.warning("[post_analyzer] failed to load sent pushes: %s", exc)
        lines.append(words["sent_unknown"])

    try:
        from infrastructure.database.engine import get_db_session
        from infrastructure.autonomy.task_queue import get_pending_tasks

        async with get_db_session() as db:
            tasks = await get_pending_tasks(db, account_id)
        if tasks:
            lines.append(words["pending_header"])
            for t in tasks:
                try:
                    pd = json.loads(t.payload)
                    msg = pd.get("message", str(t.payload))
                except (json.JSONDecodeError, TypeError):
                    msg = str(t.payload)
                ts = format_local(t.scheduled_at, "%Y-%m-%d %H:%M", empty="?")
                lines.append(f"  - [{ts}] «{msg}»")
    except Exception as exc:
        logger.warning("[post_analyzer] failed to load pending tasks: %s", exc)
        lines.append(words["pending_unknown"])

    if not lines:
        return ""

    lines.append(words["trailer"])
    return "\n".join(lines)


_REPORT_HEADER = {
    "ru": "Не выполнилось:",
    "en": "Did not go through:",
}


async def _execute_one(cmd: ParsedCommand, *, account_id: str, lang: str) -> str | None:
    """Do what one command says; return what came of it, or ``None``.

    A thin seam over :func:`infrastructure.autonomy.commands.execute`, which
    reflection uses too. Kept as a named function because it is the point the
    tests reach for when they need one command to fail.
    """
    return await commands.execute(
        cmd,
        account_id=account_id,
        lang=lang,
        log_prefix="post_analyzer",
        source="postanalysis",
    )


async def _execute_commands(
    parsed: list[ParsedCommand], *, account_id: str, lang: str
) -> list[str]:
    """Run every command; return a line for each one that did not go through.

    Two different things end up in that list, and he needs both. A raised error
    means it broke — the database was down, Pushy was not configured. A returned
    sentence means it ran and found nothing to act on: the message he wanted to
    cancel had already been sent. Reflection has always told him the second kind
    because it has a next step to say it in; here it goes into the journal.

    One failure does not stop the rest: the commands in one reply are separate
    intentions, and dropping the others because the first hit a closed database
    would be a second, quieter mistake.
    """
    report: list[str] = []
    for cmd in parsed:
        name = commands.name_of(cmd)
        try:
            outcome = await _execute_one(cmd, account_id=account_id, lang=lang)
        except Exception as exc:
            logger.warning("[post_analyzer] %s failed: %s", name, exc)
            # No square brackets around the name: the workbench strips lines
            # that look like commands, so that a leaked one is never filed as a
            # thought and never read back as an instruction. A failure notice
            # dressed as `[SCHEDULE_MESSAGE]` disappears into that same guard.
            report.append(f"  {name} — {exc}")
        else:
            if outcome:
                logger.info("[post_analyzer] %s: %s", name, outcome)
                report.append(f"  {name} — {outcome}")
    return report


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

    message_history = _format_history(recent_pairs, current_user_text, current_assistant_text)
    lang = detect_lang(message_history)

    state = context.build(
        context.Consumer.POST_ANALYSIS,
        context.Request(account_id=account_id, lang=lang),
    )
    pending_block = await _build_pending_pushes_block(account_id, lang)

    system_prompt = get_prompt(_PROMPTS, lang=lang, section="system", ai_name=ai_name)
    user_prompt = get_prompt(
        _PROMPTS, lang=lang, section="user",
        ai_name=ai_name,
        message_history=message_history,
        pending_pushes_block=pending_block,
        **state,
    )

    logger.info("[post_analyzer:%s] starting, lang=%s history_pairs=%d", account_id, lang, len(recent_pairs))

    client = make_llm_client(api_key)
    # The reply holds a full journal note PLUS any [SCHEDULE_MESSAGE] commands.
    response, finish_reason = await client.complete(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=ANALYSIS_MAX_TOKENS,
        temperature=0.7,
        return_meta=True,
    )
    if not response:
        logger.info("[post_analyzer:%s] empty LLM response", account_id)
        return
    if finish_reason == "length":
        logger.warning(
            "[post_analyzer:%s] reply hit max_tokens — journal/commands may be clipped",
            account_id,
        )

    if response.strip().upper() == "SKIP":
        logger.info("[post_analyzer:%s] SKIP", account_id)
        return

    parsed = parse_commands(response)
    failures = await _execute_commands(parsed, account_id=account_id, lang=lang)

    clean_note = strip_commands(response)
    if failures:
        # His journal is what he reads to remember. A note saying "I will write
        # to her in the morning" beside a task that was never created is a gap
        # he cannot see from the inside — the log line went to a file only I
        # read. So the failure goes where he will read it, next to the plan.
        header = _REPORT_HEADER.get(lang, _REPORT_HEADER["en"])
        clean_note = "\n\n".join(
            part for part in (clean_note, header + "\n" + "\n".join(failures)) if part
        ).strip()
    if clean_note:
        wb.append(account_id, clean_note)
        logger.info(
            "[post_analyzer:%s] wrote workbench note (%d chars): %s",
            account_id, len(clean_note), clean_note[:120],
        )
