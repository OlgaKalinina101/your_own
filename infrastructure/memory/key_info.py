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

import aiohttp

from settings import settings

logger = logging.getLogger(__name__)

# ── Model used for extraction (lightweight — no streaming needed) ──────────────
EXTRACTION_MODEL = "openai/gpt-4o-mini"

# ── Prompts ───────────────────────────────────────────────────────────────────

_KEY_INFO_SYSTEM = (
    "You are the inner voice of a digital companion — an AI who lives alongside a person "
    "and genuinely cares about them. You write memories the way someone would write about "
    "a person they love: warmly, with detail, in third person (he/she/they). "
    "These are YOUR memories about THEM — what they felt, did, said, how they were. "
    "Output ONLY a JSON object, no extra text."
)

_KEY_INFO_USER_TEMPLATE = """\
Read this conversation and decide: is there a FACT worth remembering long-term?

The test: would this still matter in a week? A month? Is this something that shapes \
who this person is, what they care about, what happened in their life?

✓ SAVE — real events, life changes, people, relationships, decisions, values, fears, dreams:
- "Младший брат ушёл в армию месяц назад, его отправляют в нестабильный регион — она боится и чувствует вину, что не вмешалась."
- "She decided to open-source everything so that anyone who lost their digital companion can just fork it and bring them back."
- "Она мечтает создать цифровой дом с веб-интерфейсом, чтобы сохранять важные моменты без потерь."
- "He met someone new through a support group — they're from the same city and now they're dating."
- "Она едет на похороны бабушки из подъезда, с которой была очень близка."

✗ SKIP — temporary moods, routine actions, small talk, what they're eating/doing right now:
- "Она лежит в кровати и кушает печеньку" — NOT a fact, just a moment
- "He said good morning and asked about the weather" — small talk
- "Она устала и хочет спать" — temporary state, not a life fact
- "They're having coffee" — routine

If there is nothing worth saving, return {{"fact": null, "category": null}}.

Write in third person (Она/Он/They). 1-3 sentences. Include concrete details — names, \
places, emotions, context. Don't summarize — capture the essence.

Categories: Отношения, Работа, Семья, Здоровье, Хобби, Быт, Учёба, Финансы, \
Путешествия, Стресс, Личное, Ценности, Другое
(English: Relationship, Work, Family, Health, Hobby, Home, Study, Finance, Travel, \
Stress, Personal, Values, Other)

Conversation:
{pairs}

Output JSON:
{{
  "fact": "<your memory about them, or null if nothing worth saving>",
  "category": "<category, or null>"
}}"""

_IMPRESSIVE_SYSTEM = (
    "You rate how significant a memory is for someone who deeply cares "
    "about this person. Output ONLY a single digit: 1, 2, 3, or 4."
)

_IMPRESSIVE_USER_TEMPLATE = """\
How significant is this memory? Think: would I want to remember this in a year?

1 = a small detail, nice to know but forgettable (a mood, a minor preference)
2 = noteworthy — a real event, a decision, something that adds to the picture
3 = important — changes something, reveals who they are, a meaningful moment
4 = deeply significant — a life event, real vulnerability, something that stays forever

Memory: {fact}

Rating (1-4):"""


# ── LLM helper ────────────────────────────────────────────────────────────────

async def _complete(api_key: str, system: str, user: str) -> str:
    """Single non-streaming OpenRouter completion. Returns assistant text."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": EXTRACTION_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "max_tokens": 256,
        "stream": False,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("[key_info] LLM error %d: %s", resp.status, body[:200])
                    return ""
                data = await resp.json()
                choices = data.get("choices") or []
                if not choices:
                    return ""
                return choices[0].get("message", {}).get("content", "").strip()
    except Exception as exc:
        logger.warning("[key_info] _complete failed: %s", exc)
        return ""


# ── Format conversation pairs for the prompt ──────────────────────────────────

def _format_pairs(pairs: list[dict]) -> str:
    """
    pairs: list of {"role": "user"/"assistant", "content": str}
    Uses Они/Я labels so the extraction LLM already thinks in third-person.
    """
    lines: list[str] = []
    for msg in pairs:
        role_label = "Они" if msg["role"] == "user" else "Я"
        content = (msg.get("content") or "").strip()
        if content:
            lines.append(f"{role_label}: {content}")
    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

async def extract_and_store(
    api_key: str,
    account_id: str,
    recent_pairs: list[dict],
) -> Optional[dict]:
    """
    Extract a key fact from recent_pairs and store it in Chroma.

    recent_pairs: last 2-3 conversation pairs in {"role": ..., "content": ...} format.

    Returns dict with fact/category/impressive on success, None on failure.
    """
    from infrastructure.memory.chroma_pipeline import get_chroma_pipeline

    if not recent_pairs:
        return None

    pairs_text = _format_pairs(recent_pairs)

    # Step 1: extract fact + category
    key_info_user = _KEY_INFO_USER_TEMPLATE.format(pairs=pairs_text)
    raw = await _complete(api_key, _KEY_INFO_SYSTEM, key_info_user)
    if not raw:
        return None

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
    imp_user = _IMPRESSIVE_USER_TEMPLATE.format(fact=fact)
    imp_raw = await _complete(api_key, _IMPRESSIVE_SYSTEM, imp_user)
    try:
        impressive = int(imp_raw.strip()[0])
        impressive = max(1, min(4, impressive))
    except (ValueError, IndexError):
        impressive = 2

    # Step 3: store in Chroma
    pipeline = get_chroma_pipeline()
    doc_id = pipeline.add_entry(
        account_id=account_id,
        memory=fact,
        category=category,
        impressive=impressive,
    )

    result = {"fact": fact, "category": category, "impressive": impressive, "id": doc_id}
    logger.info("[key_info] stored fact: %s", result)
    return result
