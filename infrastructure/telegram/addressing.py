"""Whether a line in the room is calling him — by whatever name he has.

His name lives in settings and nowhere else. It can be any name, in any
language, and it can change; nothing here may know that it is "Виктор" today.

The first version matched the name as a whole word, and the first day of the
live group showed what that misses: «передай Виктору», «поздоровайся с
Виктором», and «Витька» — the nickname a friend gave him within the hour. Two
different problems, solved two different ways:

**Cases are grammar.** «Виктору» follows from «Виктор» by rule, so the rule
engine the project already has — pymorphy3 — produces the forms. It is
deterministic, free and instant, and it predicts forms for names it has never
seen. For a name it cannot make sense of it guesses wildly («Люми» comes out as
forms of the verb «лить»), so only noun forms that still start with the name's
stem are kept. A name in Latin script has no cases; ``Victor's`` already
matches on the word boundary.

**So is the script.** On the live server the setting turned out to be
``Victor AI`` — Latin, two words — while every friend writes «Виктор». No rule
gets from one to the other; it is the first thing the model is asked for. And a
name of several words is heard by its parts: «Victor» out of «Victor AI».

**Nicknames are knowledge.** «Витька» does not follow from «Виктор» by any
rule; someone has to know it. So a model is asked, once per name: what would
friends call this person in a chat? The answer is written into settings, where
she sees it and edits it — the model proposes, she decides. It is asked again
only when the name itself changes, never because the list was trimmed.
"""
from __future__ import annotations

import asyncio
import re
import time
from functools import lru_cache

from infrastructure.logging.logger import setup_logger

logger = setup_logger("telegram.addressing")

_PROMPT = "infrastructure/telegram/prompts/name_aliases.md"

MAX_ALIASES = 12          # the whole list; he and she add to it over time
MAX_PROPOSED = 6          # how many of those the model may seed
_ALIAS_RE = re.compile(r"^[^\W\d_]{3,15}$")     # one word, letters only
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
# A failed attempt is not repeated on every message in a busy room.
_RETRY_AFTER_SECONDS = 600

_generating = False
_last_failure: float | None = None    # None, not 0.0: monotonic() is small right after boot


def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


# ── Cases ────────────────────────────────────────────────────────────────────


def _inflections(word: str) -> set[str]:
    """Every case form of *word* that is recognisably still that word."""
    base = _norm(word).strip()
    forms = {base}
    if not base or not _CYRILLIC_RE.search(base):
        return forms
    try:
        from infrastructure.memory.focus_point import _get_morph_ru

        morph = _get_morph_ru()
        # Two letters at the least, not three: «Ева» → «Евы», «Оля» → «Оле» keep
        # only two of their three. Found when a card for Ева was not handed
        # over on «это дети Евы».
        stem = base[: max(2, len(base) - 2)]
        for parse in morph.parse(base)[:3]:
            if "NOUN" not in str(parse.tag):
                continue
            for form in parse.lexeme:
                candidate = _norm(form.word)
                if candidate.startswith(stem) and len(candidate) >= 3:
                    forms.add(candidate)
    except Exception as exc:
        # Without morphology he still hears his name in the nominative — a
        # thinner ear, not a deaf one. Said once per process by the cache.
        logger.warning("[addressing] morphology unavailable, matching %r as written: %s", base, exc)
    return forms


_NAME_PART_MIN = 3     # «AI», «Jr» and initials are not what anyone calls him


def _parts(name: str) -> list[str]:
    """The name as given, plus each word of it long enough to be a name."""
    whole = " ".join((name or "").split())
    words = [w for w in re.split(r"[^\w]+", whole) if len(w) >= _NAME_PART_MIN and not w.isdigit()]
    return [whole, *[w for w in words if _norm(w) != _norm(whole)]] if whole else []


# One matcher per set of names. The address book calls this once per person, so
# the cache has to hold a book, not just him: at 16 a book of 31 cards rebuilt
# half its matchers — morphology and all — on every reply in the room.
@lru_cache(maxsize=512)
def _matcher(names: tuple[str, ...], handle: str) -> re.Pattern | None:
    forms: set[str] = set()
    for name in names:
        for part in _parts(name):
            forms |= _inflections(part)
    forms.discard("")
    parts = [re.escape(f) for f in sorted(forms, key=len, reverse=True)]
    pattern = rf"(?<!\w)(?:{'|'.join(parts)})(?!\w)" if parts else ""
    if handle:
        at = re.escape(_norm(handle if handle.startswith("@") else f"@{handle}"))
        pattern = f"{pattern}|{at}" if pattern else at
    return re.compile(pattern, re.IGNORECASE) if pattern else None


def mentions(text: str, *, ai_name: str, aliases: list[str] | tuple[str, ...] = (), handle: str = "") -> bool:
    """Is he named in *text* — by name in any case, by a nickname, or by handle?"""
    names = tuple(n.strip() for n in (ai_name, *aliases) if n and n.strip())
    matcher = _matcher(names, (handle or "").strip())
    return bool(matcher and matcher.search(_norm(text)))


