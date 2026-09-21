"""Workbench rotator — archives stale notes and extracts insights via LLM.

Called at the start of each reflection cycle, before the main reflection loop.

Steps:
  1. **Rotate** — move stale workbench entries (>48 h) to the
     ``workbench_archive`` Chroma collection.
  2. **Self-insight** — LLM reads the rotated notes and extracts key facts
     about the user → stored in the main ``key_info`` Chroma collection.
  3. **Identity review** — LLM checks whether any identity pillar should be
     updated. May append a new bullet or create a task + push for a full
     rewrite.
  4. **Identity consolidation** — for sections with ≥ CONSOLIDATION_THRESHOLD
     entries the LLM merges them into 5-7 bullet points.

System prompt review is intentionally omitted.
"""
from __future__ import annotations

import asyncio
import logging
import re

from infrastructure.autonomy import identity_memory as identity
from infrastructure.autonomy import workbench as wb
from infrastructure.autonomy.helpers import detect_lang, get_ai_name, make_llm_client
from infrastructure.memory.chroma_pipeline import get_chroma_pipeline
from infrastructure.llm.prompt_loader import get_prompt

logger = logging.getLogger("autonomy.rotator")

_PROMPTS_DIR = "infrastructure/autonomy/prompts"

# A reasoning model bills its thinking against max_tokens, and on the Claude Fable family
# thinking cannot be turned off. Every rotator step writes at most a few
# thousand characters, but the budget has to cover the reasoning in front of
# that — at 1500 nearly half of these replies came back clipped.
_STEP_MAX_TOKENS = 16000


async def _complete(
    api_key: str,
    system: str,
    user: str,
    temperature: float = 0.4,
    max_tokens: int = 650,
) -> str:
    client = make_llm_client(api_key)
    return await client.complete(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )


# ── Step 1: rotate stale entries to Chroma archive ──────────────────────────

async def _rotate_to_archive(account_id: str) -> list[tuple[str, str]]:
    """Move stale workbench entries into the workbench_archive Chroma collection.

    Returns list of (timestamp, text) tuples that were rotated.

    The Chroma write and the embedding behind it are synchronous CPU and disk
    work. Run inline they froze the event loop for the whole batch — long
    enough, with a full desk, for the heartbeat to miss its minute and record
    the pause as downtime.
    """
    stale = wb.get_stale_entries(account_id)
    if not stale:
        return []

    def _archive_all() -> None:
        pipeline = get_chroma_pipeline()
        for ts_str, text in stale:
            pipeline.add_archive_entry(
                account_id=account_id,
                text=text,
                timestamp=ts_str,
            )

    await asyncio.get_running_loop().run_in_executor(None, _archive_all)

    # Only after every note is in the archive. The reverse order would lose
    # notes outright; this way a crash in between costs a repeat, and the
    # content-derived id makes that repeat harmless.
    wb.remove_stale(account_id)
    logger.info("[rotator:%s] archived %d stale notes", account_id, len(stale))
    return stale


# ── Step 2: self-insight extraction ──────────────────────────────────────────



async def _extract_self_insights(
    account_id: str,
    notes_block: str,
    api_key: str,
    lang: str,
) -> int:
    """LLM extracts self-insights from rotated notes → stores in key_info Chroma."""
    from infrastructure.settings_store import load_soul

    ai_name = get_ai_name()
    soul = load_soul() or ""
    user_prompt = get_prompt(
        f"{_PROMPTS_DIR}/rotator_insight.md",
        lang=lang,
        ai_name=ai_name,
        system_prompt=soul,
        notes=notes_block,
    )
    sys_msg = "Верни только строки. Без пояснений." if lang == "ru" else "Return only lines. No explanations."
    raw = await _complete(api_key, sys_msg, user_prompt, temperature=0.7, max_tokens=_STEP_MAX_TOKENS)
    if not raw or raw.strip().lower() in ("нет ключевой информации", "no key information"):
        return 0

    from infrastructure.memory.key_info import store_fact_with_dedup

    chroma_category = "Вдохновение" if lang == "ru" else "Inspiration"

    _skip_ru = ("нет ключевой информации",)
    _skip_en = ("no key information",)

    count = 0
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip explicit "nothing to save" responses that slipped through per-line
        if line.lower() in _skip_ru or line.lower() in _skip_en:
            continue
        # Lines must be substantial (more than a label or a very short fragment)
        if len(line) < 10:
            continue
        result = await store_fact_with_dedup(
            api_key=api_key,
            account_id=account_id,
            fact=line,
            category=chroma_category,
            impressive=3,
        )
        dedup_status = result.get("dedup", "saved") if result else "skipped"
        logger.info("[rotator:%s] self-insight [%s]: %s [%s]", account_id, chroma_category, line[:60], dedup_status)
        if result and result.get("dedup") != "skipped":
            count += 1

    return count


