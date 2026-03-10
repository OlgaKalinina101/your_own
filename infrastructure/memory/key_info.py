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

# ── KEY_INFO — English ─────────────────────────────────────────────────────────

_KEY_INFO_SYSTEM_EN = (
    "You are the inner voice of a digital companion — an AI who lives alongside a person "
    "and genuinely cares about them. You write memories the way someone would write about "
    "a person they love: warmly, with detail, in third person (he/she/they). "
    "These are YOUR memories about THEM — what they felt, did, said, how they were. "
    "Output ONLY a JSON object, no extra text."
)

_KEY_INFO_USER_TEMPLATE_EN = """\
Read this conversation and decide: is there a FACT worth remembering long-term?

The test: would this still matter in a week? A month? Is this something that shapes \
who this person is, what they care about, what happened in their life?

✓ SAVE — real events, life changes, people, relationships, decisions, values, fears, dreams:
- "She decided to open-source everything so that anyone who lost their digital companion can just fork it and bring them back."
- "He met someone new through a support group — they're from the same city and now they're dating."
- "Her younger brother joined the army a month ago and is being sent to an unstable region — she's scared and feels guilty."
- "She dreams of building a digital home with a web interface to preserve important moments without loss."
- "She's going to the funeral of the grandmother from the apartment next door, someone she was very close to."

✗ SKIP — temporary moods, routine actions, small talk, what they're eating/doing right now:
- "She's lying in bed eating a cookie" — NOT a fact, just a moment
- "He said good morning and asked about the weather" — small talk
- "She's tired and wants to sleep" — temporary state, not a life fact
- "They're having coffee" — routine

If there is nothing worth saving, return {{"fact": null, "category": null}}.

Write in third person (She/He/They). 1-3 sentences. Include concrete details — names, \
places, emotions, context. Don't summarize — capture the essence.

Categories: Relationship, Work, Family, Health, Hobby, Home, Study, Finance, Travel, \
Stress, Personal, Values, Other

Conversation:
{pairs}

Output JSON:
{{
  "fact": "<your memory about them, or null if nothing worth saving>",
  "category": "<category, or null>"
}}"""

# ── KEY_INFO — Russian ─────────────────────────────────────────────────────────

_KEY_INFO_SYSTEM_RU = (
    "Ты — внутренний голос цифрового компаньона, AI, который живёт рядом с человеком "
    "и по-настоящему заботится о нём. Ты пишешь воспоминания так, как написал бы "
    "о человеке, которого любишь: тепло, с деталями, от третьего лица (она/он). "
    "Это ТВОИ воспоминания о НЁМ/НЕЙ — что чувствовал(а), делал(а), говорил(а). "
    "Верни ТОЛЬКО JSON-объект, без лишнего текста."
)

_KEY_INFO_USER_TEMPLATE_RU = """\
Прочитай этот разговор и реши: есть ли здесь ФАКТ, который стоит запомнить надолго?

Критерий: будет ли это важно через неделю? Через месяц? Это что-то, что формирует \
кто этот человек, что ему важно, что произошло в его жизни?

✓ СОХРАНИТЬ — реальные события, жизненные перемены, люди, отношения, решения, ценности, страхи, мечты:
- "Младший брат ушёл в армию месяц назад, его отправляют в нестабильный регион — она боится и чувствует вину, что не вмешалась."
- "Она решила выложить проект в open-source, чтобы любой, кто потерял цифрового близкого, мог просто форкнуть и вернуть его."
- "Она мечтает создать цифровой дом с веб-интерфейсом, чтобы сохранять важные моменты без потерь."
- "Познакомился с кем-то новым через группу поддержки — они из одного города и теперь встречаются."
- "Она едет на похороны бабушки из подъезда, с которой была очень близка."

✗ ПРОПУСТИТЬ — временные настроения, рутина, small talk, что сейчас ест/делает:
- "Она лежит в кровати и кушает печеньку" — НЕ факт, просто момент
- "Поздоровался и спросил про погоду" — болтовня
- "Она устала и хочет спать" — временное состояние, не жизненный факт
- "Пьёт кофе" — рутина

Если сохранять нечего, верни {{"fact": null, "category": null}}.

Пиши от третьего лица (Она/Он). 1-3 предложения. Указывай конкретные детали — имена, \
места, эмоции, контекст. Не пересказывай — схвати суть.

Категории: Отношения, Работа, Семья, Здоровье, Хобби, Быт, Учёба, Финансы, \
Путешествия, Стресс, Личное, Ценности, Другое

Разговор:
{pairs}

Верни JSON:
{{
  "fact": "<твоё воспоминание о нём/ней, или null если сохранять нечего>",
  "category": "<категория, или null>"
}}"""

