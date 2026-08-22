"""Base class and shared types for chat skills."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from logging import Logger

    from sqlalchemy.ext.asyncio import AsyncSession

    from infrastructure.llm.client import LLMClient


# ---------------------------------------------------------------------------
# Runtime context passed into every skill
# ---------------------------------------------------------------------------

@dataclass
class SkillContext:
    db: AsyncSession
    client: LLMClient
    account_id: str
    api_key: str
    lang: str
    recent_pairs: list[dict]
    current_user_text: str
    cutoff_days: int
    logger: Logger
    dbg: Any  # callable(str) -> None


# ---------------------------------------------------------------------------
# Result returned by skill.execute()
# ---------------------------------------------------------------------------

@dataclass
class SkillResult:
    sse_events: list[tuple[str, dict]] = field(default_factory=list)
    continuation: str | None = None
    db_markers: list[str] = field(default_factory=list)
    stream_chunks: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Abstract skill
# ---------------------------------------------------------------------------

class SkillBase(ABC):
    """Every chat skill must subclass this and set the class-level attributes."""

    id: str
    cmd_name: str
    display: dict[str, str]
    description: dict[str, str]
    example: str | None = None

    # How the agentic loop processes this skill:
    #   agentic — execute, inject continuation prompt, re-stream LLM
    #   inline  — execute (e.g. image gen), resume stream (no LLM continuation)
    #   post    — execute after the main stream ends (save_memory, schedule)
    action_type: Literal["agentic", "inline", "post"]

    allow_mid_reply: bool = False
    stream_command_text: bool = True
    persist_in_db: bool = True

    # Compiled regex to detect this command in LLM output.
    parse_re: re.Pattern

    # Directory where this skill's files live (set by each subclass).
    _prompt_dir: Path

    # ------------------------------------------------------------------
    # Regex helpers
    # ------------------------------------------------------------------

    @property
    def open_re_fragment(self) -> str:
        """Fragment contributed to the combined buffering regex."""
        return self.cmd_name.replace("_", "[_ ]")

    # ------------------------------------------------------------------
    # Prompt helpers
    # ------------------------------------------------------------------

    def prompt_fragment(self, lang: str) -> str:
        """Return the skill description block for the assembled system prompt."""
        from infrastructure.llm.prompt_loader import get_prompt

        return get_prompt(
            str(self._prompt_dir / "prompt.md"),
            lang=lang,
            section="description",
        )

    def get_section(self, section: str, lang: str, **kwargs: Any) -> str:
        """Load a named section from this skill's ``prompt.md``."""
        from infrastructure.llm.prompt_loader import get_prompt

        return get_prompt(
            str(self._prompt_dir / "prompt.md"),
            lang=lang,
            section=section,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # SSE events emitted *before* execute()
    # ------------------------------------------------------------------

    def pre_sse_events(self, match: re.Match) -> list[tuple[str, dict]]:
        return []

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    @abstractmethod
    async def execute(self, match: re.Match, ctx: SkillContext) -> SkillResult:
        ...
