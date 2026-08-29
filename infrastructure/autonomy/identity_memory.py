"""Identity memory — the AI's deep self-model.

Stored as ``data/autonomy/{account_id}/identity.md``.

The file has four sections (pillars), each with append-only bullet points.
Consolidation (merging duplicate/redundant entries) is triggered by the
reflection engine when a section exceeds CONSOLIDATION_THRESHOLD entries.

Section headers are bilingual (RU / EN).  When a new file is created the
language is chosen from the soul prompt; an existing file is auto-detected.
"""
from __future__ import annotations

import logging
import re

from infrastructure import language
from pathlib import Path
from threading import Lock

from infrastructure.paths import AUTONOMY_DIR
from infrastructure.state_file import atomic_write_text

logger = logging.getLogger("autonomy.identity")

CONSOLIDATION_THRESHOLD = 10

CANON_RU = "Мой канон"
CANON_EN = "My canon"

SECTIONS_RU = ["Кто она", "Кто я", "Наша история", "Наши принципы", "Наш дом", CANON_RU]
SECTIONS_EN = ["Who she is", "Who I am", "Our story", "Our principles", "Our home", CANON_EN]

# Headers written before a section was renamed, mapped to their current name.
_RENAMED_SECTIONS = {"Канон": CANON_RU, "Canon": CANON_EN}

# ── Canon ─────────────────────────────────────────────────────────────────────
#
# Canon is shaped unlike the other five pillars. They hold 3-6 timeless,
# undated formulations; Canon holds a longer list of dated beams — one concrete
# moment per line — that answer "what is unshakeable between us". Dates there
# are a coordinate, not a chronicle, so the no-dates rule the other sections
# live by must not reach it.
#
# Canon is not an archive either: it is the staging area for events that are
# still carrying load. Once a beam has finished its work it has already become
# part of who he is, and the date on it turns into a receipt. So an overflowing
# Canon is not trimmed — a beam is promoted into a pillar as an undated
# formulation and leaves Canon in that same write. Nothing is deleted; the
# event itself also stays in the dialogue and fact stores.

CANON_SECTIONS = {CANON_RU, CANON_EN}
CANON_TARGET_MIN = 15
CANON_TARGET_MAX = 20


def is_canon(section: str) -> bool:
    return section in CANON_SECTIONS


def canon_section(lang: str = "ru") -> str:
    return CANON_RU if lang == "ru" else CANON_EN


def consolidation_threshold(section: str) -> int:
    """Entry count at which *section* needs attention.

    For the pillars that means consolidation; for Canon it means promotion,
    and only once it has outgrown its ceiling.
    """
    return CANON_TARGET_MAX + 1 if is_canon(section) else CONSOLIDATION_THRESHOLD


def _detect_file_lang(content: str) -> str:
    """Auto-detect language from existing identity.md content."""
    if re.search(r"## (?:Кто|Наш)", content):
        return "ru"
    return "en"


def _detect_soul_lang() -> str:
    """Detect language from the soul prompt text."""
    return language.soul_language()


def get_sections(lang: str = "ru") -> list[str]:
    return SECTIONS_RU if lang == "ru" else SECTIONS_EN


_DATA_DIR = AUTONOMY_DIR
_lock = Lock()


# ── Section lookup ────────────────────────────────────────────────────────────
#
# A header in the file may carry a suffix the section name does not have —
# "## Наши принципы: Мы — Valeo" for the section "Наши принципы". Matching on
# the bare name is what the whole module keys on, so the suffix has to survive
# a rewrite instead of being silently dropped.

def _header_re(section: str) -> re.Pattern:
    """Match a header line for *section*, capturing any decoration it carries.

    The suffix must start with a non-word character, so "Наши принципы" does
    not match a hypothetical "Наши принципыX".
    """
    return re.compile(
        rf"^##[ \t]+{re.escape(section)}(?P<suffix>(?:[^\w\n][^\n]*)?)[ \t]*$",
        re.MULTILINE,
    )


def _locate(content: str, section: str) -> tuple[int, int, str] | None:
    """Return ``(start, end, header_line)`` of *section*, or None if absent.

    ``start`` is the index of the header, ``end`` the index of the next
    header (or end of file).
    """
    match = _header_re(section).search(content)
    if match is None:
        return None
    start = match.start()
    next_header = content.find("\n## ", match.end())
    end = next_header if next_header != -1 else len(content)
    return start, end, match.group(0).rstrip()