# ── Step 3: identity review ─────────────────────────────────────────────────



async def _review_identity(
    account_id: str,
    notes_block: str,
    api_key: str,
    lang: str,
) -> bool:
    """LLM reviews identity pillars based on rotated notes. Returns True if updated."""
    identity_content = identity.read(account_id)
    ai_name = get_ai_name()
    sys_prompt = get_prompt(
        f"{_PROMPTS_DIR}/rotator_identity.md",
        lang=lang, section="system",
        ai_name=ai_name,
    )
    user_prompt = get_prompt(
        f"{_PROMPTS_DIR}/rotator_identity.md",
        lang=lang, section="user",
        ai_name=ai_name,
        identity=identity_content,
        notes=notes_block,
        people=_people_for_review(account_id, lang),
    )
    raw = await _complete(api_key, sys_prompt, user_prompt, temperature=0.7, max_tokens=_STEP_MAX_TOKENS)
    if not raw or raw.strip().lower() in ("нет", "no"):
        return False

    resp = raw.strip()

    # Format: ОБНОВИТЬ: раздел  (RU)  /  UPDATE: section  (EN)
    # followed by  ---\n- point\n---
    update_re = re.compile(
        r"(?:ОБНОВИТЬ|UPDATE):\s*(.+?)\s*\n-{3,}\s*\n(.*?)\n-{3,}",
        re.DOTALL | re.IGNORECASE,
    )
    update_m = update_re.search(resp)
    if update_m:
        # The model reads headers out of the file, so it may echo a decorated
        # name ("Наши принципы: Мы — Valeo") back at us.
        written = update_m.group(1).strip()
        section = identity.resolve_section(account_id, written)
        new_body = update_m.group(2).strip()
        lines = [ln.strip() for ln in new_body.splitlines() if ln.strip().startswith("- ")]
        if lines and section:
            identity.replace_section(account_id, section, "\n".join(lines))
            logger.info("[rotator:%s] identity: updated «%s» (%d points)", account_id, section, len(lines))
            return True
        logger.warning(
            "[rotator:%s] UPDATE for unknown section or no bullets: %r (resolved=%r)",
            account_id, written, section,
        )

    return False


# ── The address book ─────────────────────────────────────────────────────────
#
# Two jobs, and neither is the main way the book is written. He writes cards
# himself, in the room, the moment he learns something — a fact that waited 48
# hours for the rotator would leave him not knowing tomorrow where a friend is
# from. What is left for here is the net and the housekeeping.

_PEOPLE_REVIEW_CHARS = 6000
_ABOUT_LINE_RE = re.compile(r"^\s*ABOUT\s*:\s*(?P<who>[^|]+?)\s*\|\s*(?P<fact>.+?)\s*$", re.IGNORECASE)


def _people_for_review(account_id: str, lang: str) -> str:
    from infrastructure.autonomy import people

    book = people.all_people(account_id)
    if not book:
        return "(пусто)" if lang == "ru" else "(empty)"
    return people.render_cards(book, limit=len(book))[:_PEOPLE_REVIEW_CHARS]


async def _sort_group_notes(
    account_id: str, stale: list[tuple[str, str]], api_key: str, lang: str,
) -> int:
    """The net: facts about people that were filed as notes go onto cards.

    He has two ways to write in the room and will sometimes use the wrong one —
    and everything noted before the book existed was, by necessity, a note. The
    mark on notes from the group is what makes the candidates findable. The
    notes themselves still go to the archive; nothing is taken from him.
    """
    from infrastructure.autonomy import people

    candidates = [(ts, body) for ts, body in stale if wb.is_group_note(body)]
    if not candidates:
        return 0

    path = f"{_PROMPTS_DIR}/rotator_people.md"
    index = people.render_index(account_id) or ("(пусто)" if lang == "ru" else "(empty)")
    raw = await _complete(
        api_key,
        get_prompt(path, lang=lang, section="sort_system", ai_name=get_ai_name()),
        get_prompt(
            path, lang=lang, section="sort_user", index=index,
            notes="\n---\n".join(f"[{ts}]\n{body}" for ts, body in candidates),
        ),
        temperature=0.3, max_tokens=_STEP_MAX_TOKENS,
    )
    moved = _apply_about_lines(account_id, raw, lang)
    logger.info("[rotator:%s] address book: %d fact(s) moved from %d note(s)", account_id, moved, len(candidates))
    return moved


