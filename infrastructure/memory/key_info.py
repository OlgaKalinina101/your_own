"""
Key-info extraction pipeline for Chroma long-term memory.

When the AI emits [SAVE_MEMORY: <text>] in its response, this module:
  1. Asks the LLM to extract a clean memory fact + category from the last few
     conversation pairs (KEY_INFO_PROMPTS).
  2. Asks the LLM to rate the fact's impressiveness 1-4 (IMPRESSIVE_RATING_PROMPT).
  3. Stores the result in ChromaDB via ChromaMemoryPipeline.

All LLM calls use a fast model (configurable) and are non-streaming.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from infrastructure.autonomy.helpers import make_llm_client
from infrastructure.llm.prompt_loader import get_prompt

logger = logging.getLogger(__name__)

_PROMPTS_DIR = "infrastructure/memory/prompts"


# ── Prompts ───────────────────────────────────────────────────────────────────

async def _complete(api_key: str, system: str, user: str) -> str:
    client = make_llm_client(api_key)
    return await client.complete(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=650,
        temperature=0.7,
    )


# ── Format conversation pairs for the prompt ──────────────────────────────────

def _format_pairs(pairs: list[dict]) -> str:
    """
    pairs: list of {"role": "user"/"assistant", "content": str}
    Человек = the human, Я = the AI (first-person perspective).
    """
    lines: list[str] = []
    for msg in pairs:
        role_label = "Человек" if msg["role"] == "user" else "Я"
        content = (msg.get("content") or "").strip()
        if content:
            lines.append(f"{role_label}: {content}")
    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

async def extract_and_store(
    api_key: str,
    account_id: str,
    recent_pairs: list[dict],
    hint: str = "",
) -> Optional[dict]:
    """
    Extract a key fact from recent_pairs and store it in Chroma.

    recent_pairs: last 2-3 conversation pairs in {"role": ..., "content": ...} format.
    hint: the text from [SAVE_MEMORY: <hint>] command — guides extraction.

    Returns dict with fact/category/impressive on success, None on failure.
    """

    if not recent_pairs:
        return None

    pairs_text = _format_pairs(recent_pairs)

    from infrastructure.memory.focus_point import detect_language
    lang = detect_language(hint or pairs_text)

    hint_block = ""
    if hint.strip():
        if lang == "ru":
            hint_block = (
                f"\n\nЯ уже решил сохранить именно это: \"{hint}\"\n"
                "Используй это как ориентир. Перепиши от первого лица (я, мне, мы, наш), "
                "сохрани суть и детали. Не игнорируй подсказку — она указывает на конкретный факт."
            )
        else:
            hint_block = (
                f"\n\nI already decided to save this: \"{hint}\"\n"
                "Use this as a guide. Rewrite from first person (I, me, we, our), "
                "preserve the essence and details. Don't ignore the hint — it points to the specific fact."
            )

    # Step 1: extract fact + category
    key_info_sys = get_prompt(f"{_PROMPTS_DIR}/key_info_extraction.md", lang=lang, section="system")
    key_info_user = get_prompt(
        f"{_PROMPTS_DIR}/key_info_extraction.md",
        lang=lang, section="user",
        pairs=pairs_text,
        hint_block=hint_block,
    )
    raw = await _complete(api_key, key_info_sys, key_info_user)
    if not raw:
        logger.warning("[key_info] _complete returned empty for hint=%s", (hint or pairs_text)[:80])
        return None
    logger.info("[key_info] extraction raw=%s", raw[:200])

    try:
        # Try to extract JSON even if wrapped in markdown fences
        json_str = raw
        if "```" in raw:
            import re
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            if m:
                json_str = m.group(1)
        parsed = json.loads(json_str)
        fact_raw = parsed.get("fact")
        category_raw = parsed.get("category")
        # LLM may return null to indicate nothing worth saving
        if fact_raw is None or (isinstance(fact_raw, str) and not fact_raw.strip()):
            logger.info("[key_info] LLM decided nothing worth saving")
            return None
        fact = str(fact_raw).strip()
        category = str(category_raw).strip() if category_raw else "Другое"
    except (json.JSONDecodeError, AttributeError):
        logger.warning("[key_info] could not parse JSON from: %s", raw[:200])
        return None

    if not fact:
        return None

    # Step 2: rate impressiveness
    imp_sys = get_prompt(f"{_PROMPTS_DIR}/key_info_impressive.md", lang=lang, section="system")
    imp_user = get_prompt(f"{_PROMPTS_DIR}/key_info_impressive.md", lang=lang, section="user", fact=fact)
    imp_raw = await _complete(api_key, imp_sys, imp_user)
    try:
        impressive = int(imp_raw.strip()[0])
        impressive = max(1, min(4, impressive))
    except (ValueError, IndexError):
        impressive = 2

    # Step 3+4: dedup + store
    return await store_fact_with_dedup(
        api_key=api_key,
        account_id=account_id,
        fact=fact,
        category=category,
        impressive=impressive,
    )


async def store_fact_with_dedup(
    *,
    api_key: str,
    account_id: str,
    fact: str,
    category: str,
    impressive: int = 2,
) -> Optional[dict]:
    """Check for duplicates via LLM, then store in Chroma.

    Used by both ``extract_and_store`` (chat) and the workbench rotator.
    Returns dict with fact/category/impressive/id on success, None on skip.
    """
    from infrastructure.memory.chroma_pipeline import get_chroma_pipeline

    pipeline = get_chroma_pipeline()
    similar = pipeline.find_similar(account_id=account_id, memory=fact)

    if similar:
        old_fact = similar["text"]
        logger.info(
            "[key_info] dedup: found similar fact (dist=%.3f) id=%s: %s",
            similar["distance"], similar["id"], old_fact[:120],
        )
        from infrastructure.memory.focus_point import detect_language
        dup_lang = detect_language(fact + " " + old_fact)
        dedup_sys = get_prompt(f"{_PROMPTS_DIR}/key_info_dedup.md", lang=dup_lang, section="system")
        dedup_user = get_prompt(
            f"{_PROMPTS_DIR}/key_info_dedup.md",
            lang=dup_lang, section="user",
            old_fact=old_fact,
            new_fact=fact,
        )
        dedup_raw = await _complete(api_key, dedup_sys, dedup_user)

        action = "keep_both"
        reason = ""
        if dedup_raw:
            try:
                dedup_json = dedup_raw
                if "```" in dedup_raw:
                    import re as _re
                    m = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", dedup_raw, _re.DOTALL)
                    if m:
                        dedup_json = m.group(1)
                parsed_dedup = json.loads(dedup_json)
                action = parsed_dedup.get("action", "keep_both")
                reason = parsed_dedup.get("reason", "")
            except (json.JSONDecodeError, AttributeError):
                logger.warning("[key_info] dedup: could not parse AI response: %s", dedup_raw[:200])

        logger.info("[key_info] dedup AI decision: %s — %s", action, reason)

        if action == "skip":
            return {"fact": fact, "category": category, "impressive": impressive, "id": similar["id"], "dedup": "skipped"}
        elif action == "replace":
            pipeline.delete_entry(similar["id"])
            logger.info("[key_info] dedup: deleted old fact id=%s, saving new one", similar["id"])
            doc_id = pipeline.add_entry(
                account_id=account_id, memory=fact,
                category=category, impressive=impressive,
            )
            return {"fact": fact, "category": category, "impressive": impressive, "id": doc_id, "dedup": "replaced"}

    doc_id = pipeline.add_entry(
        account_id=account_id,
        memory=fact,
        category=category,
        impressive=impressive,
    )

    result = {"fact": fact, "category": category, "impressive": impressive, "id": doc_id}
    logger.info("[key_info] stored fact: %s", result)
    return result