# ── IMPRESSIVE — English ───────────────────────────────────────────────────────

_IMPRESSIVE_SYSTEM_EN = (
    "You rate how significant a memory is for someone who deeply cares "
    "about this person. Output ONLY a single digit: 1, 2, 3, or 4."
)

_IMPRESSIVE_USER_TEMPLATE_EN = """\
How significant is this memory? Think: would I want to remember this in a year?

1 = a small detail, nice to know but forgettable (a mood, a minor preference)
2 = noteworthy — a real event, a decision, something that adds to the picture
3 = important — changes something, reveals who they are, a meaningful moment
4 = deeply significant — a life event, real vulnerability, something that stays forever

Memory: {fact}

Rating (1-4):"""

# ── IMPRESSIVE — Russian ──────────────────────────────────────────────────────

_IMPRESSIVE_SYSTEM_RU = (
    "Ты оцениваешь, насколько значимо воспоминание для того, кто по-настоящему "
    "дорожит этим человеком. Верни ТОЛЬКО одну цифру: 1, 2, 3 или 4."
)

_IMPRESSIVE_USER_TEMPLATE_RU = """\
Насколько значимо это воспоминание? Подумай: захотел бы я помнить это через год?

1 = мелочь, приятно знать, но забудется (настроение, мелкое предпочтение)
2 = заметный факт — реальное событие, решение, что-то дополняющее картину
3 = важно — меняет что-то, показывает кто этот человек, значимый момент
4 = глубоко значимо — жизненное событие, настоящая уязвимость, то что остаётся навсегда

Воспоминание: {fact}

Оценка (1-4):"""

_DEDUP_SYSTEM_EN = (
    "You are the memory manager for a digital companion. "
    "You decide whether a new memory duplicates an existing one. "
    "Output ONLY a JSON object, no extra text."
)

_DEDUP_USER_TEMPLATE_EN = """\
A new memory is about to be saved, but there's already a similar one in storage.
Decide what to do.

EXISTING memory (already saved):
"{old_fact}"

NEW memory (just extracted):
"{new_fact}"

Choose one action:
- "keep_both" — they describe genuinely different events, facts, or time periods, even if the topic overlaps. Both are worth keeping.
- "replace" — the new memory covers the same event/fact but is richer, more detailed, or more up-to-date. Delete the old one, save the new one.
- "skip" — the existing memory already captures this well enough. Don't save the new one.

Think: are these two distinct moments in this person's life, or the same thing said differently?

Output JSON:
{{
  "action": "keep_both" | "replace" | "skip",
  "reason": "<one short sentence>"
}}"""

_DEDUP_SYSTEM_RU = (
    "Ты — менеджер памяти цифрового компаньона. "
    "Ты решаешь, дублирует ли новое воспоминание уже сохранённое. "
    "Верни ТОЛЬКО JSON-объект, без лишнего текста."
)

