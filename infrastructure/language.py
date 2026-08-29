"""Which language to speak in, decided in one place.

Three copies of "does this contain Cyrillic" had grown up independently — in
``autonomy.helpers``, in ``memory.focus_point`` and privately inside
``autonomy.identity_memory`` — and they had already drifted: one of them did not
count Ukrainian letters. They all delegate here now.

The rule has two halves, and the second one is the reason this module exists:

1. Cyrillic wins outright; any other letters mean English.
2. Text with **no letters at all** is not evidence of anything. An empty
   dialogue, a photo sent without a caption, a bare timestamp — guessing
   English there is a guess dressed as a detection, and it showed: a first-ever
   failed waking wrote its note into his journal in English on a Russian
   instance, and a message that was only a picture was filed as English. With
   nothing to go on, fall back to the language *he* is written in — the soul
   prompt.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("language")

_CYRILLIC = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]")

RU = "ru"
EN = "en"


def detect(text: str | None) -> str:
    """Language of *text* alone, with no fallback. Letterless text reads as English."""
    return RU if _CYRILLIC.search(text or "") else EN


def has_evidence(text: str | None) -> bool:
    """True when *text* contains any letter at all, in any script."""
    return any(ch.isalpha() for ch in text or "")


def soul_language() -> str:
    """The language he is written in, from ``data/soul.md``.

    Falls back to English if the soul cannot be read — an unwritten soul is the
    one case where there genuinely is nothing to go on.
    """
    try:
        from infrastructure.settings_store import load_soul
        return detect(load_soul())
    except Exception:                                   # pragma: no cover
        logger.warning("could not read soul for language, assuming %s", EN)
        return EN


def detect_or_soul(text: str | None) -> str:
    """Language of *text*, or — when *text* says nothing — of the soul prompt."""
    return detect(text) if has_evidence(text) else soul_language()
