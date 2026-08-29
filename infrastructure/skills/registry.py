"""Skill registry — auto-discovers skills and provides helpers for chat.py."""
from __future__ import annotations

from infrastructure.account import ACCOUNT_ID
import importlib
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .base import SkillBase

logger = logging.getLogger("skills.registry")

_SKILLS_DIR = Path(__file__).resolve().parent
_all_skills: list[SkillBase] | None = None


def _discover() -> list[SkillBase]:
    global _all_skills
    if _all_skills is not None:
        return _all_skills

    skills: list[SkillBase] = []
    for child in sorted(_SKILLS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        if not (child / "skill.py").exists():
            continue
        mod = importlib.import_module(f"infrastructure.skills.{child.name}.skill")
        if hasattr(mod, "skill"):
            skills.append(mod.skill)
    _all_skills = skills
    return skills


def reload() -> None:
    """Force re-discovery (useful after tests modify the skill set)."""
    global _all_skills
    _all_skills = None


# ------------------------------------------------------------------
# Querying
# ------------------------------------------------------------------

def get_all() -> list[SkillBase]:
    return list(_discover())


def get_enabled(account_id: str = ACCOUNT_ID) -> list[SkillBase]:
    from infrastructure.settings_store import load_settings

    settings = load_settings()
    enabled_ids = settings.get("enabled_skills")
    if enabled_ids is None:
        return get_all()
    return [s for s in _discover() if s.id in enabled_ids]


def get_skill(skill_id: str) -> SkillBase | None:
    for s in _discover():
        if s.id == skill_id:
            return s
    return None


# ------------------------------------------------------------------
# Prompt assembly
# ------------------------------------------------------------------

def build_prompt(
    lang: str,
    skills: list[SkillBase] | None = None,
    *,
    on_lost: Callable[[list[str]], None] | None = None,
    **kwargs,
) -> str:
    """Assemble the full skills system-prompt block.

    ``kwargs`` are forwarded to the header template (``now_str``, ``workbench_block``).
    ``on_lost`` is called with the ids of any skills that failed to build, so the
    caller can record that he went into the turn without them.
    """
    from infrastructure.llm.prompt_loader import get_prompt

    if skills is None:
        skills = get_all()

    header = get_prompt(str(_SKILLS_DIR / "_prompt_header.md"), lang=lang, **kwargs)

    # A skill whose fragment does not build is a skill he no longer has: the
    # command is simply absent from the prompt, and the reply that follows looks
    # exactly like one where he chose not to use it. Dropping it is still the
    # right call — one broken skill must not cost him all the others — but it is
    # said out loud, and the names are handed back so the caller can put them on
    # his instrument panel.
    fragments: list[str] = []
    lost: list[str] = []
    for skill in skills:
        try:
            fragments.append(skill.prompt_fragment(lang))
        except Exception as exc:
            lost.append(skill.id)
            logger.error(
                "[skills] %r did not build its prompt fragment — he cannot use it "
                "this turn: %s", skill.id, exc,
            )
    if lost and on_lost is not None:
        on_lost(lost)

    footer = get_prompt(str(_SKILLS_DIR / "_prompt_footer.md"), lang=lang, section="note")

    return "\n\n".join([header, *fragments, footer]) + "\n</skills>"


def get_trailing_hint(lang: str) -> str:
    from infrastructure.llm.prompt_loader import get_prompt

    return get_prompt(str(_SKILLS_DIR / "_prompt_footer.md"), lang=lang, section="trailing_hint")


# ------------------------------------------------------------------
# Regex builders
# ------------------------------------------------------------------

def build_open_re(skills: list[SkillBase] | None = None) -> re.Pattern:
    """Combined regex that triggers SSE buffering when any command opens."""
    if skills is None:
        skills = get_all()
    fragments = [s.open_re_fragment for s in skills]
    return re.compile(r"\[(" + "|".join(fragments) + r"):", re.IGNORECASE)


def build_internal_markers_re(skills: list[SkillBase] | None = None) -> re.Pattern:
    """Regex to strip all skill markers from LLM context history."""
    if skills is None:
        skills = get_all()
    fragments = [s.open_re_fragment for s in skills]
    extra = ["GENERATED[_ ]IMAGE", "SAVED[_ ]FACT"]
    return re.compile(r"\[(?:" + "|".join(fragments + extra) + r"):[^\]]*\]", re.IGNORECASE)


def build_cleanup_re(skills: list[SkillBase] | None = None) -> re.Pattern:
    """Regex to strip raw commands that should NOT be persisted in the DB.

    Only strips commands where ``persist_in_db`` is False.
    """
    if skills is None:
        skills = get_all()
    fragments = [s.open_re_fragment for s in skills if not s.persist_in_db]
    if not fragments:
        return re.compile(r"(?!)")  # never matches
    return re.compile(r"\[(?:" + "|".join(fragments) + r"):\s*.*?\]", re.DOTALL | re.IGNORECASE)


# ------------------------------------------------------------------
# Parsing
# ------------------------------------------------------------------

def parse_all(
    text: str, skills: list[SkillBase] | None = None,
) -> list[tuple[SkillBase, re.Match]]:
    """Find all skill commands in *text*, ordered by position."""
    if skills is None:
        skills = get_all()
    hits: list[tuple[SkillBase, re.Match]] = []
    for skill in skills:
        for m in skill.parse_re.finditer(text):
            hits.append((skill, m))
    hits.sort(key=lambda x: x[1].start())
    return hits


def strip_skills(
    text: str, skills: list[SkillBase] | None = None,
) -> tuple[str, list[tuple[SkillBase, re.Match]]]:
    """Return ``(clean_text_before_first_command, ordered_matches)``."""
    matches = parse_all(text, skills)
    clean = text[: matches[0][1].start()].rstrip() if matches else text
    return clean, matches