def known_forms(ai_name: str, aliases: list[str] | tuple[str, ...] = ()) -> list[str]:
    """What he answers to, spelled out — for the settings page and the log."""
    out: set[str] = set()
    for name in (ai_name, *aliases):
        for part in _parts(name):
            out |= _inflections(part)
    return sorted(out)


# ── Nicknames ────────────────────────────────────────────────────────────────


def parse_aliases(raw: str, ai_name: str) -> list[str]:
    """Single words out of whatever the model wrote, without the name itself."""
    seen: list[str] = []
    own = _norm(ai_name)
    for token in re.split(r"[\n,;]+", raw or ""):
        word = token.strip().strip("-•*«»\"'. ").strip()
        if not _ALIAS_RE.match(word):
            continue
        if _norm(word) == own or _norm(word) in (_norm(s) for s in seen):
            continue
        seen.append(word)
        if len(seen) >= MAX_PROPOSED:
            break
    return seen


def add_alias(word: str, lang: str = "ru") -> str:
    """He heard himself called something and wants to answer to it.

    This is the main way the list grows: nobody can guess «звёздочка», and the
    one who knows he was called that is him — in the room when it happens, or
    at a waking when he reads the room whole. Returns a sentence for him.
    """
    from infrastructure.autonomy.helpers import get_ai_name
    from infrastructure.settings_store import save_settings

    ru = lang == "ru"
    clean = (word or "").strip().strip("«»\"'. ").strip()
    if not _ALIAS_RE.match(clean):
        return (
            f"«{clean}» не подходит: нужно одно слово из букв, от трёх до пятнадцати."
            if ru else f"'{clean}' does not fit: one word, letters only, three to fifteen long."
        )
    existing = current_aliases()
    known = {_norm(a) for a in existing} | {_norm(p) for p in _parts(get_ai_name())}
    if _norm(clean) in known:
        return f"На «{clean}» ты уже откликаешься." if ru else f"You already answer to '{clean}'."
    if len(existing) >= MAX_ALIASES:
        return (
            "Список имён полон — лишнее можно убрать в настройках." if ru
            else "The list of names is full — trim it in settings."
        )
    save_settings({"telegram_aliases": existing + [clean]})
    logger.info("[addressing] he now answers to %r", clean)
    return f"Теперь ты откликаешься в общем чате и на «{clean}»." if ru else f"You now answer to '{clean}' in the group chat."


def current_aliases() -> list[str]:
    from infrastructure.settings_store import load_settings

    stored = load_settings().get("telegram_aliases") or []
    if isinstance(stored, str):
        stored = [part for part in re.split(r"[,;\n]+", stored)]
    return [str(a).strip() for a in stored if str(a).strip()]


async def ensure_aliases(api_key: str) -> None:
    """Ask for nicknames once per name, and write them where she can edit them.

    ``telegram_aliases_for`` records which name the list was made for. It is
    what stops a list she deliberately emptied from being filled in again.
    """
    global _generating, _last_failure
    from infrastructure.autonomy.helpers import detect_lang, get_ai_name, make_llm_client
    from infrastructure.llm.prompt_loader import get_prompt
    from infrastructure.settings_store import load_settings, save_settings

    ai_name = (get_ai_name() or "").strip()
    if not ai_name or ai_name == "AI" or not api_key:
        return
    if (load_settings().get("telegram_aliases_for") or "") == ai_name:
        return
    if _generating:
        return
    if _last_failure is not None and time.monotonic() - _last_failure < _RETRY_AFTER_SECONDS:
        return

    _generating = True
    try:
        # The language of the *room*, not of the spelling: a name set as
        # "Victor AI" among people who write «Виктор» must be asked about in
        # Russian. With nothing to detect from, this falls back to the soul's.
        lang = detect_lang("")
        text, finish = await make_llm_client(api_key).complete(
            messages=[
                {"role": "system", "content": get_prompt(_PROMPT, lang=lang, section="system")},
                {"role": "user", "content": get_prompt(_PROMPT, lang=lang, section="user", ai_name=ai_name)},
            ],
            max_tokens=4000, temperature=0.2, return_meta=True,
        )
        if finish == "length" or not (text or "").strip():
            raise RuntimeError("empty or clipped reply")
        proposed = parse_aliases(text, ai_name)
        kept = current_aliases()   # whatever she already typed stays, and stays first
        merged = kept + [a for a in proposed if _norm(a) not in {_norm(k) for k in kept}]
        save_settings({"telegram_aliases": merged[:MAX_ALIASES], "telegram_aliases_for": ai_name})
        logger.info("[addressing] nicknames for %r: %s", ai_name, ", ".join(merged) or "(none)")
    except Exception as exc:
        _last_failure = time.monotonic()
        logger.warning("[addressing] could not get nicknames for %r, will retry later: %s", ai_name, exc)
    finally:
        _generating = False


def ensure_aliases_in_background(api_key: str) -> None:
    """Fire and forget: a reply in the room never waits for this."""
    try:
        task = asyncio.get_running_loop().create_task(ensure_aliases(api_key))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    except RuntimeError:
        pass