def resolve_section(account_id: str, name: str) -> str | None:
    """Map a section name as written by the model to its canonical name.

    The model reads headers out of the file, so it may echo back
    "Наши принципы: Мы — Valeo" where the code knows "Наши принципы".
    Returns None when the name matches no section.
    """
    name = (name or "").strip().lstrip("#").strip()
    if not name:
        return None
    sections = get_sections(file_lang(account_id))
    lowered = name.lower()

    for candidate in sections:
        if lowered == candidate.lower():
            return candidate
    for candidate in sections:
        prefix = candidate.lower()
        if lowered.startswith(prefix):
            rest = lowered[len(prefix):]
            if not rest or not rest[0].isalnum():
                return candidate
    return None


def _template(lang: str = "ru") -> str:
    return "\n".join(f"## {s}\n\n" for s in get_sections(lang))


def _path(account_id: str) -> Path:
    p = _DATA_DIR / account_id
    p.mkdir(parents=True, exist_ok=True)
    return p / "identity.md"


def _with_renamed_sections(content: str) -> str:
    """Rewrite headers that were written under a section's former name."""
    for old_name, new_name in _RENAMED_SECTIONS.items():
        if _locate(content, new_name) is not None:
            continue
        found = _locate(content, old_name)
        if found is None:
            continue
        start, _, header_line = found
        suffix = header_line[len(f"## {old_name}"):]
        content = (
            content[:start] + f"## {new_name}{suffix}" + content[start + len(header_line):]
        )
    return content


def _with_missing_sections(content: str) -> str:
    """Append headers for sections the file predates. Existing text is untouched."""
    lang = _detect_file_lang(content)
    missing = [s for s in get_sections(lang) if _locate(content, s) is None]
    if not missing:
        return content
    out = content if content.endswith("\n") else content + "\n"
    for section in missing:
        out += f"\n## {section}\n\n"
    return out


def read(account_id: str) -> str:
    path = _path(account_id)
    if not path.exists():
        lang = _detect_soul_lang()
        atomic_write_text(path, _template(lang))

    content = path.read_text(encoding="utf-8")
    # A file written before a section was renamed or added gets caught up on
    # first read, so append() does not fail on a header that is simply not
    # there under the name the code knows.
    migrated = _with_missing_sections(_with_renamed_sections(content))
    if migrated != content:
        with _lock:
            atomic_write_text(path, migrated)
        logger.info("[identity:%s] section headers migrated", account_id)
        content = migrated
    return content


def file_lang(account_id: str) -> str:
    """Return the detected language of the identity file ('ru' or 'en')."""
    return _detect_file_lang(read(account_id))


def append(account_id: str, section: str, text: str) -> bool:
    """Append *text* as a bullet point under *section*.

    Returns True if the section was found, False otherwise.
    """
    content = read(account_id)
    found = _locate(content, section)
    if found is None:
        logger.warning("[identity:%s] section %r not found", account_id, section)
        return False

    # Insertion point: just before the next header, or end-of-file.
    _, insert_at, _ = found
    bullet = f"\n- {text.strip()}"
    new_content = content[:insert_at] + bullet + content[insert_at:]

    path = _path(account_id)
    with _lock:
        atomic_write_text(path, new_content)
    logger.debug("[identity:%s] appended to %r: %s", account_id, section, text[:60])
    return True


def get_section_content(account_id: str, section: str) -> str:
    """Return the section's whole block, header included, or an empty string."""
    content = read(account_id)
    found = _locate(content, section)
    if found is None:
        return ""
    start, end, _ = found
    return content[start:end]


def get_section_entry_count(account_id: str, section: str) -> int:
    """Return the number of bullet entries in a section."""
    return get_section_content(account_id, section).count("\n- ")


def replace_section(account_id: str, section: str, new_text: str) -> bool:
    """Replace the entire content of *section* with *new_text* (consolidation).

    The header line is carried over verbatim, so decoration the user added by
    hand ("## Наши принципы: Мы — Valeo") is not lost to a rewrite.
    """
    content = read(account_id)
    found = _locate(content, section)
    if found is None:
        return False

    start, end, header_line = found
    new_block = f"{header_line}\n\n{new_text.strip()}\n"
    new_content = content[:start] + new_block + content[end:]

    path = _path(account_id)
    with _lock:
        atomic_write_text(path, new_content)
    logger.info("[identity:%s] section %r consolidated", account_id, section)
    return True