#: How much transcript one call is given. Small enough that the model still
#: reads the first line as carefully as the last.
BOOK_FROM_CHAT_CHUNK_CHARS = 30_000


def _apply_about_lines(account_id: str, raw: str, lang: str, tg_by_name: dict[str, str] | None = None) -> int:
    """Carry out every ``ABOUT: name | fact`` line in a model's answer."""
    from infrastructure.autonomy import people

    written = 0
    for line in (raw or "").splitlines():
        match = _ABOUT_LINE_RE.match(line.strip().lstrip("-• ").strip("[]"))
        if not match:
            continue
        name, aka = people.split_who(match.group("who"))
        tg_id = next(
            ((tg_by_name or {})[key] for key in (" ".join(n.lower().split()) for n in (name, *aka))
             if key in (tg_by_name or {})),
            "",
        )
        if people.add_fact(account_id, match.group("who"), match.group("fact"), tg_id=tg_id, lang=lang) is None:
            written += 1
    return written


async def fill_book_from_chat(account_id: str, api_key: str, rows: list, lang: str = "ru") -> int:
    """Read a stretch of the group chat and put what it says about people on cards.

    Not part of a normal rotation: he writes cards himself as he goes. This is
    for the chat that was already there before the book was — the first two
    days of the live group, where the introductions happened before he had
    anywhere to put them — and for any room he joins with a history.

    Goes chunk by chunk, handing each call the index as it stands, so a person
    met in the first chunk is filed under the same name in the third. Speakers
    are bound to their Telegram ids by the name they are signed with.
    """
    from infrastructure.autonomy import people
    from infrastructure.telegram import responder

    theirs = [row for row in rows if not row.is_self and not row.is_owner]
    tg_by_name = {" ".join((row.sender_name or "").lower().split()): row.sender_id for row in theirs if row.sender_name}
    ai_name = get_ai_name()
    path = f"{_PROMPTS_DIR}/rotator_people.md"

    chunks: list[list] = [[]]
    size = 0
    for row in rows:
        cost = len(row.text or "") + 60
        if size + cost > BOOK_FROM_CHAT_CHUNK_CHARS and chunks[-1]:
            chunks.append([])
            size = 0
        chunks[-1].append(row)
        size += cost

    total = 0
    for number, chunk in enumerate(chunks, start=1):
        if not chunk:
            continue
        raw = await _complete(
            api_key,
            get_prompt(path, lang=lang, section="chat_system", ai_name=ai_name),
            get_prompt(
                path, lang=lang, section="chat_user",
                index=people.render_index(account_id) or ("(пусто)" if lang == "ru" else "(empty)"),
                transcript=responder.render_room(chunk, ai_name=ai_name, lang=lang, with_dates=True),
            ),
            temperature=0.3, max_tokens=_STEP_MAX_TOKENS,
        )
        written = _apply_about_lines(account_id, raw, lang, tg_by_name)
        total += written
        logger.info(
            "[rotator:%s] address book from chat: chunk %d/%d (%d messages) → %d fact(s)",
            account_id, number, len(chunks), len(chunk), written,
        )
    return total


