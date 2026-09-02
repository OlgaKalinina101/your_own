"""The command vocabulary, and the one place where a command actually happens.

Two things live here, and they are the same subject seen from two sides.

**The names.** ``REFLECTION_COMMANDS`` is what the reflection parser will
recognise; ``LEAKABLE_COMMANDS`` is what the workbench sanitiser must strip
from a note. They used to be two separate lists in two files, and they drifted:
the thread commands were added to the parser and never to the sanitiser, so
thirteen replies cut off mid-token filed ``[UPDATE_THREAD: …`` into his journal
while the update they carried never ran. They are defined together now, and the
sanitiser list is derived from the parser list rather than written out again —
see ``tests/autonomy/test_command_parsing.py``, which holds that shape.

**The doing.** :func:`execute` performs one parsed command and says what came of
it. Before this there were two dispatchers — ``reflection_engine`` took the
command as two strings, ``post_analyzer`` took a typed object — and the actions
were the same code twice. The outcomes were not, and that was the part that
mattered: five of these helpers report back a ``found`` flag, reflection turned
``found=False`` into a sentence he reads on the next step, and post threw the
boolean away. So "I cancelled the 9:00 message" went into his journal whether or
not there had ever been a 9:00 message.
"""
from __future__ import annotations

from infrastructure.autonomy.cmd_parser import (
    CancelAllScheduled,
    CancelMessage,
    ParsedCommand,
    PinThread,
    RescheduleMessage,
    RewriteMessage,
    ScheduleMessage,
    SendMessage,
    UnpinThread,
    UpdateThread,
)
from infrastructure.logging.logger import setup_logger

logger = setup_logger("autonomy.commands")


# ── The vocabulary ───────────────────────────────────────────────────────────

# Every search command maps to one backend of the research agent.
_SEARCH = (
    "SEARCH_DIALOGUE",
    "SEARCH_DOCS",
    "SEARCH_FACTS",
    "SEARCH_NOTES",
    "WEB_SEARCH",
)

# Older spellings he still uses; the engine maps them to the names above.
# They have to be parseable, or the command is simply not seen.
_ALIASES = ("SEARCH_MEMORIES", "RECALL", "HISTORY", "WRITE")

_WRITES = ("WRITE_NOTE", "WRITE_IDENTITY")

_MESSAGES = (
    "SEND_MESSAGE",
    "SCHEDULE_MESSAGE",
    "CANCEL_MESSAGE",
    "RESCHEDULE_MESSAGE",
    "REWRITE_MESSAGE",
)

_THREADS = ("PIN_THREAD", "UNPIN_THREAD", "UPDATE_THREAD")

# Reading his own machinery: the prompts he is run on, by name.
_READS = ("SHOW_PROMPT",)

# Commands written as a bare bracket, with no argument. They are matched by
# their own regexes in the engine rather than through the alternation, but a
# leaked one still has to be stripped from a note.
_BARE = ("SLEEP", "EXTEND", "CANCEL_ALL_SCHEDULED", "VITALS", "LIST_PROMPTS")

# Markers *we* write into his text — never issued by him, never executed. They
# must be stripped from a note and must never be parsed as commands.
CHAT_MARKERS = ("GENERATED_IMAGE", "SAVED_FACT")


