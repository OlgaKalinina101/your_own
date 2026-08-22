"""Tests for the ResearchAgent orchestrator.

Covers the loop every search in the app now runs through:
  1. Satisfied on the first probe — one attempt, no re-query.
  2. Judge refines — the second probe uses the new formulation.
  3. Attempts run out — result is marked exhausted.
  4. Nothing found — the brief falls back to the "empty" prompt section.
  5. Judge repeating a tried query — loop stops instead of spinning.
  6. A single prose probe (the web backend) — no extra summarising call.
  7. Several probes with hits — results are merged into one brief.
  8. Guards — unknown source, blank task, missing api key.
  9. Citations are deduplicated across probes.

No database, no LLM, no network — probes and completions are stubbed.

Run with:
    cd C:\\Users\\User\\PycharmProjects\\your_own
    python -m pytest tests/agents/test_research_agent.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest

from infrastructure.agents import sources
from infrastructure.agents.research import (
    Citation,
    ProbeResult,
    ResearchAgent,
    Source,
)

TEST_SOURCE = "test_source"


# ── Fixtures ──────────────────────────────────────────────────────────────────

class FakeProbe:
    """Records the queries it was asked, returns canned results in order."""

    def __init__(self, results: list[ProbeResult]) -> None:
        self.results = results
        self.queries: list[str] = []

    async def __call__(self, query: str, ctx) -> ProbeResult:
        self.queries.append(query)
        index = min(len(self.queries) - 1, len(self.results) - 1)
        return self.results[index]


def hit(text: str = "found something", **meta) -> ProbeResult:
    return ProbeResult(hits=[{"text": text, "meta": meta}])


def prose(text: str = "a written brief") -> ProbeResult:
    return ProbeResult(hits=[{"text": text, "meta": {}}], is_brief=True)


def miss() -> ProbeResult:
    return ProbeResult(hits=[], citations=[])


@pytest.fixture
def register_probe():
    """Register a fake backend and remove it afterwards."""
    installed: list[str] = []

    def _register(probe) -> str:
        sources.PROBES[TEST_SOURCE] = probe
        installed.append(TEST_SOURCE)
        return TEST_SOURCE

    yield _register

    for key in installed:
        sources.PROBES.pop(key, None)


def make_agent(verdicts: list[str], max_attempts: int = 3) -> tuple[ResearchAgent, list[str]]:
    """Agent whose LLM steps replay *verdicts* and record their prompts."""
    agent = ResearchAgent(api_key="test-key", model="test/model", max_attempts=max_attempts)
    seen: list[str] = []
    replay = list(verdicts)

    async def fake_complete(*, system: str, user: str, max_tokens: int) -> str:
        seen.append(user)
        return replay.pop(0) if replay else "ENOUGH"

    agent._complete = fake_complete  # type: ignore[method-assign]
    return agent, seen


# ── The loop ──────────────────────────────────────────────────────────────────

class TestLoop:
    @pytest.mark.asyncio
    async def test_satisfied_on_first_probe(self, register_probe):
        probe = FakeProbe([hit()])
        register_probe(probe)
        agent, _ = make_agent(["ENOUGH"])

        result = await agent.research(task="what is the weather", source=TEST_SOURCE)

        assert result.attempts == 1
        assert probe.queries == ["what is the weather"]
        assert result.exhausted is False
        assert result.found is True

    @pytest.mark.asyncio
    async def test_judge_refines_the_query(self, register_probe):
        probe = FakeProbe([miss(), hit()])
        register_probe(probe)
        agent, _ = make_agent(["REFINE: yerevan weather forecast", "ENOUGH"])

        result = await agent.research(task="weather", source=TEST_SOURCE)

        assert probe.queries == ["weather", "yerevan weather forecast"]
        assert result.queries == ["weather", "yerevan weather forecast"]
        assert result.attempts == 2
        assert result.exhausted is False

    @pytest.mark.asyncio
    async def test_runs_out_of_attempts(self, register_probe):
        probe = FakeProbe([miss()])
        register_probe(probe)
        agent, _ = make_agent(["REFINE: try two", "REFINE: try three"], max_attempts=3)

        result = await agent.research(task="nothing there", source=TEST_SOURCE)

        assert result.attempts == 3
        assert probe.queries == ["nothing there", "try two", "try three"]
        assert result.exhausted is True
        assert result.found is False

    @pytest.mark.asyncio
    async def test_last_attempt_skips_the_judge(self, register_probe):
        """No attempts left means no point paying for a verdict."""
        register_probe(FakeProbe([prose("already written up")]))
        agent, prompts = make_agent([], max_attempts=1)

        result = await agent.research(task="one shot", source=TEST_SOURCE)

        assert result.attempts == 1
        assert result.exhausted is False
        assert result.brief == "already written up"
        assert prompts == []  # neither judge nor brief ran — zero LLM calls

    @pytest.mark.asyncio
    async def test_judge_repeating_a_tried_query_stops_the_loop(self, register_probe):
        probe = FakeProbe([miss()])
        register_probe(probe)
        agent, _ = make_agent(["REFINE: same words"], max_attempts=4)

        result = await agent.research(task="same words", source=TEST_SOURCE)

        assert probe.queries == ["same words"]
        assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_unparseable_verdict_counts_as_enough(self, register_probe):
        """A flaky judge costs one wasted call, not three."""
        probe = FakeProbe([hit()])
        register_probe(probe)
        agent, _ = make_agent(["mumble mumble"])

        result = await agent.research(task="q", source=TEST_SOURCE)

        assert result.attempts == 1
        assert result.exhausted is False


# ── Brief ─────────────────────────────────────────────────────────────────────

class TestBrief:
    @pytest.mark.asyncio
    async def test_single_prose_probe_is_used_verbatim(self, register_probe):
        """The web backend already writes prose — restating it is a wasted call."""
        register_probe(FakeProbe([prose("Yerevan is 24 degrees today.")]))
        agent, prompts = make_agent(["ENOUGH"])

        result = await agent.research(task="weather", source=TEST_SOURCE)

        assert result.brief == "Yerevan is 24 degrees today."
        assert len(prompts) == 1  # the judge only; no summarising call

    @pytest.mark.asyncio
    async def test_raw_hits_are_summarised(self, register_probe):
        register_probe(FakeProbe([hit("row one")]))
        agent, _ = make_agent(["ENOUGH", "a merged summary"])

        result = await agent.research(task="q", source=TEST_SOURCE)

        assert result.brief == "a merged summary"

    @pytest.mark.asyncio
    async def test_material_from_several_probes_reaches_the_brief(self, register_probe):
        register_probe(FakeProbe([hit("first finding"), hit("second finding")]))
        agent, prompts = make_agent(["REFINE: another angle", "ENOUGH", "merged"])

        result = await agent.research(task="q", source=TEST_SOURCE)

        assert result.brief == "merged"
        assert "first finding" in prompts[-1]
        assert "second finding" in prompts[-1]

    @pytest.mark.asyncio
    async def test_empty_result_falls_back_to_the_empty_section(self, register_probe):
        register_probe(FakeProbe([miss()]))
        agent, _ = make_agent([], max_attempts=1)

        result = await agent.research(task="unfindable thing", source=TEST_SOURCE, lang="en")

        assert "unfindable thing" in result.brief
        assert result.exhausted is True

    @pytest.mark.asyncio
    async def test_brief_falls_back_to_material_when_the_call_fails(self, register_probe):
        register_probe(FakeProbe([hit("raw material")]))
        agent, _ = make_agent(["ENOUGH", ""])

        result = await agent.research(task="q", source=TEST_SOURCE)

        assert "raw material" in result.brief


# ── Citations ─────────────────────────────────────────────────────────────────

class TestCitations:
    @pytest.mark.asyncio
    async def test_deduplicated_across_probes(self, register_probe):
        shared = Citation(title="Same page", url="https://example.com/a")
        first = ProbeResult(hits=[{"text": "one", "meta": {}}], citations=[shared])
        second = ProbeResult(
            hits=[{"text": "two", "meta": {}}],
            citations=[shared, Citation(title="Other", url="https://example.com/b")],
        )
        register_probe(FakeProbe([first, second]))
        agent, _ = make_agent(["REFINE: another angle", "ENOUGH", "merged"])

        result = await agent.research(task="q", source=TEST_SOURCE)

        assert [c.url for c in result.citations] == [
            "https://example.com/a",
            "https://example.com/b",
        ]

    def test_to_dict_shape(self):
        payload = Citation(title="T", url="U", ref="R").to_dict()
        assert payload == {"title": "T", "url": "U", "ref": "R"}


# ── Guards ────────────────────────────────────────────────────────────────────

class TestGuards:
    @pytest.mark.asyncio
    async def test_unknown_source_raises(self):
        agent, _ = make_agent([])
        with pytest.raises(ValueError, match="unknown research source"):
            await agent.research(task="q", source="no_such_backend")

    @pytest.mark.asyncio
    async def test_blank_task_returns_empty(self, register_probe):
        probe = FakeProbe([hit()])
        register_probe(probe)
        agent, _ = make_agent([])

        result = await agent.research(task="   ", source=TEST_SOURCE)

        assert result.attempts == 0
        assert result.exhausted is True
        assert probe.queries == []

    @pytest.mark.asyncio
    async def test_missing_api_key_skips_the_search(self, register_probe, monkeypatch):
        probe = FakeProbe([hit()])
        register_probe(probe)
        agent, _ = make_agent([])
        agent.api_key = ""

        result = await agent.research(task="q", source=TEST_SOURCE)

        assert result.attempts == 0
        assert result.exhausted is True
        assert probe.queries == []


# ── Registry ──────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_web_backend_is_registered(self):
        assert Source.WEB in sources.PROBES

    def test_web_tools_declare_search_and_fetch(self):
        tools = sources._web_tools("parallel")
        types = [t["type"] for t in tools]
        assert types == ["openrouter:web_search", "openrouter:web_fetch"]
        assert tools[0]["parameters"]["engine"] == "parallel"
