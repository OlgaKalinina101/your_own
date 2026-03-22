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

import logging

from infrastructure.autonomy import identity_memory as identity
from infrastructure.autonomy import workbench as wb
from infrastructure.autonomy.helpers import detect_lang, get_ai_name, make_llm_client
from infrastructure.memory.chroma_pipeline import get_chroma_pipeline
from infrastructure.llm.prompt_loader import get_prompt

logger = logging.getLogger("autonomy.rotator")

_PROMPTS_DIR = "infrastructure/autonomy/prompts"


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

def _rotate_to_archive(account_id: str) -> list[tuple[str, str]]:
    """Move stale workbench entries into the workbench_archive Chroma collection.

    Returns list of (timestamp, text) tuples that were rotated.
    """
    stale = wb.get_stale_entries(account_id)
    if not stale:
        return []

    pipeline = get_chroma_pipeline()
    for ts_str, text in stale:
        pipeline.add_archive_entry(
            account_id=account_id,
            text=text,
            timestamp=ts_str,
        )

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
    raw = await _complete(api_key, sys_msg, user_prompt, temperature=0.7, max_tokens=1500)
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
    sys_prompt = get_prompt(f"{_PROMPTS_DIR}/rotator_identity.md", lang=lang, section="system")
    user_prompt = get_prompt(
        f"{_PROMPTS_DIR}/rotator_identity.md",
        lang=lang, section="user",
        identity=identity_content,
        notes=notes_block,
    )
    raw = await _complete(api_key, sys_prompt, user_prompt, temperature=0.7, max_tokens=1500)
    if not raw or raw.strip().lower() in ("нет", "no"):
        return False

    resp = raw.strip()
    sections = identity.get_sections(identity.file_lang(account_id))

    # Format: ОБНОВИТЬ: раздел  (RU)  /  UPDATE: section  (EN)
    # followed by  ---\n- point\n---
    update_re = re.compile(
        r"(?:ОБНОВИТЬ|UPDATE):\s*(.+?)\s*\n-{3,}\s*\n(.*?)\n-{3,}",
        re.DOTALL | re.IGNORECASE,
    )
    update_m = update_re.search(resp)
    if update_m:
        section = update_m.group(1).strip()
        new_body = update_m.group(2).strip()
        lines = [ln.strip() for ln in new_body.splitlines() if ln.strip().startswith("- ")]
        if lines and section in sections:
            identity.replace_section(account_id, section, "\n".join(lines))
            logger.info("[rotator:%s] identity: updated «%s» (%d points)", account_id, section, len(lines))
            return True
        logger.warning("[rotator:%s] UPDATE for unknown section or no bullets: %r", account_id, section)

    return False


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

        section_content = ""
        header = f"## {section}"
        content = full_identity
        if header in content:
            idx = content.index(header)
            next_sec = content.find("\n## ", idx + len(header))
            section_content = content[idx:next_sec] if next_sec != -1 else content[idx:]

        from infrastructure.llm.prompt_loader import load_prompt

        sys_prompt = load_prompt(
            f"{_PROMPTS_DIR}/rotator_consolidate.md",
            lang=lang, section="system",
        ).format(ai_name=ai_name)
        user_prompt = load_prompt(
            f"{_PROMPTS_DIR}/rotator_consolidate.md",
            lang=lang, section="user",
        ).format(
            ai_name=ai_name,
            section=section,
            count=count,
            full_identity=full_identity,
            section_content=section_content,
            notes=notes,
        )

        raw = await _complete(api_key, sys_prompt, user_prompt, temperature=0.7, max_tokens=1500)
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
    }

    # Step 1: archive stale notes
    stale = _rotate_to_archive(account_id)
    result["rotated"] = len(stale)
    if not stale:
        # Still run consolidation even when nothing rotated
        lang = detect_lang(identity.read(account_id))
        result["consolidated"] = await _consolidate_identity(account_id, api_key, lang, notes_block="")
        return result

    notes_block = "\n---\n".join(
        f"[{ts}]\n{text}" for ts, text in stale
    )

    lang = detect_lang(notes_block)

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

    logger.info("[rotator:%s] done: %s", account_id, result)
    return result
