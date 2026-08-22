from __future__ import annotations

import re
from pathlib import Path

from infrastructure.skills.base import SkillBase, SkillContext, SkillResult


class SearchDialogueSkill(SkillBase):
    """Search past conversation pairs in Postgres through the research agent.

    Named SEARCH_DIALOGUE to match reflection: one command name, one meaning,
    one storage. SEARCH_MEMORIES stays accepted so replies already in the
    database — and the model's own habit — keep resolving.
    """

    id = "search_dialogue"
    cmd_name = "SEARCH_DIALOGUE"
    display = {"en": "Search Dialogue", "ru": "Поиск по разговорам"}
    description = {
        "en": "AI searches raw conversation history in pgvector for relevant past context.",
        "ru": "AI ищет в истории разговоров через pgvector релевантный контекст.",
    }
    example = "[SEARCH_DIALOGUE: breakup, longing, ex-boyfriend]"
    action_type = "agentic"
    persist_in_db = True
    parse_re = re.compile(
        r"\[SEARCH[_ ](?:DIALOGUE|MEMORIES):\s*(.*?)\]", re.DOTALL | re.IGNORECASE
    )
    _prompt_dir = Path(__file__).resolve().parent

    @property
    def open_re_fragment(self) -> str:
        return "SEARCH[_ ]DIALOGUE|SEARCH[_ ]MEMORIES"

    def pre_sse_events(self, match: re.Match) -> list[tuple[str, dict]]:
        return [("search_start", {"query": match.group(1).strip()})]

    def get_cont_hint(self, lang: str, attempts_left: int) -> str:
        return self.get_section("search_cont_hint", lang, attempts_left=attempts_left)

    async def execute(self, match: re.Match, ctx: SkillContext) -> SkillResult:
        from infrastructure.agents import Source, research

        query = match.group(1).strip()
        result = await research(
            task=query,
            source=Source.DIALOGUE,
            api_key=ctx.api_key,
            account_id=ctx.account_id,
            lang=ctx.lang,
            db=ctx.db,
            top_n=6,
            min_age_days=ctx.cutoff_days,
        )
        ctx.logger.info(
            "[search_dialogue] hits=%d attempts=%d query=%s",
            len(result.raw_hits), result.attempts, query[:120],
        )
        ctx.dbg(
            f"SEARCH_DIALOGUE attempts={result.attempts} "
            f"queries={result.queries} hits={len(result.raw_hits)}"
        )

        found_pairs = [
            {
                "time": hit["meta"].get("time", ""),
                "user": hit["meta"].get("user", ""),
                "assistant": hit["meta"].get("assistant", ""),
            }
            for hit in result.raw_hits
        ]
        sse_events: list[tuple[str, dict]] = [
            ("search_results", {
                "query": query,
                "results": found_pairs,
                "brief": result.brief,
                "attempts": result.attempts,
            }),
        ]

        if result.found:
            continuation = self.get_section(
                "search_continuation",
                ctx.lang,
                brief=result.brief,
                results_block="\n\n".join(h["text"] for h in result.raw_hits),
            )
        else:
            continuation = self.get_section("search_empty", ctx.lang, query=query)

        return SkillResult(sse_events=sse_events, continuation=continuation)


skill = SearchDialogueSkill()