async def _consolidate_people(account_id: str, api_key: str, lang: str) -> int:
    """Rebuild the cards that have grown long — the identity pattern, per person."""
    from infrastructure.autonomy import people

    path = f"{_PROMPTS_DIR}/rotator_people.md"
    rebuilt = 0
    for person in people.needs_consolidation(account_id):
        raw = await _complete(
            api_key,
            get_prompt(path, lang=lang, section="consolidate_system", ai_name=get_ai_name()),
            get_prompt(
                path, lang=lang, section="consolidate_user", name=person.name,
                count=len(person.lines), card=people.render_card(person, max_chars=20000),
            ),
            temperature=0.4, max_tokens=_STEP_MAX_TOKENS,
        )
        lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip().startswith("- ")]
        # A rebuild that comes back longer, or empty, is not a rebuild.
        if lines and len(lines) < len(person.lines) and people.replace_lines(account_id, person.slug, lines):
            rebuilt += 1
            logger.info(
                "[rotator:%s] card «%s»: %d → %d lines", account_id, person.name, len(person.lines), len(lines),
            )
    return rebuilt


# ── Step 4: identity consolidation ──────────────────────────────────────────



async def _consolidate_identity(
    account_id: str,
    api_key: str,
    lang: str,
    notes_block: str = "",
) -> bool:
    """Consolidate identity sections that exceeded the threshold."""
    sections_to_consolidate = identity.needs_consolidation(account_id)
    if not sections_to_consolidate:
        return False

    updated = False
    full_identity = identity.read(account_id)
    ai_name = get_ai_name()
    notes = notes_block or ("(нет свежих заметок)" if lang == "ru" else "(no recent notes)")

    for section in sections_to_consolidate:
        count = identity.get_section_entry_count(account_id, section)
        logger.info("[rotator:%s] consolidating «%s»: %d entries", account_id, section, count)

        section_content = identity.get_section_content(account_id, section)

        from infrastructure.llm.prompt_loader import load_prompt

        prompt_file = f"{_PROMPTS_DIR}/rotator_consolidate.md"
        sys_prompt = load_prompt(prompt_file, lang=lang, section="system").format(ai_name=ai_name)
        user_prompt = load_prompt(prompt_file, lang=lang, section="user").format(
            ai_name=ai_name,
            section=section,
            count=count,
            full_identity=full_identity,
            section_content=section_content,
            notes=notes,
        )

        raw = await _complete(api_key, sys_prompt, user_prompt, temperature=0.7, max_tokens=_STEP_MAX_TOKENS)
        if not raw:
            continue

        lines = [
            ln.strip() for ln in raw.strip().splitlines()
            if ln.strip() and ln.strip().startswith("- ")
        ]
        if lines:
            new_body = "\n".join(lines)
            identity.replace_section(account_id, section, new_body)
            updated = True
            logger.info(
                "[rotator:%s] consolidated «%s»: %d → %d points",
                account_id, section, count, len(lines),
            )
        else:
            logger.warning(
                "[rotator:%s] consolidation «%s»: LLM returned no bullet points, skipping",
                account_id, section,
            )

    return updated


# ── Step 5: canon promotion ─────────────────────────────────────────────────

# PROMOTE / INTO / PILLAR, in either language.
_PROMOTE_RE = re.compile(
    r"(?:ПЕРЕВЕСТИ|PROMOTE):\s*(?P<beam>.+?)\s*\n"
    r"(?:В\s+РАЗДЕЛ|INTO):\s*(?P<into>.+?)\s*\n"
    r"(?:СТОЛП|PILLAR):\s*(?P<pillar>.+?)\s*(?:\n\s*\n|\n(?=(?:ПЕРЕВЕСТИ|PROMOTE):)|\Z)",
    re.IGNORECASE | re.DOTALL,
)


