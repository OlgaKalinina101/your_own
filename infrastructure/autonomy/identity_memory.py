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
from pathlib import Path
from threading import Lock

logger = logging.getLogger("autonomy.identity")

CONSOLIDATION_THRESHOLD = 10

SECTIONS_RU = ["Кто она", "Кто я", "Наша история", "Наши принципы"]
SECTIONS_EN = ["Who she is", "Who I am", "Our story", "Our principles"]


def _detect_file_lang(content: str) -> str:
    """Auto-detect language from existing identity.md content."""
    if re.search(r"## (?:Кто|Наш)", content):
        return "ru"
    return "en"


def _detect_soul_lang() -> str:
    """Detect language from the soul prompt text."""
    try:
        from infrastructure.settings_store import load_soul
        soul = load_soul()
        if re.search(r"[А-Яа-яЁё]", soul or ""):
            return "ru"
    except Exception:
        pass
    return "en"


def get_sections(lang: str = "ru") -> list[str]:
    return SECTIONS_RU if lang == "ru" else SECTIONS_EN


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "autonomy"
_lock = Lock()


def _template(lang: str = "ru") -> str:
    return "\n".join(f"## {s}\n\n" for s in get_sections(lang))


def _path(account_id: str) -> Path:
    p = _DATA_DIR / account_id
    p.mkdir(parents=True, exist_ok=True)
    return p / "identity.md"


def read(account_id: str) -> str:
    path = _path(account_id)
    if not path.exists():
        lang = _detect_soul_lang()
        path.write_text(_template(lang), encoding="utf-8")
    return path.read_text(encoding="utf-8")


def file_lang(account_id: str) -> str:
    """Return the detected language of the identity file ('ru' or 'en')."""
    return _detect_file_lang(read(account_id))


def append(account_id: str, section: str, text: str) -> bool:
    """Append *text* as a bullet point under *section*.

    Returns True if the section was found, False otherwise.
    """
    content = read(account_id)
    header = f"## {section}"
    if header not in content:
        logger.warning("[identity:%s] section %r not found", account_id, section)
        return False

    # Find insertion point: just before the next ## or end-of-file
    idx = content.index(header)
    next_section = content.find("\n## ", idx + len(header))
    insert_at = next_section if next_section != -1 else len(content)

    bullet = f"\n- {text.strip()}"
    new_content = content[:insert_at] + bullet + content[insert_at:]

    path = _path(account_id)
    with _lock:
        path.write_text(new_content, encoding="utf-8")
    logger.debug("[identity:%s] appended to %r: %s", account_id, section, text[:60])
    return True


def get_section_entry_count(account_id: str, section: str) -> int:
    """Return the number of bullet entries in a section."""
    content = read(account_id)
    header = f"## {section}"
    if header not in content:
        return 0
    idx = content.index(header)
    next_section = content.find("\n## ", idx + len(header))
    block = content[idx:next_section] if next_section != -1 else content[idx:]
    return block.count("\n- ")


def replace_section(account_id: str, section: str, new_text: str) -> bool:
    """Replace the entire content of *section* with *new_text* (consolidation)."""
    content = read(account_id)
    header = f"## {section}"
    if header not in content:
        return False

    idx = content.index(header)
    next_section = content.find("\n## ", idx + len(header))
    end = next_section if next_section != -1 else len(content)

    new_block = f"## {section}\n\n{new_text.strip()}\n"
    new_content = content[:idx] + new_block + content[end:]

    path = _path(account_id)
    with _lock:
        path.write_text(new_content, encoding="utf-8")
    logger.info("[identity:%s] section %r consolidated", account_id, section)
    return True


def needs_consolidation(account_id: str) -> list[str]:
    """Return list of section names that have >= CONSOLIDATION_THRESHOLD entries."""
    lang = file_lang(account_id)
    return [s for s in get_sections(lang) if get_section_entry_count(account_id, s) >= CONSOLIDATION_THRESHOLD]
