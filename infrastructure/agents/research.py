"""ResearchAgent — the one orchestrator every search in the app goes through.

Chat and reflection do not call a search backend. They hand the agent a
task; the agent probes a source, judges whether what came back answers the
task, re-queries with a different formulation when it does not, and returns
a brief plus the raw hits.

    result = await research(task="weather in Yerevan today", source=Source.WEB,
                            api_key=key, lang="ru")
    result.brief      # prose summary for the calling model
    result.citations  # sources it grounded on
    result.raw_hits   # material for UI cards / SSE

Backends live in :mod:`infrastructure.agents.sources`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from infrastructure.llm.prompt_loader import get_prompt

logger = logging.getLogger("agents.research")

_PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "research_agent.md"

DEFAULT_MAX_ATTEMPTS = 3
_REFINE_RE = re.compile(r"REFINE\s*:\s*(.+)", re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class Source:
    """Search backends. Plain string constants so they serialise for free."""

    WEB = "web"
    DIALOGUE = "dialogue"   # Postgres + pgvector, raw conversation pairs
    FACTS = "facts"         # Chroma key_info, long-term facts
    NOTES = "notes"         # workbench + Chroma workbench_archive


@dataclass
class Citation:
    """One source the brief rests on.

    ``url`` is set for web results; database sources use ``ref`` (pair id,
    fact id, timestamp) and leave the url empty.
    """

    title: str
    url: str = ""
    ref: str = ""

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "ref": self.ref}


@dataclass
class ProbeResult:
    """What one pass against a backend returned."""

    hits: list[dict] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    # True when the backend already returns narrative prose (the web agent
    # does) — then the summarising call can be skipped for a single probe.
    is_brief: bool = False


@dataclass
class ResearchContext:
    """Everything a probe needs. Mirrors the SkillContext idiom."""

    account_id: str
    lang: str
    api_key: str
    model: str
    web_engine: str
    now_str: str
    db: Any = None                       # AsyncSession, for the Postgres source
    extras: dict = field(default_factory=dict)

    def prompt(self, section: str, **kwargs: Any) -> str:
        return get_prompt(str(_PROMPT_FILE), lang=self.lang, section=section, **kwargs)


@dataclass
class ResearchResult:
    brief: str
    citations: list[Citation] = field(default_factory=list)
    raw_hits: list[dict] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    attempts: int = 0
    # True when the loop ran out of attempts without a usable result.
    exhausted: bool = False

    @property
    def found(self) -> bool:
        return bool(self.raw_hits)

    def to_dict(self) -> dict:
        return {
            "brief": self.brief,
            "citations": [c.to_dict() for c in self.citations],
            "queries": self.queries,
            "attempts": self.attempts,
            "exhausted": self.exhausted,
        }


# ---------------------------------------------------------------------------
# The orchestrator
# ---------------------------------------------------------------------------

class ResearchAgent:
    """Probe -> judge -> re-query -> brief, over any registered source."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        web_engine: str | None = None,
        max_attempts: int | None = None,
    ) -> None:
        from infrastructure.settings_store import load_settings

        srv = load_settings()
        self.api_key = api_key or str(srv.get("openrouter_api_key", ""))
        self.model = model or str(srv.get("research_model", "google/gemini-3.5-flash"))
        self.web_engine = web_engine or str(srv.get("research_web_engine", "parallel"))
        self.max_attempts = max_attempts or int(
            srv.get("research_max_attempts", DEFAULT_MAX_ATTEMPTS)
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def research(
        self,
        *,
        task: str,
        source: str,
        account_id: str = "default",
        lang: str = "ru",
        db: Any = None,
        **extras: Any,
    ) -> ResearchResult:
        from infrastructure.agents import sources as _sources
        from infrastructure.settings_store import now_local_str

        task = (task or "").strip()
        probe = _sources.PROBES.get(source)
        if probe is None:
            raise ValueError(f"unknown research source: {source!r}")
        if not task:
            return ResearchResult(brief="", queries=[], attempts=0, exhausted=True)
        if not self.api_key:
            logger.warning("[research] no api_key - %s search skipped", source)
            return ResearchResult(brief="", queries=[task], attempts=0, exhausted=True)

        ctx = ResearchContext(
            account_id=account_id,
            lang=lang,
            api_key=self.api_key,
            model=self.model,
            web_engine=self.web_engine,
            now_str=now_local_str(),
            db=db,
            extras=extras,
        )

        query = task
        queries: list[str] = []
        results: list[ProbeResult] = []
        satisfied = False

        for attempt in range(1, self.max_attempts + 1):
            queries.append(query)
            logger.info(
                "[research] source=%s attempt=%d/%d query=%s",
                source, attempt, self.max_attempts, query[:120],
            )
            result = await probe(query, ctx)
            results.append(result)

            if attempt == self.max_attempts:
                # No attempts left - keep whatever we have.
                satisfied = bool(result.hits)
                break

            requery = await self._judge(task, queries, result, ctx)
            if requery is None:
                satisfied = True
                break
            query = requery

        hits = [h for r in results for h in r.hits]
        citations = self._dedup_citations(results)
        brief = await self._make_brief(task, results, ctx)

        logger.info(
            "[research] source=%s done attempts=%d hits=%d citations=%d satisfied=%s",
            source, len(queries), len(hits), len(citations), satisfied,
        )
        return ResearchResult(
            brief=brief,
            citations=citations,
            raw_hits=hits,
            queries=queries,
            attempts=len(queries),
            exhausted=not satisfied,
        )

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    async def _judge(
        self,
        task: str,
        queries: list[str],
        result: ProbeResult,
        ctx: ResearchContext,
    ) -> str | None:
        """Return a better query, or ``None`` when the result is good enough.

        Fails open: an unparseable or failed verdict counts as good enough,
        so a flaky judge costs one wasted call rather than three.
        """
        if not result.hits:
            # Nothing at all - the judge still sees the miss so it can pick
            # a different angle instead of repeating the same words.
            found = ctx.prompt("empty", task=task)
        else:
            found = "\n\n".join(str(h.get("text", "")) for h in result.hits)[:4000]

        verdict = await self._complete(
            system=ctx.prompt("judge_system"),
            user=ctx.prompt(
                "judge_user",
                task=task,
                tried=" | ".join(queries),
                found=found,
            ),
            max_tokens=200,
        )
        if not verdict:
            return None

        match = _REFINE_RE.search(verdict)
        if not match:
            return None

        requery = match.group(1).strip().split("\n")[0].strip().strip('"')
        if not requery:
            return None
        # A judge that suggests something already tried would loop forever.
        if requery.lower() in {q.lower() for q in queries}:
            logger.info("[research] judge repeated a tried query - stopping")
            return None
        logger.info("[research] refine -> %s", requery[:120])
        return requery

    async def _make_brief(
        self,
        task: str,
        results: list[ProbeResult],
        ctx: ResearchContext,
    ) -> str:
        usable = [r for r in results if r.hits]
        if not usable:
            return ctx.prompt("empty", task=task)

        # The web backend already returns prose - one good pass needs no
        # second call to restate it.
        if len(usable) == 1 and usable[0].is_brief:
            return str(usable[0].hits[0].get("text", "")).strip()

        material = "\n\n---\n\n".join(
            str(h.get("text", "")) for r in usable for h in r.hits
        )[:12000]
        brief = await self._complete(
            system=ctx.prompt("brief_system"),
            user=ctx.prompt("brief_user", task=task, material=material),
            max_tokens=900,
        )
        return brief or material[:2000]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _complete(self, *, system: str, user: str, max_tokens: int) -> str:
        from infrastructure.llm.client import LLMClient

        client = LLMClient(api_key=self.api_key, model=self.model, temperature=0.2)
        return await client.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.2,
        )

    @staticmethod
    def _dedup_citations(results: list[ProbeResult]) -> list[Citation]:
        out: list[Citation] = []
        seen: set[str] = set()
        for result in results:
            for citation in result.citations:
                key = citation.url or citation.ref or citation.title
                if key and key not in seen:
                    seen.add(key)
                    out.append(citation)
        return out


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

async def research(
    *,
    task: str,
    source: str,
    api_key: str,
    account_id: str = "default",
    lang: str = "ru",
    db: Any = None,
    max_attempts: int | None = None,
    **extras: Any,
) -> ResearchResult:
    """Run one research pass. See :class:`ResearchAgent`.

    ``max_attempts=1`` turns the loop off for lookups that cannot be
    reformulated (a dialogue search by date, say).
    """
    agent = ResearchAgent(api_key=api_key, max_attempts=max_attempts)
    return await agent.research(
        task=task, source=source, account_id=account_id, lang=lang, db=db, **extras
    )
