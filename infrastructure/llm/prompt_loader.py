"""Prompt loader — reads .md prompt files with ## RU / ## EN sections.

File format:
    ## RU
    ### system
    Системный промпт...

    ### user
    Пользовательский промпт с {placeholders}...

    ## EN
    ### system
    System prompt...

    ### user
    User prompt with {placeholders}...

Usage:
    from infrastructure.llm.prompt_loader import get_prompt

    system = get_prompt("infrastructure/autonomy/prompts/post_analyzer.md",
                        section="system", lang="ru")
    user   = get_prompt("infrastructure/autonomy/prompts/post_analyzer.md",
                        section="user", lang="ru", ai_name="Victor", ...)

If a file has no ### system / ### user subsections (only ## RU / ## EN),
the entire lang section is returned.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path



from infrastructure.paths import PROJECT_ROOT
@lru_cache(maxsize=64)
def _load_raw(path: str | Path) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.read_text(encoding="utf-8")


def _extract_section(raw: str, lang: str) -> str:
    """Return the block between ## {lang.upper()} and the next ## heading."""
    tag = lang.upper()
    pattern = re.compile(
        rf"^##\s+{re.escape(tag)}\s*\n(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(raw)
    if not m:
        raise KeyError(f"Prompt section '## {tag}' not found")
    return m.group(1)


def _extract_subsection(block: str, subsection: str) -> str:
    """Return the block between ### {subsection} and the next ### heading."""
    pattern = re.compile(
        rf"^###\s+{re.escape(subsection)}\s*\n(.*?)(?=^###\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(block)
    if not m:
        raise KeyError(f"Prompt subsection '### {subsection}' not found")
    return m.group(1)


def load_prompt(path: str | Path, lang: str = "ru", section: str | None = None) -> str:
    """Load a prompt string from a .md file.

    Args:
        path:    Path to the .md file (absolute or relative to project root).
        lang:    Language section to use — 'ru' or 'en'.
        section: Optional subsection name — 'system' or 'user'.
                 If omitted, returns the entire lang block.

    Returns:
        Raw prompt string (not yet formatted with variables).
    """
    raw = _load_raw(path)
    block = _extract_section(raw, lang)
    if section is not None:
        block = _extract_subsection(block, section)
    return block.strip()


def get_prompt(
    path: str | Path,
    lang: str = "ru",
    section: str | None = None,
    **kwargs,
) -> str:
    """Load a prompt and format it with keyword arguments.

    Equivalent to load_prompt(...).format(**kwargs).
    Raises KeyError / ValueError on missing section or placeholder.
    """
    template = load_prompt(path, lang=lang, section=section)
    if kwargs:
        return template.format(**kwargs)
    return template