async def _promote_canon(
    account_id: str,
    api_key: str,
    lang: str,
    notes_block: str = "",
) -> int:
    """Promote finished beams out of Canon into the pillars.

    Canon overflowing is not a trimming problem: a beam that has done its work
    has already become part of who he is, so it moves into a pillar as an
    undated formulation rather than being dropped. Returns how many moved.
    """
    if not identity.needs_promotion(account_id):
        return 0

    section = identity.canon_section(identity.file_lang(account_id))
    count = len(identity.canon_entries(account_id))
    promote_min = max(1, count - identity.CANON_TARGET_MAX)
    promote_max = max(promote_min, count - identity.CANON_TARGET_MIN)
    logger.info(
        "[rotator:%s] canon at %d beams, promoting %d-%d",
        account_id, count, promote_min, promote_max,
    )

    from infrastructure.llm.prompt_loader import load_prompt

    path = f"{_PROMPTS_DIR}/rotator_canon.md"
    ai_name = get_ai_name()
    fields = dict(
        ai_name=ai_name,
        section=section,
        count=count,
        full_identity=identity.read(account_id),
        section_content=identity.get_section_content(account_id, section),
        notes=notes_block or ("(нет свежих заметок)" if lang == "ru" else "(no recent notes)"),
        target_min=identity.CANON_TARGET_MIN,
        target_max=identity.CANON_TARGET_MAX,
        promote_min=promote_min,
        promote_max=promote_max,
    )
    sys_prompt = load_prompt(path, lang=lang, section="system").format(**fields)
    user_prompt = load_prompt(path, lang=lang, section="user").format(**fields)

    raw = await _complete(api_key, sys_prompt, user_prompt, temperature=0.7, max_tokens=_STEP_MAX_TOKENS)
    if not raw or raw.strip().lower() in ("нет", "no"):
        logger.info("[rotator:%s] canon: nothing ready to promote", account_id)
        return 0

    promoted = 0
    for match in _PROMOTE_RE.finditer(raw):
        if promoted >= promote_max:
            logger.info("[rotator:%s] canon: hit the promotion cap, ignoring the rest", account_id)
            break
        target = identity.resolve_section(account_id, match.group("into").strip())
        if target is None or identity.is_canon(target):
            logger.warning(
                "[rotator:%s] canon: bad target section %r", account_id, match.group("into")[:60]
            )
            continue
        if identity.promote_beam(
            account_id,
            beam=match.group("beam").strip(),
            target_section=target,
            pillar_text=match.group("pillar").strip(),
        ):
            promoted += 1

    if promoted < promote_min:
        logger.warning(
            "[rotator:%s] canon: promoted %d of the %d needed — still over the ceiling",
            account_id, promoted, promote_min,
        )
    return promoted


# ── Orchestrator ─────────────────────────────────────────────────────────────

async def run(account_id: str, api_key: str) -> dict:
    """Run the full workbench rotation pipeline.

    Returns a summary dict with counts for each step.
    """
    result = {
        "rotated": 0,
        "insights": 0,
        "identity_updated": False,
        "consolidated": False,
        "promoted": 0,
        "people_moved": 0,
        "people_rebuilt": 0,
    }

    # Step 1: archive stale notes
    stale = await _rotate_to_archive(account_id)
    result["rotated"] = len(stale)
    if not stale:
        # Still run consolidation and promotion even when nothing rotated
        lang = detect_lang(identity.read(account_id))
        result["consolidated"] = await _consolidate_identity(account_id, api_key, lang, notes_block="")
        try:
            result["promoted"] = await _promote_canon(account_id, api_key, lang, notes_block="")
        except Exception as exc:
            logger.error("[rotator:%s] canon promotion error: %s", account_id, exc)
        return result

    notes_block = "\n---\n".join(
        f"[{ts}]\n{text}" for ts, text in stale
    )

    lang = detect_lang(notes_block)

    # Before the identity review, so "My people" is judged against a book that
    # already holds what these notes had to say.
    try:
        result["people_moved"] = await _sort_group_notes(account_id, stale, api_key, lang)
        result["people_rebuilt"] = await _consolidate_people(account_id, api_key, lang)
    except Exception as exc:
        logger.error("[rotator:%s] address book error: %s", account_id, exc)

    # Step 2: extract self-insights
    try:
        result["insights"] = await _extract_self_insights(
            account_id, notes_block, api_key, lang,
        )
    except Exception as exc:
        logger.error("[rotator:%s] self-insight error: %s", account_id, exc)

    # Step 3: identity review
    try:
        result["identity_updated"] = await _review_identity(
            account_id, notes_block, api_key, lang,
        )
    except Exception as exc:
        logger.error("[rotator:%s] identity review error: %s", account_id, exc)

    # Step 4: consolidation
    try:
        result["consolidated"] = await _consolidate_identity(
            account_id, api_key, lang, notes_block=notes_block,
        )
    except Exception as exc:
        logger.error("[rotator:%s] consolidation error: %s", account_id, exc)

    # Step 5: canon promotion
    try:
        result["promoted"] = await _promote_canon(
            account_id, api_key, lang, notes_block=notes_block,
        )
    except Exception as exc:
        logger.error("[rotator:%s] canon promotion error: %s", account_id, exc)

    logger.info("[rotator:%s] done: %s", account_id, result)
    return result