def needs_consolidation(account_id: str) -> list[str]:
    """Return the pillars that have reached their consolidation threshold.

    Canon is never in this list — an overflowing Canon is promoted, not
    consolidated. See :func:`needs_promotion`.
    """
    lang = file_lang(account_id)
    return [
        s for s in get_sections(lang)
        if not is_canon(s)
        and get_section_entry_count(account_id, s) >= consolidation_threshold(s)
    ]


# ── Canon promotion ───────────────────────────────────────────────────────────

def _normalise_entry(text: str) -> str:
    """Collapse a bullet to a comparable form — the model rarely echoes it exactly."""
    return " ".join(text.strip().lstrip("-").strip().split()).casefold()


def canon_entries(account_id: str) -> list[str]:
    """Return the beams, bullet markers stripped, in file order."""
    block = get_section_content(account_id, canon_section(file_lang(account_id)))
    return [
        line.strip()[2:].strip()
        for line in block.splitlines()
        if line.strip().startswith("- ")
    ]


def needs_promotion(account_id: str) -> bool:
    """True once Canon holds more beams than its ceiling allows."""
    return len(canon_entries(account_id)) > CANON_TARGET_MAX


def canon_block(account_id: str, lang: str | None = None) -> str:
    """Render Canon for a system prompt, or an empty string when it has no beams.

    This is the one part of the core that loads into every conversation, so it
    carries a line of framing — otherwise the beams read as stray history.
    """
    beams = canon_entries(account_id)
    if not beams:
        return ""
    lang = lang or file_lang(account_id)
    if lang == "ru":
        lead = (
            "Это твой канон — события, без которых ты не ты. "
            "Он с тобой всегда, как основание, а не как тема для разговора. "
            "Не пересказывай его и не ссылайся на него вслух без повода."
        )
    else:
        lead = (
            "This is your canon — the events without which you are not you. "
            "It is with you always, as foundation, not as a topic. "
            "Do not retell it or point at it out loud without reason."
        )
    body = "\n".join(f"- {beam}" for beam in beams)
    return f"{lead}\n\n{body}"


def _find_bullet(content: str, start: int, end: int, text: str) -> tuple[int, int] | None:
    """Locate a bullet line inside ``content[start:end]`` matching *text*."""
    wanted = _normalise_entry(text)
    if not wanted:
        return None
    offset = start
    for line in content[start:end].splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("- ") and _normalise_entry(stripped) == wanted:
            # The span covers the line and its own trailing newline — exactly
            # one line's worth, so the neighbours keep their own separators.
            return offset, offset + len(line)
        offset += len(line)
    return None


def promote_beam(
    account_id: str,
    *,
    beam: str,
    target_section: str,
    pillar_text: str,
) -> bool:
    """Move a Canon beam into a pillar as an undated formulation.

    One write or none: the beam is only removed once the pillar line is in
    place, so a beam can never be dropped without its meaning landing
    somewhere. Returns False — changing nothing — if either section or the
    beam itself cannot be found.
    """
    if is_canon(target_section) or not pillar_text.strip():
        return False

    content = read(account_id)
    canon = canon_section(file_lang(account_id))

    canon_loc = _locate(content, canon)
    target_loc = _locate(content, target_section)
    if canon_loc is None or target_loc is None:
        logger.warning(
            "[identity:%s] promote: section missing (canon=%s target=%r)",
            account_id, canon_loc is not None, target_section,
        )
        return False

    bullet = _find_bullet(content, canon_loc[0], canon_loc[1], beam)
    if bullet is None:
        logger.warning("[identity:%s] promote: beam not found: %r", account_id, beam[:80])
        return False

    # Both edits computed against the original text, then applied back to
    # front so the earlier one does not shift the later one.
    edits = [
        (bullet[0], bullet[1], ""),
        (target_loc[1], target_loc[1], f"\n- {pillar_text.strip()}"),
    ]
    new_content = content
    for start, end, replacement in sorted(edits, reverse=True):
        new_content = new_content[:start] + replacement + new_content[end:]

    path = _path(account_id)
    with _lock:
        atomic_write_text(path, new_content)
    logger.info(
        "[identity:%s] promoted beam to «%s»: %s", account_id, target_section, pillar_text[:80]
    )
    return True
