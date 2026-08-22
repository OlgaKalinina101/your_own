from __future__ import annotations

import re
from pathlib import Path

from infrastructure.skills.base import SkillBase, SkillContext, SkillResult


class WebSearchSkill(SkillBase):
    id = "web_search"
    cmd_name = "WEB_SEARCH"
    display = {"en": "Web Search", "ru": "Поиск в интернете"}
    description = {
        "en": "AI searches the live web for fresh external information.",
        "ru": "AI ищет актуальную информацию в интернете.",
    }
    example = "[WEB_SEARCH: weather Yerevan today]"
    action_type = "agentic"
    persist_in_db = True
    parse_re = re.compile(r"\[WEB[_ ]SEARCH:\s*(.*?)\]", re.DOTALL | re.IGNORECASE)
    _prompt_dir = Path(__file__).resolve().parent

    def pre_sse_events(self, match: re.Match) -> list[tuple[str, dict]]:
        return [("web_start", {"query": match.group(1).strip()})]

    async def execute(self, match: re.Match, ctx: SkillContext) -> SkillResult:
        from infrastructure.agents import Source, research

        query = match.group(1).strip()
        ctx.logger.info("[web_search] query=%s", query[:120])
        ctx.dbg(f"WEB_SEARCH query={query[:120]}")

        result = await research(
            task=query,
            source=Source.WEB,
            api_key=ctx.api_key,
            account_id=ctx.account_id,
            lang=ctx.lang,
        )
        ctx.dbg(
            f"WEB_SEARCH done attempts={result.attempts} "
            f"citations={len(result.citations)} found={result.found}"
        )

        sources = [c.to_dict() for c in result.citations]
        sse_events: list[tuple[str, dict]] = [
            ("web_results", {
                "query": query,
                "brief": result.brief,
                "sources": sources,
                "attempts": result.attempts,
            }),
            ("web_done", {"query": query}),
        ]

        if result.found:
            continuation = self.get_section(
                "web_continuation",
                ctx.lang,
                web_query=query,
                brief=result.brief,
                sources_block=_render_sources(sources),
            )
        else:
            continuation = self.get_section("web_empty", ctx.lang, web_query=query)

        return SkillResult(sse_events=sse_events, continuation=continuation)


def _render_sources(sources: list[dict]) -> str:
    """One source per line, or an empty string when the agent cited nothing."""
    lines = [
        f"- {s['title']} ({s['url']})" if s.get("url") else f"- {s['title']}"
        for s in sources
    ]
    return "\n".join(lines)


skill = WebSearchSkill()
