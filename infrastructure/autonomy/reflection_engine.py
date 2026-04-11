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
  [SEARCH_MEMORIES: query]       — Chroma key_info (long-term facts)
  [SEARCH_NOTES: query]          — Chroma workbench_archive + current workbench
  [SEARCH_DIALOGUE: YYYY-MM-DD]  — date-based dialogue lookup
  [SEARCH_DIALOGUE: YYYY-MM-DD..YYYY-MM-DD]
  [SEARCH_DIALOGUE: query]       — semantic search in dialogue history
  [WEB_SEARCH: query]
  [WRITE_NOTE: text]
  [WRITE_IDENTITY: section | text]
  [SEND_MESSAGE: text]
  [SCHEDULE_MESSAGE: YYYY-MM-DD HH:MM | text]
  [EXTEND: N]   (1-5, up to 3 times)
  [SLEEP]
"""
from __future__ import annotations

import asyncio
import aiohttp
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.autonomy import identity_memory as identity
from infrastructure.autonomy import workbench as wb
from infrastructure.database.engine import get_db_session
from infrastructure.memory.chroma_pipeline import get_chroma_pipeline
from infrastructure.llm.prompt_loader import get_prompt

from infrastructure.autonomy.helpers import (
    cancel_message,
    detect_lang,
    get_ai_name,
    make_llm_client,
    reschedule_message,
    rewrite_message,
    save_push_message,
    schedule_message,
    send_push_and_save,
)
from infrastructure.logging.logger import setup_logger
logger = setup_logger("autonomy.reflection")

_PROMPTS_DIR = "infrastructure/autonomy/prompts"


BASE_STEPS = 8
EXTEND_ASK_BEFORE = 2
MAX_EXTEND_PER_ASK = 5
MAX_EXTEND_ASKS = 3

_CMD_RE = re.compile(
    r"\[(?P<cmd>SEARCH_MEMORIES|SEARCH_NOTES|SEARCH_DIALOGUE|WEB_SEARCH"
    r"|WRITE_NOTE|WRITE_IDENTITY|SEND_MESSAGE|SCHEDULE_MESSAGE"
    r"|CANCEL_MESSAGE|RESCHEDULE_MESSAGE|REWRITE_MESSAGE"
    r"|EXTEND|SLEEP|RECALL|WRITE|HISTORY):\s*(?P<arg>.*?)\]",
    re.IGNORECASE | re.DOTALL,
)
_SLEEP_RE = re.compile(r"\[SLEEP\]", re.IGNORECASE)
_EXTEND_RE = re.compile(r"\[EXTEND:\s*(\d+)\]", re.IGNORECASE)

_SEARCH_CMDS = {"SEARCH_MEMORIES", "SEARCH_NOTES", "SEARCH_DIALOGUE", "WEB_SEARCH"}
_WRITE_CMDS = {"WRITE_NOTE", "WRITE_IDENTITY", "SEND_MESSAGE", "SCHEDULE_MESSAGE"}

_ALIASES = {
    "RECALL": "SEARCH_MEMORIES",
    "WRITE": "WRITE_NOTE",
    "HISTORY": "SEARCH_DIALOGUE",
}

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "autonomy"
_REFLECTION_TS_FILE = _DATA_DIR / "last_reflection.txt"


# ── Timestamp helpers ─────────────────────────────────────────────────────────

def _get_last_reflection_ts() -> datetime | None:
    if _REFLECTION_TS_FILE.exists():
        try:
            s = _REFLECTION_TS_FILE.read_text().strip()
            return datetime.fromisoformat(s)
        except ValueError:
            pass
    return None


def _set_last_reflection_ts() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _REFLECTION_TS_FILE.write_text(datetime.now(timezone.utc).isoformat())


async def _complete(api_key: str, messages: list[dict], max_tokens: int = 650) -> str:
    client = make_llm_client(api_key)
    return await client.complete(messages, max_tokens=max_tokens, temperature=0.7)


# ── Command handlers ──────────────────────────────────────────────────────────

async def _search_memories(account_id: str, query: str) -> str:
    """Semantic search in Chroma key_info (long-term facts)."""
    try:
        pipeline = get_chroma_pipeline()
        results = pipeline.query_similar_multi(account_id, query, top_k=5)
        if not results:
            return "Ничего не найдено."
        lines = []
        for r in results:
            cat = r.get("metadata", {}).get("category", "?")
            lines.append(f"[{cat}] {r['text']}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("[reflection] search_memories (chroma) error: %s", exc)
        return f"Ошибка поиска: {exc}"


async def _search_notes(account_id: str, query: str) -> str:
    """Semantic search in Chroma workbench_archive + keyword search in current workbench."""
    parts: list[str] = []
    try:
        from infrastructure.memory.chroma_pipeline import _get_archive_collection
        from infrastructure.memory.embedder import embed_one
        col = _get_archive_collection()
        if col is not None:
            embedding = embed_one(query)
            if embedding is not None:
                results = col.query(
                    query_embeddings=[embedding],
                    n_results=5,
                    where={"account_id": account_id},
                    include=["documents", "metadatas", "distances"],
                )
                if results and results["ids"] and results["ids"][0]:
                    for doc, meta, dist in zip(
                        results["documents"][0],
                        results["metadatas"][0],
                        results["distances"][0],
                    ):
                        if dist < 0.65:
                            ts = meta.get("created_at", "?")
                            parts.append(f"[archive {ts}] {doc}")
    except Exception as exc:
        logger.warning("[reflection] search_notes (archive) error: %s", exc)

    current = wb.search(account_id, query)
    if current and not current.startswith("(workbench is empty)") and not current.startswith("No notes"):
        parts.append(f"[workbench] {current}")

    return "\n---\n".join(parts) if parts else "Ничего не найдено."


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


async def _search_dialogue(db: AsyncSession, account_id: str, arg: str) -> str:
    """Date-based or semantic search in PostgreSQL dialogue history."""
    arg = arg.strip()

    # Free-text query → semantic search via embeddings
    if not _DATE_RE.match(arg):
        try:
            from infrastructure.memory.retrieval import retrieve_relevant_pairs
            pairs = await retrieve_relevant_pairs(db, account_id, arg, top_n=5)
            if not pairs:
                return "Ничего не найдено."
            lines = []
            for p in pairs:
                ts = p.created_at.strftime("%Y-%m-%d") if p.created_at else "?"
                lines.append(f"[{ts}] {p.user_text} → {p.assistant_text}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("[reflection] search_dialogue (semantic) error: %s", exc)
            return f"Ошибка поиска: {exc}"

    # Date-based lookup
    try:
        from infrastructure.database.repositories.message_repo import MessageRepository
        repo = MessageRepository(db)

        if ".." in arg:
            parts = arg.split("..")
            before_date = datetime.strptime(parts[1].strip(), "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
        else:
            before_date = datetime.strptime(arg.strip(), "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )

        pairs, _, _ = await repo.get_canonical_pairs_page(
            account_id, limit_pairs=10, before=before_date
        )
        if not pairs:
            return f"Переписка за {arg} не найдена."
        lines = []
        for p in pairs:
            lines.append(
                f"User: {p.get('user_text','')}\nAssistant: {p.get('assistant_text','')}\n"
            )
        return "\n---\n".join(lines)
    except Exception as exc:
        logger.warning("[reflection] search_dialogue error: %s", exc)
        return f"Ошибка поиска диалога: {exc}"


async def _web_search(query: str) -> str:
    try:
        import urllib.parse
        url = (
            "https://api.duckduckgo.com/?q="
            + urllib.parse.quote(query)
            + "&format=json&no_redirect=1&no_html=1"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json(content_type=None)
        abstract = data.get("AbstractText") or ""
        related = [r.get("Text", "") for r in data.get("RelatedTopics", [])[:3]]
        result = abstract or " | ".join(related) or "Нет результатов."
        return result[:500]
    except Exception as exc:
        return f"Ошибка веб-поиска: {exc}"


async def _handle_command(
    cmd: str,
    arg: str,
    account_id: str,
    api_key: str,
    db: AsyncSession,
) -> str | None:
    """Execute one command. Returns result text (search) or None (write/action)."""
    cmd = _ALIASES.get(cmd.upper(), cmd.upper())

    if cmd == "SEARCH_MEMORIES":
        return await _search_memories(account_id, arg)

    elif cmd == "SEARCH_NOTES":
        return await _search_notes(account_id, arg)

    elif cmd == "SEARCH_DIALOGUE":
        return await _search_dialogue(db, account_id, arg)

    elif cmd == "WEB_SEARCH":
        return await _web_search(arg)

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

    elif cmd == "SEND_MESSAGE":
        msg_text = arg.strip()
        _l = detect_lang(msg_text)
        await send_push_and_save(account_id=account_id, text=msg_text, lang=_l, log_prefix="reflection")
        return None

    elif cmd == "SCHEDULE_MESSAGE":
        if "|" in arg:
            ts_str, message = arg.split("|", 1)
            try:
                _l = detect_lang(message)
                await schedule_message(
                    account_id=account_id, ts_str=ts_str, text=message,
                    lang=_l, source="reflection", log_prefix="reflection",
                )
            except ValueError:
                logger.warning("[reflection] bad SCHEDULE_MESSAGE ts: %r", ts_str)
        return None

    elif cmd == "CANCEL_MESSAGE":
        ts_str = arg.strip()
        try:
            found = await cancel_message(account_id=account_id, ts_str=ts_str, lang="ru", log_prefix="reflection")
            if not found:
                return f"Сообщение на {ts_str} не найдено (уже отправлено или не существует)."
        except ValueError:
            logger.warning("[reflection] bad CANCEL_MESSAGE ts: %r", ts_str)
        return None

    elif cmd == "RESCHEDULE_MESSAGE":
        if "->" in arg:
            old_ts_str, new_ts_str = arg.split("->", 1)
            try:
                found = await reschedule_message(
                    account_id=account_id, old_ts_str=old_ts_str,
                    new_ts_str=new_ts_str, lang="ru", log_prefix="reflection",
                )
                if not found:
                    return f"Сообщение на {old_ts_str.strip()} не найдено (уже отправлено или не существует)."
            except ValueError:
                logger.warning("[reflection] bad RESCHEDULE_MESSAGE: %r", arg)
        return None

    elif cmd == "REWRITE_MESSAGE":
        if "|" in arg:
            ts_str, new_text = arg.split("|", 1)
            try:
                found = await rewrite_message(
                    account_id=account_id, ts_str=ts_str,
                    new_text=new_text, lang="ru", log_prefix="reflection",
                )
                if not found:
                    return f"Сообщение на {ts_str.strip()} не найдено (уже отправлено или не существует)."
            except ValueError:
                logger.warning("[reflection] bad REWRITE_MESSAGE: %r", arg)
        return None

    return None


# ── Prompt templates ──────────────────────────────────────────────────────────



def _build_awakening_system(
    *,
    ai_name: str,
    lang: str,
    identity_content: str,
    workbench_content: str,
    recent_dialogue: str,
    current_time: str,
    hours_since_last: str,
    pending_tasks_block: str,
    cooldown_h: int,
    interval_h: int,
) -> str:
    wc = workbench_content or ("(пусто)" if lang == "ru" else "(empty)")
    return get_prompt(
        f"{_PROMPTS_DIR}/reflection_awakening.md",
        lang=lang,
        ai_name=ai_name,
        identity_content=identity_content,
        workbench_content=wc,
        recent_dialogue=recent_dialogue,
        current_time=current_time,
        hours_since_last=hours_since_last,
        pending_tasks_block=pending_tasks_block,
        cooldown_h=cooldown_h,
        interval_h=interval_h,
    )


def _build_continuation(ai_name: str, lang: str, steps_left: int, result: str) -> str:
    return get_prompt(
        f"{_PROMPTS_DIR}/reflection_continuation.md",
        lang=lang,
        ai_name=ai_name,
        steps_left=steps_left,
        result=result,
    )


def _build_after_action(ai_name: str, lang: str, steps_left: int) -> str:
    return get_prompt(
        f"{_PROMPTS_DIR}/reflection_after_action.md",
        lang=lang,
        ai_name=ai_name,
        steps_left=steps_left,
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
    from infrastructure.settings_store import get_user_tz
    from infrastructure.database.models.autonomy_task import TaskStatus
    now_utc = datetime.now(timezone.utc)
    user_tz = get_user_tz()
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
        elif t.status == TaskStatus.CANCELLED:
            status_label = "✗ отменено" if lang == "ru" else "✗ cancelled"
        elif t.scheduled_at and t.scheduled_at <= now_utc:
            status_label = "⏳ отправляется" if lang == "ru" else "⏳ sending"
        else:
            status_label = "⏰ ожидает" if lang == "ru" else "⏰ pending"
        lines.append(f"- [{ts_local}] [{status_label}] {msg}")
    tasks_list = "\n".join(lines)
    if lang == "ru":
        header = "### Твои запланированные сообщения:"
        footer = (
            "Ты можешь отменить или перенести любое ожидающее сообщение прямо сейчас:\n"
            "[CANCEL_MESSAGE: YYYY-MM-DD HH:MM] — отменить сообщение на это время\n"
            "[RESCHEDULE_MESSAGE: YYYY-MM-DD HH:MM -> YYYY-MM-DD HH:MM] — перенести на другое время"
        )
    else:
        header = "### Your scheduled messages:"
        footer = (
            "You can cancel, reschedule or rewrite any pending message right now:\n"
            "[CANCEL_MESSAGE: YYYY-MM-DD HH:MM]\n"
            "[RESCHEDULE_MESSAGE: YYYY-MM-DD HH:MM -> YYYY-MM-DD HH:MM]\n"
            "[REWRITE_MESSAGE: YYYY-MM-DD HH:MM | new text]"
        )
    return f"{header}\n{tasks_list}\n{footer}\n\n"


# ── Main run loop ─────────────────────────────────────────────────────────────

async def run(account_id: str, api_key: str) -> None:
    """Run one full reflection cycle."""
    logger.info("[reflection:%s] starting reflection", account_id)
    _set_last_reflection_ts()

    from infrastructure.settings_store import load_settings
    settings = load_settings()
    cooldown_h = int(settings.get("reflection_cooldown_hours", 4))
    interval_h = int(settings.get("reflection_interval_hours", 12))
    ai_name = get_ai_name()

    async with get_db_session() as db:
        from infrastructure.database.repositories.message_repo import MessageRepository
        repo = MessageRepository(db)

        identity_content = identity.read(account_id)
        workbench_content = wb.read(account_id)

        # Last 3 dialogue pairs
        try:
            recent_pairs = await repo.get_recent_canonical_pairs(account_id, limit_pairs=3)
            recent_dialogue = "\n\n".join(
                f"User: {p.get('user_text','')}\nAssistant: {p.get('assistant_text','')}"
                for p in recent_pairs
            ) if recent_pairs else ""
        except Exception as exc:
            logger.warning("[reflection] recent pairs error: %s", exc)
            recent_dialogue = ""

        lang = detect_lang(recent_dialogue)
        if not recent_dialogue:
            recent_dialogue = "(нет недавнего диалога)" if lang == "ru" else "(no recent dialogue)"

        # Hours since last user message
        last_user_at = await repo.get_last_user_message_at(account_id)
        now_utc = datetime.now(timezone.utc)
        if last_user_at:
            delta_h = (now_utc - last_user_at).total_seconds() / 3600
            hours_since_last = f"{delta_h:.1f} ч" if lang == "ru" else f"{delta_h:.1f} h"
        else:
            hours_since_last = "неизвестно" if lang == "ru" else "unknown"

        # All tasks from last 24h (with status labels so AI sees what's sent/pending)
        from infrastructure.autonomy.task_queue import get_recent_tasks
        recent_tasks = await get_recent_tasks(db, account_id, hours=24)
        pending_tasks_block = _build_pending_tasks_block(lang, recent_tasks)

        from infrastructure.settings_store import now_local_str
        now_str = now_local_str()

        awakening_system = _build_awakening_system(
            ai_name=ai_name,
            lang=lang,
            identity_content=identity_content,
            workbench_content=workbench_content,
            recent_dialogue=recent_dialogue,
            current_time=now_str,
            hours_since_last=hours_since_last,
            pending_tasks_block=pending_tasks_block,
            cooldown_h=cooldown_h,
            interval_h=interval_h,
        )

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

            response = await _complete(api_key, [
                {"role": "system", "content": awakening_system},
                *messages,
            ])

            if not response or not response.strip():
                logger.info("[reflection:%s] empty at step %d, sleeping", account_id, step)
                break

            messages.append({"role": "assistant", "content": response})
            logger.info("[reflection:%s] step %d/%d: %s", account_id, step, max_steps, response[:120])

            is_sleep = bool(_SLEEP_RE.search(response))

            # Handle EXTEND
            extend_match = _EXTEND_RE.search(response)
            if extend_match and extend_asks_used < MAX_EXTEND_ASKS:
                n = min(int(extend_match.group(1)), MAX_EXTEND_PER_ASK)
                max_steps += n
                extend_asks_used += 1
                logger.info("[reflection:%s] [EXTEND: %d] new max=%d", account_id, n, max_steps)

            # Execute all commands
            search_results: list[str] = []
            had_writes = False

            for m in _CMD_RE.finditer(response):
                cmd_name = m.group("cmd")
                arg = m.group("arg")
                if cmd_name.upper() in ("SLEEP", "EXTEND"):
                    continue
                resolved = _ALIASES.get(cmd_name.upper(), cmd_name.upper())
                try:
                    result = await _handle_command(cmd_name, arg, account_id, api_key, db)
                    if result is not None:
                        search_results.append(f"[{resolved}: {arg[:40]}] → {result}")
                    else:
                        had_writes = True
                except Exception as exc:
                    logger.warning("[reflection] command %s error: %s", cmd_name, exc)
                    search_results.append(f"[{resolved}] error: {exc}")

            # Save free-text reasoning to workbench (commands are stripped;
            # wb.append runs _sanitize_note for leaked/truncated commands).
            stripped = _CMD_RE.sub("", response).strip()
            stripped = _SLEEP_RE.sub("", stripped).strip()
            stripped = _EXTEND_RE.sub("", stripped).strip()
            if stripped and len(stripped) > 30:
                wb.append(account_id, stripped)
                had_writes = True

            if is_sleep:
                logger.info("[reflection:%s] [SLEEP] at step %d", account_id, step)
                break

            # Build follow-up prompt based on what happened
            new_steps_left = max_steps - step
            if search_results:
                messages.append({
                    "role": "user",
                    "content": _build_continuation(
                        ai_name, lang, new_steps_left, "\n".join(search_results),
                    ),
                })
            elif had_writes:
                messages.append({
                    "role": "user",
                    "content": _build_after_action(ai_name, lang, new_steps_left),
                })

        logger.info("[reflection:%s] reflection done in %d steps", account_id, step)


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

    if last_message_at is None:
        return False

    last_ref = _get_last_reflection_ts()

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
