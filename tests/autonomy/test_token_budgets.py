"""Tests for output budgets and request timeouts on reasoning models.

Written after a reflection came back completely empty: the Claude Fable family has
thinking permanently on, the thinking is billed against ``max_tokens``, and at
2200 the whole allowance went to reasoning. The reply was `''`, which the loop
read as "nothing to say" and logged as a normal sleep.

Measured over the log, once the autonomy stack moved to fable-5:
reflection 25% truncated, post-analysis 38%, rotator 47%.

Pinned here:
  1. Every autonomy step has room for reasoning plus its visible output.
  2. The request timeout scales with the budget, so a bigger budget does not
     just trade truncation for timeouts.
  3. Reflection reports a truncated step instead of calling it silence.

Run with:
    cd C:\\Users\\User\\PycharmProjects\\your_own
    python -m pytest tests/autonomy/test_token_budgets.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest

from infrastructure.llm.client import (
    MAX_COMPLETE_TIMEOUT_S,
    MIN_COMPLETE_TIMEOUT_S,
    _timeout_for,
)

# Room for the thinking plus a few thousand characters of visible output.
# Anthropic's guidance for a non-streaming request is ~16000.
REASONING_FLOOR = 8000


class TestAutonomyBudgets:
    """Every step that runs on the chat model needs reasoning headroom."""

    def test_reflection_step(self):
        from infrastructure.autonomy.reflection_engine import STEP_MAX_TOKENS

        assert STEP_MAX_TOKENS >= REASONING_FLOOR

    def test_post_analysis(self):
        from infrastructure.autonomy.post_analyzer import ANALYSIS_MAX_TOKENS

        assert ANALYSIS_MAX_TOKENS >= REASONING_FLOOR

    def test_rotator_steps(self):
        from infrastructure.autonomy.workbench_rotator import _STEP_MAX_TOKENS

        assert _STEP_MAX_TOKENS >= REASONING_FLOOR

    def test_research_agent_steps(self):
        """The judge and brief hit the same wall on the searcher model."""
        from infrastructure.agents.research import BRIEF_MAX_TOKENS, JUDGE_MAX_TOKENS

        # A one-line verdict, but the reasoning in front of it is billed too.
        assert JUDGE_MAX_TOKENS >= 500
        assert BRIEF_MAX_TOKENS >= 1500

    def test_no_rotator_call_site_passes_a_raw_number(self):
        """A literal budget is how these drifted apart in the first place."""
        import inspect

        from infrastructure.autonomy import workbench_rotator

        source = inspect.getsource(workbench_rotator)
        for literal in ("max_tokens=1500", "max_tokens=2200", "max_tokens=2500"):
            assert literal not in source, f"{literal} is too small for a reasoning model"


class TestTimeoutScaling:
    def test_small_calls_keep_the_old_floor(self):
        assert _timeout_for(650) == MIN_COMPLETE_TIMEOUT_S
        assert _timeout_for(1200) == MIN_COMPLETE_TIMEOUT_S

    def test_a_large_budget_gets_minutes(self):
        # The reflection step that came back empty had already burned 39s.
        assert _timeout_for(16000) > 300

    def test_it_is_capped(self):
        assert _timeout_for(128000) == MAX_COMPLETE_TIMEOUT_S

    def test_it_never_shrinks_as_the_budget_grows(self):
        budgets = [256, 650, 2200, 8000, 16000, 64000, 128000]
        timeouts = [_timeout_for(b) for b in budgets]
        assert timeouts == sorted(timeouts)

    @pytest.mark.parametrize("budget", [0, 1, 256])
    def test_tiny_budgets_are_safe(self, budget):
        assert _timeout_for(budget) == MIN_COMPLETE_TIMEOUT_S


class TestReflectionReportsTruncation:
    """A step that ran out of budget must not read as a quiet reflection."""

    @pytest.mark.asyncio
    async def test_complete_reports_truncation(self, monkeypatch):
        from infrastructure.autonomy import reflection_engine as engine

        class FakeClient:
            async def complete(self, messages, max_tokens, temperature, return_meta):
                assert return_meta is True
                assert max_tokens >= REASONING_FLOOR
                return "", "length"

        monkeypatch.setattr(engine, "make_llm_client", lambda api_key: FakeClient())

        text, truncated = await engine._complete("key", [{"role": "user", "content": "q"}])

        assert text == ""
        assert truncated is True

    @pytest.mark.asyncio
    async def test_complete_reports_a_normal_reply(self, monkeypatch):
        from infrastructure.autonomy import reflection_engine as engine

        class FakeClient:
            async def complete(self, messages, max_tokens, temperature, return_meta):
                return "мысль", "end_turn"

        monkeypatch.setattr(engine, "make_llm_client", lambda api_key: FakeClient())

        text, truncated = await engine._complete("key", [{"role": "user", "content": "q"}])

        assert text == "мысль"
        assert truncated is False