def _ordered(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """Collect the names into one deterministic tuple, longest first.

    The order is *not* what keeps ``WRITE`` from capturing ``[WRITE_NOTE: …]``.
    Every regex built from these lists requires a delimiter right after the
    name — ``:`` in the engine, and whitespace, colon, pipe or a closing
    bracket in the sanitiser — so a
    prefix alternative fails at that character and the engine backtracks to the
    longer name on its own. Verified by mutation: sorting these alphabetically
    instead breaks nothing.

    Longest-first is kept anyway, because it costs nothing and makes the
    generated pattern stable and readable in a traceback.
    """
    names = {name for group in groups for name in group}
    return tuple(sorted(names, key=lambda name: (-len(name), name)))


#: What the reflection parser recognises. ``reflection_engine`` builds its
#: command regex from this exact object.
REFLECTION_COMMANDS = _ordered(
    _SEARCH, _ALIASES, _WRITES, _MESSAGES, _THREADS, _READS, ("VITALS",)
)

#: What the workbench must strip from a note. A superset of the above by
#: construction — that is the drift this file exists to prevent.
LEAKABLE_COMMANDS = _ordered(
    REFLECTION_COMMANDS, _BARE, CHAT_MARKERS
)


# ── The doing ────────────────────────────────────────────────────────────────

# What he called each one. Used for saying which command did not happen, so it
# has to read the way he wrote it.
NAMES: dict[type, str] = {
    CancelAllScheduled: "CANCEL_ALL_SCHEDULED",
    SendMessage: "SEND_MESSAGE",
    ScheduleMessage: "SCHEDULE_MESSAGE",
    CancelMessage: "CANCEL_MESSAGE",
    RescheduleMessage: "RESCHEDULE_MESSAGE",
    RewriteMessage: "REWRITE_MESSAGE",
    PinThread: "PIN_THREAD",
    UnpinThread: "UNPIN_THREAD",
    UpdateThread: "UPDATE_THREAD",
}

# Said to him, so his language. The Russian wording is the one reflection
# already used — he has been reading these sentences for months.
_OUTCOMES = {
    "ru": {
        "no_message": "Сообщение на {ts} не найдено (уже отправлено или не существует).",
        "no_thread": "Нить {tid} не найдена на доске.",
    },
    "en": {
        "no_message": "No message found at {ts} (already sent, or never existed).",
        "no_thread": "Thread {tid} is not on the board.",
    },
}


def name_of(cmd: ParsedCommand) -> str:
    return NAMES.get(type(cmd), type(cmd).__name__)


def _say(lang: str, key: str, **fields: str) -> str:
    words = _OUTCOMES.get(lang, _OUTCOMES["en"])
    return words[key].format(**fields)


async def execute(
    cmd: ParsedCommand,
    *,
    account_id: str,
    lang: str,
    log_prefix: str,
    source: str,
) -> str | None:
    """Do what one command says; return what came of it, or ``None``.

    ``log_prefix`` and ``source`` look alike and are not: the first only ever
    reaches a log line, the second is written into the task payload and lives in
    the database ("chat", "postanalysis", "reflection"). Collapsing them into one
    would quietly start writing a fourth value.

    A returned string is not an error — it is the truthful answer to something
    he asked for: the message he wanted to cancel was already sent, the thread
    he wanted to update is not on the board. He is the one who decides what that
    means. Errors are deliberately not caught here, because the two callers do
    genuinely different things with them: reflection hands a failure back to him
    mid-cycle, where he has another step to react in; post-analysis has no next
    step, so it writes the failure into the journal beside the plan.
    """
    from infrastructure.autonomy import threads
    from infrastructure.autonomy.helpers import (
        cancel_all_messages,
        cancel_message,
        reschedule_message,
        rewrite_message,
        schedule_message,
        send_push_and_save,
    )

    if isinstance(cmd, CancelAllScheduled):
        count = await cancel_all_messages(account_id=account_id, log_prefix=log_prefix)
        logger.info("[%s:%s] CANCEL_ALL_SCHEDULED: %s cancelled", log_prefix, account_id, count)
        return None

    if isinstance(cmd, SendMessage):
        await send_push_and_save(
            account_id=account_id, text=cmd.text, log_prefix=log_prefix
        )
        return None

    if isinstance(cmd, ScheduleMessage):
        await schedule_message(
            account_id=account_id, ts_str=cmd.ts_str, text=cmd.text,
            source=source, log_prefix=log_prefix,
        )
        return None

    if isinstance(cmd, CancelMessage):
        found = await cancel_message(
            account_id=account_id, ts_str=cmd.ts_str, log_prefix=log_prefix
        )
        return None if found else _say(lang, "no_message", ts=cmd.ts_str.strip())

    if isinstance(cmd, RescheduleMessage):
        found = await reschedule_message(
            account_id=account_id, old_ts_str=cmd.old_ts_str,
            new_ts_str=cmd.new_ts_str, log_prefix=log_prefix,
        )
        return None if found else _say(lang, "no_message", ts=cmd.old_ts_str.strip())

    if isinstance(cmd, RewriteMessage):
        found = await rewrite_message(
            account_id=account_id, ts_str=cmd.ts_str,
            new_text=cmd.new_text, log_prefix=log_prefix,
        )
        return None if found else _say(lang, "no_message", ts=cmd.ts_str.strip())

    if isinstance(cmd, PinThread):
        threads.pin(account_id, cmd.text.strip())
        return None

    if isinstance(cmd, UnpinThread):
        found = threads.unpin(account_id, cmd.thread_id.strip())
        return None if found else _say(lang, "no_thread", tid=cmd.thread_id.strip())

    if isinstance(cmd, UpdateThread):
        found = threads.update(account_id, cmd.thread_id.strip(), cmd.new_text.strip())
        return None if found else _say(lang, "no_thread", tid=cmd.thread_id.strip())

    raise ValueError(f"unknown command: {type(cmd).__name__}")