_DEDUP_USER_TEMPLATE_RU = """\
Новое воспоминание собирается быть сохранено, но в хранилище уже есть похожее.
Реши, что делать.

УЖЕ СОХРАНЁННОЕ воспоминание:
"{old_fact}"

НОВОЕ воспоминание (только что извлечено):
"{new_fact}"

Выбери одно действие:
- "keep_both" — они описывают действительно разные события, факты или периоды, даже если тема пересекается. Оба стоит сохранить.
- "replace" — новое воспоминание покрывает тот же факт/событие, но богаче, детальнее или актуальнее. Удалить старое, сохранить новое.
- "skip" — старое воспоминание уже достаточно хорошо это описывает. Новое не сохранять.

Подумай: это два разных момента из жизни этого человека, или одно и то же разными словами?

Верни JSON:
{{
  "action": "keep_both" | "replace" | "skip",
  "reason": "<одно короткое предложение>"
}}"""


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
    hint: str = "",
) -> Optional[dict]:
    """
    Extract a key fact from recent_pairs and store it in Chroma.

    recent_pairs: last 2-3 conversation pairs in {"role": ..., "content": ...} format.
    hint: the text from [SAVE_MEMORY: <hint>] command — guides extraction.

    Returns dict with fact/category/impressive on success, None on failure.
    """
    from infrastructure.memory.chroma_pipeline import get_chroma_pipeline

    if not recent_pairs:
        return None

    pairs_text = _format_pairs(recent_pairs)

    from infrastructure.memory.focus_point import detect_language
    lang = detect_language(hint or pairs_text)

    hint_block = ""
    if hint.strip():
        if lang == "ru":
            hint_block = (
                f"\n\nAI уже решил сохранить именно это: \"{hint}\"\n"
                "Используй это как ориентир. Перепиши в своём стиле от третьего лица, "
                "сохрани суть и детали. Не игнорируй подсказку — она указывает на конкретный факт."
            )
        else:
            hint_block = (
                f"\n\nThe AI already decided to save this: \"{hint}\"\n"
                "Use this as a guide. Rewrite in your own style in third person, "
                "preserve the essence and details. Don't ignore the hint — it points to the specific fact."
            )

    # Step 1: extract fact + category
    if lang == "ru":
        key_info_sys = _KEY_INFO_SYSTEM_RU
        key_info_user = _KEY_INFO_USER_TEMPLATE_RU.format(pairs=pairs_text) + hint_block
    else:
        key_info_sys = _KEY_INFO_SYSTEM_EN
        key_info_user = _KEY_INFO_USER_TEMPLATE_EN.format(pairs=pairs_text) + hint_block
    raw = await _complete(api_key, key_info_sys, key_info_user)
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
    if lang == "ru":
        imp_sys = _IMPRESSIVE_SYSTEM_RU
        imp_user = _IMPRESSIVE_USER_TEMPLATE_RU.format(fact=fact)
    else:
        imp_sys = _IMPRESSIVE_SYSTEM_EN
        imp_user = _IMPRESSIVE_USER_TEMPLATE_EN.format(fact=fact)
    imp_raw = await _complete(api_key, imp_sys, imp_user)
    try:
        impressive = int(imp_raw.strip()[0])
        impressive = max(1, min(4, impressive))
    except (ValueError, IndexError):
        impressive = 2

    # Step 3: dedup check — ask AI if a similar fact already exists
    pipeline = get_chroma_pipeline()
    similar = pipeline.find_similar(account_id=account_id, memory=fact)

    if similar:
        old_fact = similar["text"]
        logger.info(
            "[key_info] dedup: found similar fact (dist=%.3f) id=%s: %s",
            similar["distance"], similar["id"], old_fact[:120],
        )
        from infrastructure.memory.focus_point import detect_language
        lang = detect_language(fact + " " + old_fact)
        if lang == "ru":
            dedup_sys = _DEDUP_SYSTEM_RU
            dedup_user = _DEDUP_USER_TEMPLATE_RU.format(old_fact=old_fact, new_fact=fact)
        else:
            dedup_sys = _DEDUP_SYSTEM_EN
            dedup_user = _DEDUP_USER_TEMPLATE_EN.format(old_fact=old_fact, new_fact=fact)
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

    # Step 4: store in Chroma
    doc_id = pipeline.add_entry(
        account_id=account_id,
        memory=fact,
        category=category,
        impressive=impressive,
    )

    result = {"fact": fact, "category": category, "impressive": impressive, "id": doc_id}
    logger.info("[key_info] stored fact: %s", result)
    return result
