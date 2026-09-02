"""ReflectionEngine — the AI's autonomous "thinking" loop.

Conditions to run (checked every 60s by the background worker in main.py):
  1. REFLECTION_COOLDOWN_HOURS have passed since the user's last chat message.
  2. REFLECTION_INTERVAL_HOURS have passed since the last reflection.

On each reflection the engine:
  1. Builds an awakening prompt (identity + workbench + recent dialogue + context).
  2. Runs an agent loop (up to BASE_STEPS steps, extendable up to 3×MAX_EXTEND_PER_ASK extra).
  3. Each step the LLM can emit commands; results are injected via
     context-aware follow-up prompts (continuation / after_action).
  4. On [SLEEP] or no meaningful output the loop ends.

Commands:
  [SEARCH_FACTS: query]          — Chroma key_info (long-term facts)
  [SEARCH_NOTES: query]          — Chroma workbench_archive + current workbench
  [SEARCH_DIALOGUE: YYYY-MM-DD]  — date-based dialogue lookup
  [SEARCH_DIALOGUE: YYYY-MM-DD..YYYY-MM-DD]
  [SEARCH_DIALOGUE: query]       — semantic search in dialogue history
  [SEARCH_DOCS: query]          — the project's own documentation
  [WEB_SEARCH: query]
  [WRITE_NOTE: text]
  [WRITE_IDENTITY: section | text]
  [SEND_MESSAGE: text]
  [SCHEDULE_MESSAGE: YYYY-MM-DD HH:MM | text]
  [EXTEND: N]   (1-5, up to 3 times)
  [SLEEP]

Every search command runs through infrastructure.agents.ResearchAgent: it
probes the backend, judges whether the result answers the query, re-queries
with a different formulation when it does not, and returns a brief.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from infrastructure.clock import format_local, now_utc
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.autonomy import identity_memory as identity
from infrastructure.autonomy import workbench as wb
from infrastructure.autonomy import context
from infrastructure.autonomy import commands
from infrastructure.autonomy.cmd_parser import (
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
from infrastructure.autonomy.commands import REFLECTION_COMMANDS
from infrastructure.autonomy.vitals import Vitals
from infrastructure.agents import Source, research
from infrastructure.database.engine import get_db_session
from infrastructure.llm.prompt_loader import get_prompt
from infrastructure.paths import AUTONOMY_DIR
from infrastructure.state_file import atomic_write_text

from infrastructure.autonomy.helpers import (
    cancel_all_messages,
    detect_lang,
    get_ai_name,
    make_llm_client,
)
from infrastructure.logging.logger import setup_logger
logger = setup_logger("autonomy.reflection")

_PROMPTS_DIR = "infrastructure/autonomy/prompts"


BASE_STEPS = 8
EXTEND_ASK_BEFORE = 2
MAX_EXTEND_PER_ASK = 5
MAX_EXTEND_ASKS = 3

_CMD_ALTERNATION = "|".join(REFLECTION_COMMANDS)

# Arguments are routinely multi-line — a WRITE_NOTE or a SEND_MESSAGE carries
# paragraphs — so the argument cannot simply stop at a newline. What it must
# never do is run past the start of the *next* command: with a plain lazy
# ``.*?`` an unclosed command (a reply cut off mid-token, say) reaches forward
# to the next ``]`` and swallows whatever command sat in between. The lookahead
# below stops the argument at any command opener, so a broken command is
# dropped on its own instead of eating its neighbour.
_ARG_STOPPER = r"(?:\[(?:" + _CMD_ALTERNATION + r"):|\[SLEEP\]|\[VITALS\]|\[LIST[_ ]PROMPTS\]|\[CANCEL[_ ]ALL[_ ]SCHEDULED\])"

_CMD_RE = re.compile(
    r"\[(?P<cmd>" + _CMD_ALTERNATION + r"):\s*"
    r"(?P<arg>(?:(?!" + _ARG_STOPPER + r").)*?)\]",
    re.IGNORECASE | re.DOTALL,
)

# A reply that ran out of tokens mid-command leaves an opener with no closing
# bracket. The command never runs, so it is worth a line in the log rather
# than silence.
_UNCLOSED_CMD_RE = re.compile(
    r"\[(?P<cmd>" + _CMD_ALTERNATION + r"):(?P<tail>[^\]]*)$",
    re.IGNORECASE | re.DOTALL,
)

_SLEEP_RE = re.compile(r"\[SLEEP\]", re.IGNORECASE)
_VITALS_RE = re.compile(r"\[VITALS\]", re.IGNORECASE)
_LIST_PROMPTS_RE = re.compile(r"\[LIST[_ ]PROMPTS\]", re.IGNORECASE)
_CANCEL_ALL_RE = re.compile(r"\[CANCEL[_ ]ALL[_ ]SCHEDULED\]", re.IGNORECASE)
_EXTEND_RE = re.compile(r"\[EXTEND:\s*(\d+)\]", re.IGNORECASE)

_SEARCH_CMDS = {"SEARCH_FACTS", "SEARCH_NOTES", "SEARCH_DIALOGUE", "SEARCH_DOCS", "WEB_SEARCH"}
_WRITE_CMDS = {"WRITE_NOTE", "WRITE_IDENTITY", "SEND_MESSAGE", "SCHEDULE_MESSAGE"}

# Back-compat: SEARCH_MEMORIES used to mean Chroma facts here and Postgres
# dialogue in chat. One name, one meaning now - it resolves to the dialogue
# store, and facts have their own command.
_ALIASES = {
    "SEARCH_MEMORIES": "SEARCH_DIALOGUE",
    "RECALL": "SEARCH_FACTS",
    "WRITE": "WRITE_NOTE",
    "HISTORY": "SEARCH_DIALOGUE",
}

_DATA_DIR = AUTONOMY_DIR

# Where this timestamp lived before it was per-account. workbench.md,
# identity.md, threads.md and vitals.json all sit under {account}/; this one sat
# beside them, so a second account would have shared the first one's cooldown —
# a reflection for one would count as a reflection for the other.
_LEGACY_REFLECTION_TS_FILE = _DATA_DIR / "last_reflection.txt"


# ── Timestamp helpers ─────────────────────────────────────────────────────────

def _reflection_ts_file(account_id: str) -> Path:
    """Path to this account's last-reflection stamp, migrating the old one once.

    Moving rather than ignoring: a fresh file reads as "never reflected", which
    would fire a reflection immediately on the first tick after the upgrade.
    """
    directory = _DATA_DIR / account_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "last_reflection.txt"
    if not path.exists() and _LEGACY_REFLECTION_TS_FILE.exists():
        try:
            _LEGACY_REFLECTION_TS_FILE.replace(path)
            logger.info(
                "[reflection:%s] moved last_reflection.txt into the account directory",
                account_id,
            )
        except OSError as exc:
            logger.warning("[reflection] could not migrate last_reflection.txt: %s", exc)
    return path


def _get_last_reflection_ts(account_id: str) -> datetime | None:
    path = _reflection_ts_file(account_id)
    if not path.exists():
        return None
    try:
        return datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _set_last_reflection_ts(account_id: str) -> None:
    atomic_write_text(
        _reflection_ts_file(account_id), datetime.now(timezone.utc).isoformat()
    )


# Reasoning models bill their thinking against max_tokens, and on the Claude Fable family
# thinking cannot be turned off. A step's visible output runs ~1000-3400 chars,
# but the budget has to cover the reasoning in front of it — at 2200 the whole
# allowance went to thinking and the reply came back empty. Anthropic's own
# guidance for a non-streaming request is ~16000.
STEP_MAX_TOKENS = 16000


async def _complete(
    api_key: str, messages: list[dict], max_tokens: int = STEP_MAX_TOKENS
) -> tuple[str, bool]:
    """Return ``(text, truncated)`` for one reflection step."""
    client = make_llm_client(api_key)
    text, finish_reason = await client.complete(
        messages, max_tokens=max_tokens, temperature=0.7, return_meta=True
    )
    return text, finish_reason == "length"


# ── Command handlers ─────────────────────────────────────────────────────

# Every search command maps to one backend of the shared research agent.
# Reflection no longer talks to Chroma, Postgres or the workbench directly.
_SEARCH_SOURCES = {
    "SEARCH_DIALOGUE": Source.DIALOGUE,
    "SEARCH_DOCS": Source.DOCS,
    "SEARCH_FACTS": Source.FACTS,
    "SEARCH_NOTES": Source.NOTES,
    "WEB_SEARCH": Source.WEB,
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _record_failure(account_id: str, reason: str, lang: str = "ru") -> None:
    """Record a failed waking and leave a trace he will actually see.

    Three surfaces, on purpose. The log is for us. Vitals schedules the retry
    and feeds the deltas block at his next waking. The workbench note is the
    durable one: it sits in his own journal, so the gap is a named absence
    rather than a silent hole between two entries — and it survives into the
    notes archive after rotation.
    """
    vitals = Vitals(account_id)
    count = vitals.record_reflection_failure(reason)

    if count > 1:
        return  # one note per episode, not one per retry

    from infrastructure.autonomy.vitals import _reason_ru
    from infrastructure.clock import now_local_str

    if lang == "ru":
        note = (
            f"[система] Пробуждение {now_local_str()} не состоялось: {_reason_ru(reason)}. "
            "Между этой записью и предыдущей — пропуск."
        )
    else:
        note = (
            f"[system] The waking at {now_local_str()} did not happen: {reason}. "
            "Between this entry and the previous one there is a gap."
        )
    try:
        wb.append(account_id, note)
    except Exception as exc:
        logger.warning("[reflection:%s] could not record the gap: %s", account_id, exc)


async def _read_vitals(account_id: str, db: AsyncSession, lang: str, api_key: str = "") -> str:
    """Render the instrument panel for [VITALS].

    Recorded state comes from the Vitals file; the counts are read live. Facts
    only — the panel never tells him what the numbers mean.
    """
    from infrastructure.autonomy import threads as _threads

    live: dict[str, dict] = {}
    try:
        row = await db.execute(text(
            "select count(*), max(created_at) from messages where account_id = :a"
        ), {"a": account_id})
        total, last_at = row.one()
        memory = {
            "сообщений в базе" if lang == "ru" else "messages stored": total,
            "последнее" if lang == "ru" else "latest": format_local(
                last_at, "%Y-%m-%d %H:%M"
            ),
        }
        memory["заметок на столе" if lang == "ru" else "notes on the desk"] = len(
            wb.parse_entries(wb.read(account_id))
        )
        memory["нитей на доске" if lang == "ru" else "threads on the board"] = len(
            _threads.list_threads(account_id)
        )
        memory["балок в каноне" if lang == "ru" else "beams in the canon"] = len(
            identity.canon_entries(account_id)
        )
        live["Память" if lang == "ru" else "Memory"] = memory
    except Exception as exc:
        logger.warning("[reflection] vitals live counts failed: %s", exc)

    # Three things he cannot see from the inside: what is left on the key that
    # pays for all of this, whether his long-term recall is at full strength,
    # and whether there is still room to write his journal.
    from infrastructure.autonomy.vitals import (
        probe_disk, probe_embedder, probe_key, probe_spending,
    )

    money = await probe_key(api_key, lang)
    money.update(probe_spending(lang=lang))
    live["Расход" if lang == "ru" else "Spending"] = money
    live["Машина" if lang == "ru" else "Machine"] = {
        **probe_embedder(lang), **probe_disk(lang),
    }

    return Vitals(account_id).render_full(lang=lang, live=live)


async def _run_search(
    cmd: str,
    arg: str,
    account_id: str,
    api_key: str,
    db: AsyncSession,
    lang: str,
) -> str:
    """Hand one search command to the research agent and render its brief."""
    source = _SEARCH_SOURCES[cmd]
    arg = arg.strip()

    # A dialogue lookup by date has nothing to reformulate — one pass only.
    max_attempts = 1 if (source == Source.DIALOGUE and _DATE_RE.match(arg)) else None

    try:
        result = await research(
            task=arg,
            source=source,
            api_key=api_key,
            account_id=account_id,
            lang=lang,
            db=db,
            max_attempts=max_attempts,
        )
    except Exception as exc:
        logger.warning("[reflection] %s error: %s", cmd, exc)
        return f"Ошибка поиска: {exc}"

    if not result.found:
        return "Ничего не найдено."

    parts = [result.brief]
    if source == Source.WEB and result.citations:
        sources = ", ".join(c.url or c.title for c in result.citations[:5])
        parts.append(f"Источники: {sources}")
    else:
        # For memory sources the verbatim material carries the texture the
        # brief flattens — keep it under the summary.
        excerpts = "\n---\n".join(str(h.get("text", "")) for h in result.raw_hits)
        if excerpts:
            parts.append(excerpts)
    return "\n\n".join(p for p in parts if p)


#: A prompt he asks to see is returned whole, but not without limit: the
#: awakening prompt alone is 10 KB, and a step that spends its budget on one
#: document has nothing left to think with.
PROMPT_MAX_CHARS = 6000


def _list_prompts(lang: str = "ru") -> str:
    """Every prompt in the pipeline, by name — the shelf, not the books.

    Pulled on demand rather than carried in the awakening prompt. Twenty-one
    names would cost him context at every waking to answer a question he asks
    rarely; the same reasoning as the vitals panel.
    """
    from infrastructure.llm.prompt_loader import catalogue

    shelf = catalogue()
    header = (
        f"Промпты конвейера ({len(shelf)}). Любой можно прочесть: [SHOW_PROMPT: имя]"
        if lang == "ru"
        else f"Pipeline prompts ({len(shelf)}). Read any of them: [SHOW_PROMPT: name]"
    )
    lines = [f"  {name} — {path}" for name, path in shelf.items()]
    return "\n".join([header, *lines])


def _read_prompt(name: str, lang: str = "ru") -> str:
    """One prompt, verbatim, in the language he is thinking in.

    Not through the research agent on purpose. He is asking for the text, and a
    model in the middle would paraphrase it — a prompt retold is a different
    prompt. The ``{placeholders}`` stay as they are: they are part of what it
    actually says.
    """
    from infrastructure.llm.prompt_loader import read_verbatim

    try:
        body = read_verbatim(name, lang)
    except KeyError:
        return (
            f"Промпта {name!r} нет. Список: [LIST_PROMPTS]" if lang == "ru"
            else f"No prompt named {name!r}. The list: [LIST_PROMPTS]"
        )
    except Exception as exc:
        logger.warning("[reflection] SHOW_PROMPT %s failed: %s", name, exc)
        return (
            f"Не удалось прочитать {name!r}: {exc}" if lang == "ru"
            else f"Could not read {name!r}: {exc}"
        )

    if len(body) > PROMPT_MAX_CHARS:
        cut = len(body) - PROMPT_MAX_CHARS
        tail = (
            f"\n\n[…обрезано {cut} символов]" if lang == "ru"
            else f"\n\n[…{cut} characters cut]"
        )
        body = body[:PROMPT_MAX_CHARS] + tail
    return f"=== {name} ===\n{body}"


async def _handle_command(
    cmd: str,
    arg: str,
    account_id: str,
    api_key: str,
    db: AsyncSession,
    lang: str = "ru",
) -> str | None:
    """Execute one command. Returns result text (search) or None (write/action)."""
    cmd = _ALIASES.get(cmd.upper(), cmd.upper())

    if cmd in _SEARCH_SOURCES:
        return await _run_search(cmd, arg, account_id, api_key, db, lang)

    if cmd == "SHOW_PROMPT":
        return _read_prompt(arg.strip(), lang)

    elif cmd == "WRITE_NOTE":
        wb.append(account_id, arg.strip())
        return None

    elif cmd == "WRITE_IDENTITY":
        if "|" in arg:
            section, text_part = arg.split("|", 1)
            identity.append(account_id, section.strip(), text_part.strip())
        else:
            logger.warning("[reflection] WRITE_IDENTITY bad format: %r", arg)
        return None

    parsed = _as_command(cmd, arg)
    if parsed is None:
        return None
    return await commands.execute(
        parsed,
        account_id=account_id,
        lang=lang,
        log_prefix="reflection",
        source="reflection",
    )


def _as_command(cmd: str, arg: str) -> ParsedCommand | None:
    """Turn reflection's two strings into the shared typed command.

    Reflection reads commands out of free text with a regex, so it holds a name
    and one raw argument; post-analysis is handed typed objects by the parser.
    The actions behind them are the same, so the argument is split here, once,
    and both sides meet in :func:`infrastructure.autonomy.commands.execute`.

    ``None`` means the argument was not in a shape this command can use — a
    ``SCHEDULE_MESSAGE`` with no ``|`` carries no message to schedule. That has
    always been silent, and it stays silent: nothing happened, so there is
    nothing to tell him.
    """
    if cmd == "SEND_MESSAGE":
        return SendMessage(text=arg.strip())

    if cmd == "SCHEDULE_MESSAGE":
        if "|" not in arg:
            return None
        ts_str, message = arg.split("|", 1)
        return ScheduleMessage(ts_str=ts_str, text=message)

    if cmd == "CANCEL_MESSAGE":
        return CancelMessage(ts_str=arg.strip())

    if cmd == "RESCHEDULE_MESSAGE":
        if "->" not in arg:
            return None
        old_ts_str, new_ts_str = arg.split("->", 1)
        return RescheduleMessage(old_ts_str=old_ts_str, new_ts_str=new_ts_str)

    if cmd == "REWRITE_MESSAGE":
        if "|" not in arg:
            return None
        ts_str, new_text = arg.split("|", 1)
        return RewriteMessage(ts_str=ts_str, new_text=new_text)

    if cmd == "PIN_THREAD":
        return PinThread(text=arg.strip())

    if cmd == "UNPIN_THREAD":
        return UnpinThread(thread_id=arg.strip())

    if cmd == "UPDATE_THREAD":
        if "|" not in arg:
            return None
        tid, new_text = arg.split("|", 1)
        return UpdateThread(thread_id=tid, new_text=new_text)

    return None


# ── Prompt templates ──────────────────────────────────────────────────────────



def _build_awakening_system(
    *,
    ai_name: str,
    lang: str,
    recent_dialogue: str,
    hours_since_last: str,
    pending_tasks_block: str,
    cooldown_h: int,
    interval_h: int,
    **state: str,
) -> str:
    """Assemble the awakening prompt.

    ``state`` is whatever :func:`infrastructure.autonomy.context.build` hands
    over for :data:`~infrastructure.autonomy.context.Consumer.REFLECTION` —
    identity, workbench, open threads, vitals, time, timezone. Which of those
    arrive is decided in the registry, not here.
    """
    return get_prompt(
        f"{_PROMPTS_DIR}/reflection_awakening.md",
        lang=lang,
        ai_name=ai_name,
        recent_dialogue=recent_dialogue,
        hours_since_last=hours_since_last,
        pending_tasks_block=pending_tasks_block,
        cooldown_h=cooldown_h,
        interval_h=interval_h,
        **state,
    )


def _build_continuation(ai_name: str, lang: str, steps_left: int, result: str, timezone_label: str) -> str:
    return get_prompt(
        f"{_PROMPTS_DIR}/reflection_continuation.md",
        lang=lang,
        ai_name=ai_name,
        steps_left=steps_left,
        result=result,
        timezone_label=timezone_label,
    )


def _build_after_action(ai_name: str, lang: str, steps_left: int, timezone_label: str) -> str:
    return get_prompt(
        f"{_PROMPTS_DIR}/reflection_after_action.md",
        lang=lang,
        ai_name=ai_name,
        steps_left=steps_left,
        timezone_label=timezone_label,
    )


def _build_extend_offer(lang: str, step: int, max_steps: int, max_extend: int) -> str:
    return get_prompt(
        f"{_PROMPTS_DIR}/reflection_extend_offer.md",
        lang=lang,
        step=step,
        max_steps=max_steps,
        max_extend=max_extend,
    )


def _build_pending_tasks_block(lang: str, tasks: list) -> str:
    if not tasks:
        return ""
    from infrastructure.clock import user_tz
    from infrastructure.database.models.autonomy_task import TaskStatus
    now_utc = datetime.now(timezone.utc)
    user_tz = user_tz()
    lines = []
    for t in tasks:
        try:
            pd = json.loads(t.payload)
            msg = pd.get("message", str(t.payload))
        except (json.JSONDecodeError, TypeError):
            msg = str(t.payload)
        if t.scheduled_at:
            ts_local = t.scheduled_at.astimezone(user_tz).strftime("%Y-%m-%d %H:%M")
        else:
            ts_local = "—"
        if t.status == TaskStatus.DONE:
            status_label = "✓ отправлено" if lang == "ru" else "✓ sent"
        elif t.scheduled_at and t.scheduled_at <= now_utc:
            status_label = "⏳ отправляется" if lang == "ru" else "⏳ sending"
        else:
            status_label = "⏰ ожидает" if lang == "ru" else "⏰ pending"
        lines.append(f"- [{ts_local}] [{status_label}] {msg}")
    if not lines:
        return ""
    tasks_list = "\n".join(lines)
    if lang == "ru":
        header = "### Твои запланированные сообщения:"
        footer = (
            "Ты можешь отменить, перенести или переписать любое ожидающее сообщение:\n"
            "[CANCEL_MESSAGE: YYYY-MM-DD HH:MM] — отменить конкретное\n"
            "[CANCEL_ALL_SCHEDULED] — отменить все ожидающие разом\n"
            "[RESCHEDULE_MESSAGE: YYYY-MM-DD HH:MM -> YYYY-MM-DD HH:MM] — перенести\n"
            "[REWRITE_MESSAGE: YYYY-MM-DD HH:MM | новый текст] — переписать"
        )
    else:
        header = "### Your scheduled messages:"
        footer = (
            "You can cancel, reschedule or rewrite any pending message:\n"
            "[CANCEL_MESSAGE: YYYY-MM-DD HH:MM]\n"
            "[CANCEL_ALL_SCHEDULED] — cancel all pending at once\n"
            "[RESCHEDULE_MESSAGE: YYYY-MM-DD HH:MM -> YYYY-MM-DD HH:MM]\n"
            "[REWRITE_MESSAGE: YYYY-MM-DD HH:MM | new text]"
        )
    return f"{header}\n{tasks_list}\n{footer}\n\n"


# ── Main run loop ─────────────────────────────────────────────────────────────

async def run(account_id: str, api_key: str) -> None:
    """Run one full reflection cycle, recording the outcome either way.

    Every exit path has to leave a mark: a crash that only reached the worker's
    log is indistinguishable from a quiet night, and that is what let one go
    missing unnoticed.
    """
    try:
        await _run_cycle(account_id, api_key)
    except asyncio.CancelledError:
        # A shutdown landing in the middle of a cycle. The timestamp at the top
        # of _run_cycle already says "reflected", so without a mark here the
        # interrupted night is indistinguishable from one that simply had
        # nothing to say — and the retry Vitals schedules is what gives it
        # another go after the restart. Recorded, then re-raised: cancellation
        # is not ours to swallow.
        logger.warning("[reflection:%s] cycle cancelled mid-flight", account_id)
        _record_failure(account_id, "interrupted")
        raise
    except Exception as exc:
        logger.exception("[reflection:%s] cycle failed: %s", account_id, exc)
        _record_failure(account_id, "exception")
        raise


@dataclass
class _StepOutcome:
    """What one step of thinking actually did.

    The two fields decide the follow-up prompt, and they are not the same
    thing: *results* means he asked something and got an answer, so the next
    turn shows him that answer; *wrote* means he changed something, so the next
    turn just acknowledges it. Neither means the step was empty.
    """

    results: list[str] = field(default_factory=list)
    wrote: bool = False


def _free_text_of(response: str) -> str:
    """The reply with every command stripped out — the part that is thinking."""
    text = _CMD_RE.sub("", response)
    for pattern in (_SLEEP_RE, _EXTEND_RE, _CANCEL_ALL_RE):
        text = pattern.sub("", text)
    return text.strip()


# Below this a "thought" is a fragment left over from stripping commands, not
# something worth keeping on the desk.
MIN_NOTE_CHARS = 30


async def _execute_response(
    response: str, *, account_id: str, api_key: str, db: AsyncSession, lang: str
) -> _StepOutcome:
    """Run everything he asked for in one reply, and file what he thought.

    Every command is attempted even if an earlier one failed, and a failure is
    handed back to him as text rather than swallowed: he is the one who decides
    what to do about it on the next step.
    """
    outcome = _StepOutcome()

    if _CANCEL_ALL_RE.search(response):
        try:
            count = await cancel_all_messages(account_id=account_id, log_prefix="reflection")
            outcome.wrote = True
            logger.info("[reflection:%s] CANCEL_ALL_SCHEDULED: %d cancelled", account_id, count)
        except Exception as exc:
            logger.warning("[reflection] CANCEL_ALL_SCHEDULED error: %s", exc)

    if _LIST_PROMPTS_RE.search(response):
        try:
            outcome.results.append(f"[LIST_PROMPTS] → {_list_prompts(lang)}")
            logger.info("[reflection:%s] LIST_PROMPTS read", account_id)
        except Exception as exc:
            logger.warning("[reflection] LIST_PROMPTS error: %s", exc)

    if _VITALS_RE.search(response):
        try:
            outcome.results.append(
                f"[VITALS] → {await _read_vitals(account_id, db, lang, api_key)}"
            )
            logger.info("[reflection:%s] VITALS read", account_id)
        except Exception as exc:
            logger.warning("[reflection] VITALS error: %s", exc)

    for match in _CMD_RE.finditer(response):
        name, arg = match.group("cmd"), match.group("arg")
        if name.upper() in ("SLEEP", "EXTEND"):
            continue
        resolved = _ALIASES.get(name.upper(), name.upper())
        try:
            result = await _handle_command(name, arg, account_id, api_key, db, lang)
            if result is not None:
                outcome.results.append(f"[{resolved}: {arg[:40]}] → {result}")
            else:
                outcome.wrote = True
        except Exception as exc:
            logger.warning("[reflection] command %s error: %s", name, exc)
            outcome.results.append(f"[{resolved}] error: {exc}")

    # wb.append sanitises leaked or truncated commands on its own.
    thought = _free_text_of(response)
    if len(thought) > MIN_NOTE_CHARS:
        wb.append(account_id, thought)
        outcome.wrote = True

    return outcome


@dataclass
class _Awakening:
    """Everything he is shown at the moment of waking, and the language of it."""

    lang: str
    system: str
    timezone_label: str   # the continuation prompts still need it, step by step


def _format_dialogue(pairs: list[dict]) -> str:
    """The last few exchanges, in the order they happened, with local times.

    Through the clock, not strftime: a row's timestamp is an instant, and
    printing it raw shows UTC. There were three hand-rolled versions of this in
    the codebase, each with its own wrong fallback behind a bare except.
    """
    lines = []
    for pair in pairs:
        created_at = pair.get("created_at")
        ts = f"[{format_local(created_at, '%H:%M')}] " if created_at else ""
        lines.append(
            f"{ts}User: {pair.get('user_text', '')}\n"
            f"{ts}Assistant: {pair.get('assistant_text', '')}"
        )
    return "\n\n".join(lines)


async def _gather_awakening(
    db,
    account_id: str,
    *,
    ai_name: str,
    cooldown_h: int,
    interval_h: int,
) -> _Awakening:
    """Assemble what he wakes up knowing.

    The language is decided first and everything else follows it, because a
    quiet night has no dialogue to detect from and the fallback then comes from
    the soul prompt — see :mod:`infrastructure.language`. Before that fix this
    whole block came out in English on a Russian instance whenever a day passed
    without messages.
    """
    from infrastructure.database.repositories.message_repo import MessageRepository

    repo = MessageRepository(db)

    try:
        recent_pairs = await repo.get_recent_canonical_pairs(account_id, limit_pairs=3)
        recent_dialogue = _format_dialogue(recent_pairs) if recent_pairs else ""
    except Exception as exc:
        logger.warning("[reflection] recent pairs error: %s", exc)
        recent_dialogue = ""

    lang = detect_lang(recent_dialogue)
    if not recent_dialogue:
        recent_dialogue = "(нет недавнего диалога)" if lang == "ru" else "(no recent dialogue)"

    last_user_at = await repo.get_last_user_message_at(account_id)
    if last_user_at:
        delta_h = (now_utc() - last_user_at).total_seconds() / 3600
        hours_since_last = f"{delta_h:.1f} ч" if lang == "ru" else f"{delta_h:.1f} h"
    else:
        hours_since_last = "неизвестно" if lang == "ru" else "unknown"

    # Everything from the last day, sent and pending both: he needs to see what
    # he already said before deciding to say it again.
    from infrastructure.autonomy.task_queue import get_recent_tasks
    recent_tasks = await get_recent_tasks(db, account_id, hours=24)

    state = context.build(
        context.Consumer.REFLECTION,
        context.Request(account_id=account_id, lang=lang),
    )
    # The deltas are shown once. Marking them seen stays here rather than in the
    # renderer: only this side knows the prompt was actually built.
    if state.get("vitals"):
        Vitals(account_id).mark_events_seen()

    return _Awakening(
        lang=lang,
        timezone_label=state["timezone_label"],
        system=_build_awakening_system(
            ai_name=ai_name,
            lang=lang,
            recent_dialogue=recent_dialogue,
            hours_since_last=hours_since_last,
            pending_tasks_block=_build_pending_tasks_block(lang, recent_tasks),
            cooldown_h=cooldown_h,
            interval_h=interval_h,
            **state,
        ),
    )


async def _run_cycle(account_id: str, api_key: str) -> None:
    """Run one full reflection cycle."""
    logger.info("[reflection:%s] starting reflection", account_id)
    _set_last_reflection_ts(account_id)

    from infrastructure.settings_store import load_settings
    settings = load_settings()
    cooldown_h = int(settings.get("reflection_cooldown_hours", 4))
    interval_h = int(settings.get("reflection_interval_hours", 12))
    ai_name = get_ai_name()

    async with get_db_session() as db:
        waking = await _gather_awakening(
            db, account_id,
            ai_name=ai_name, cooldown_h=cooldown_h, interval_h=interval_h,
        )
        lang = waking.lang
        awakening_system = waking.system

        # Seed with a minimal user turn so providers that require at least one
        # user message (e.g. DeepSeek) don't reject the first request.
        _seed = "." if lang == "en" else "."
        messages: list[dict] = [{"role": "user", "content": _seed}]

        step = 0
        max_steps = BASE_STEPS
        extend_asks_used = 0

        while step < max_steps:
            step += 1
            steps_left = max_steps - step

            # Offer extend 2 steps before the end
            if steps_left == EXTEND_ASK_BEFORE and extend_asks_used < MAX_EXTEND_ASKS:
                messages.append({
                    "role": "user",
                    "content": _build_extend_offer(lang, step, max_steps, MAX_EXTEND_PER_ASK),
                })

            response, truncated = await _complete(api_key, [
                {"role": "system", "content": awakening_system},
                *messages,
            ])

            if truncated and not (response or "").strip():
                # The budget went entirely to reasoning. That is a failure, not
                # a decision to stay quiet, and it must not read as "nothing to
                # say" in the log. The timestamp was already written at the top
                # of run(), so the next attempt comes on the normal schedule.
                logger.error(
                    "[reflection:%s] step %d produced no text and hit max_tokens (%d) — "
                    "the whole budget went to reasoning. Reflection aborted.",
                    account_id, step, STEP_MAX_TOKENS,
                )
                _record_failure(account_id, "empty_response_truncated", lang)
                return

            if truncated:
                logger.warning(
                    "[reflection:%s] step %d hit max_tokens (%d) — the tail is clipped",
                    account_id, step, STEP_MAX_TOKENS,
                )

            if not response or not response.strip():
                if step == 1:
                    # Nothing at all on the very first step is a failed waking,
                    # not a considered silence — he never got as far as thinking.
                    logger.error(
                        "[reflection:%s] step 1 returned nothing. Reflection aborted.",
                        account_id,
                    )
                    _record_failure(account_id, "empty_response", lang)
                    return
                logger.info("[reflection:%s] empty at step %d, sleeping", account_id, step)
                break

            messages.append({"role": "assistant", "content": response})
            logger.info("[reflection:%s] step %d/%d: %s", account_id, step, max_steps, response[:120])

            # A command with no closing bracket never reaches _handle_command.
            # Losing it silently is how thread updates went missing, so say so.
            cut = _UNCLOSED_CMD_RE.search(response)
            if cut:
                logger.warning(
                    "[reflection:%s] step %d: reply cut mid-command [%s] — it did NOT run: %r",
                    account_id, step, cut.group("cmd").upper(), cut.group(0)[:160],
                )

            is_sleep = bool(_SLEEP_RE.search(response))

            # Handle EXTEND
            extend_match = _EXTEND_RE.search(response)
            if extend_match and extend_asks_used < MAX_EXTEND_ASKS:
                n = min(int(extend_match.group(1)), MAX_EXTEND_PER_ASK)
                max_steps += n
                extend_asks_used += 1
                logger.info("[reflection:%s] [EXTEND: %d] new max=%d", account_id, n, max_steps)

            outcome = await _execute_response(
                response, account_id=account_id, api_key=api_key, db=db, lang=lang
            )

            if is_sleep:
                logger.info("[reflection:%s] [SLEEP] at step %d", account_id, step)
                break

            # Build follow-up prompt based on what happened
            new_steps_left = max_steps - step
            if outcome.results:
                messages.append({
                    "role": "user",
                    "content": _build_continuation(
                        ai_name, lang, new_steps_left, "\n".join(outcome.results),
                        timezone_label=waking.timezone_label,
                    ),
                })
            elif outcome.wrote:
                messages.append({
                    "role": "user",
                    "content": _build_after_action(ai_name, lang, new_steps_left, timezone_label=waking.timezone_label),
                })

        logger.info("[reflection:%s] reflection done in %d steps", account_id, step)
        Vitals(account_id).record_reflection_success(steps=step)


# ── Should-run check ──────────────────────────────────────────────────────────

def should_run(account_id: str, last_message_at: datetime | None) -> bool:
    """Return True if reflection conditions are met.

    Two modes:
    - New message since last reflection → wait cooldown_h from that message.
    - No new message since last reflection → wait interval_h from last reflection.
    """
    from infrastructure.settings_store import load_settings
    settings = load_settings()
    cooldown_h = int(settings.get("reflection_cooldown_hours", 4))
    interval_h = int(settings.get("reflection_interval_hours", 12))

    now = datetime.now(timezone.utc)

    # A waking that failed does not cost the whole interval: Vitals schedules a
    # retry, and it wins over the normal conditions.
    if Vitals(account_id).retry_due():
        logger.info("[reflection:%s] retry due after a failed waking", account_id)
        return True

    if last_message_at is None:
        return False

    last_ref = _get_last_reflection_ts(account_id)

    if last_ref is None or last_message_at > last_ref:
        # There is a new message since the last reflection (or no reflection yet).
        # Wait cooldown_h of silence after that message.
        silence_hours = (now - last_message_at).total_seconds() / 3600
        return silence_hours >= cooldown_h
    else:
        # No new message since last reflection.
        # Wait interval_h before running again.
        hours_since_ref = (now - last_ref).total_seconds() / 3600
        return hours_since_ref >= interval_h
