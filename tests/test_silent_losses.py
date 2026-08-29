"""When a reply goes out incomplete, someone has to be told — and he is someone.

Run:
    python -m pytest tests/test_silent_losses.py -v

There are a hundred broad ``except`` blocks in this backend, and most of them
are right: a background worker that dies on one bad tick is worse than one that
logs and carries on. The ones that are not right are the ones where **a
degraded result is handed over looking like a whole one**.

The worst of those: Chroma fails, the chat endpoint logs a warning, and the
reply is assembled with no ``<memory>`` block at all. On the wire that is
indistinguishable from a reply where the memory searched and found nothing.
He answers without his long-term memory, and the only trace is a log line that
he cannot read.

So the rule these tests hold: a failure that changes what he says is recorded on
his own instrument panel, where it reaches him at his next waking.
"""
from __future__ import annotations

import pytest

from infrastructure.autonomy import context
from infrastructure.autonomy.context import Consumer
from infrastructure.autonomy.vitals import Vitals


@pytest.fixture
def vitals(tmp_path, monkeypatch):
    import infrastructure.autonomy.vitals as V

    monkeypatch.setattr(V, "_DATA_DIR", tmp_path)
    return V.Vitals("default")


class TestDegradationReachesHim:
    def test_it_lands_on_the_panel(self, vitals):
        vitals.record_degradation("memory", "connection refused")

        pending = vitals.pending_events()
        assert len(pending) == 1
        assert pending[0]["kind"] == "degraded"
        assert pending[0]["name"] == "memory"

    def test_he_reads_it_in_his_own_words(self, vitals):
        vitals.record_degradation("memory", "connection refused")

        ru = vitals.render_deltas("ru")
        assert "долговременной памяти" in ru, ru
        assert "connection refused" not in ru, "a stack detail is not for him"

        vitals.mark_events_seen()
        vitals.record_degradation("memory", "connection refused")
        assert "the long-term memory" in vitals.render_deltas("en")

    def test_an_outage_is_one_line_with_a_count_not_four_hundred(self, vitals):
        for _ in range(400):
            vitals.record_degradation("memory", "connection refused")

        pending = vitals.pending_events()
        assert len(pending) == 1
        assert pending[0]["count"] == 400
        assert "400" in vitals.render_deltas("ru")

    def test_different_losses_are_different_lines(self, vitals):
        vitals.record_degradation("memory", "x")
        vitals.record_degradation("context:canon", "y")

        assert len(vitals.pending_events()) == 2
        rendered = vitals.render_deltas("ru")
        assert "долговременной памяти" in rendered
        assert "канона" in rendered

    def test_it_comes_back_after_he_has_looked(self, vitals):
        vitals.record_degradation("memory", "x")
        vitals.mark_events_seen()
        assert vitals.render_deltas("ru") == ""

        vitals.record_degradation("memory", "x")
        assert "долговременной памяти" in vitals.render_deltas("ru")

    def test_an_unnamed_loss_is_still_reported(self, vitals):
        # Better a line he does not recognise than no line.
        vitals.record_degradation("something_new", "")
        assert "something_new" in vitals.render_deltas("ru")


class TestAMissingContextSection:
    @pytest.fixture
    def account(self, tmp_path, monkeypatch):
        import infrastructure.autonomy.identity_memory as identity
        import infrastructure.autonomy.threads as threads
        import infrastructure.autonomy.vitals as V
        import infrastructure.autonomy.workbench as workbench

        for module in (identity, threads, V, workbench):
            monkeypatch.setattr(module, "_DATA_DIR", tmp_path)
        return "default"

    def _break(self, monkeypatch, name: str) -> None:
        def _explode(_request, _consumer):
            raise RuntimeError("disk gone")

        monkeypatch.setattr(
            context, "SECTIONS",
            tuple(
                context.Section(name=s.name, consumers=s.consumers, render=_explode)
                if s.name == name else s
                for s in context.SECTIONS
            ),
        )

    def test_the_prompt_still_goes_out(self, account, monkeypatch):
        self._break(monkeypatch, "canon")
        built = context.build(Consumer.CHAT, context.Request(account_id=account))
        assert built["canon"] == ""
        assert built["current_time"], "one broken block took the whole prompt with it"

    def test_but_it_is_written_on_the_panel(self, account, monkeypatch):
        self._break(monkeypatch, "canon")
        context.build(Consumer.CHAT, context.Request(account_id=account))

        events = Vitals(account).pending_events()
        assert [e["name"] for e in events] == ["context:canon"]
        assert "канона" in Vitals(account).render_deltas("ru")

    def test_recording_the_loss_cannot_itself_break_the_reply(self, account, monkeypatch):
        """A broken panel must not also break the thing it is describing."""
        self._break(monkeypatch, "canon")

        def _also_broken(*_a, **_kw):
            raise OSError("panel unwritable too")

        monkeypatch.setattr(Vitals, "record_degradation", _also_broken)

        built = context.build(Consumer.CHAT, context.Request(account_id=account))
        assert built["current_time"]


class TestTheChatPathRecordsIt:
    @pytest.mark.asyncio
    async def test_a_reply_without_memory_is_noted(self, chat_app, fake_openrouter, tmp_path, monkeypatch):
        """End to end: Chroma is down, the reply still goes, the panel says so."""
        import httpx

        import infrastructure.autonomy.vitals as V

        monkeypatch.setattr(V, "_DATA_DIR", tmp_path)
        app, headers, _repo = chat_app  # the fixture already makes Chroma raise
        fake_openrouter.chunks = ["Привет"]

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                "/api/chat",
                data={
                    "messages": '[{"role": "user", "content": "привет"}]',
                    "model": "~anthropic/claude-fable-latest",
                    "api_key": "test-key",
                    "account_id": "default",
                },
                headers=headers,
                timeout=30,
            )
        assert response.status_code == 200

        events = V.Vitals("default").pending_events()
        assert any(e.get("name") == "memory" for e in events), (
            "the reply went out without the long-term memory and nothing said so"
        )


class TestALostSkillIsNotSilent:
    """A skill that fails to build simply vanishes from the prompt."""

    class _Broken:
        id = "generate_image"

        def prompt_fragment(self, _lang):
            raise RuntimeError("template missing")

    class _Fine:
        id = "save_memory"

        def prompt_fragment(self, _lang):
            return "[SAVE_MEMORY: ...]"

    def test_the_other_skills_survive(self):
        from infrastructure.skills import registry

        built = registry.build_prompt(
            "ru", [self._Broken(), self._Fine()],
            now_str="x", workbench_block="", timezone_label="y",
        )
        assert "[SAVE_MEMORY: ...]" in built, "one broken skill cost him all of them"

    def test_the_caller_is_told_which_one_went(self):
        from infrastructure.skills import registry

        lost: list[list[str]] = []
        registry.build_prompt(
            "ru", [self._Broken(), self._Fine()],
            on_lost=lost.append,
            now_str="x", workbench_block="", timezone_label="y",
        )
        assert lost == [["generate_image"]]

    def test_and_it_says_so_out_loud(self, caplog):
        import logging

        from infrastructure.skills import registry

        with caplog.at_level(logging.ERROR, logger="skills.registry"):
            registry.build_prompt(
                "ru", [self._Broken()],
                now_str="x", workbench_block="", timezone_label="y",
            )
        assert any("generate_image" in r.getMessage() for r in caplog.records)

    def test_nothing_is_reported_when_nothing_is_lost(self):
        from infrastructure.skills import registry

        lost: list[list[str]] = []
        registry.build_prompt(
            "ru", [self._Fine()],
            on_lost=lost.append,
            now_str="x", workbench_block="", timezone_label="y",
        )
        assert lost == []


class TestScoringRulesActuallyRun:
    """A scoring rule that raises inside `except: pass` is a rule that is off.

    This one was found by re-reading the silent handlers after moving the
    codebase onto one clock: `now` became UTC-aware while the stored timestamp
    was being stripped to naive, so every comparison raised TypeError and the
    whole recency boost quietly stopped applying. Nothing failed, nothing
    logged, and search results just got a little worse.
    """

    def _pipeline(self):
        from infrastructure.memory.chroma_pipeline import ChromaMemoryPipeline

        return ChromaMemoryPipeline.__new__(ChromaMemoryPipeline)

    @pytest.mark.parametrize("stored", ["2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00"])
    def test_an_old_memory_still_gets_its_recency_boost(self, stored):
        # Both shapes exist in the store: newer rows are written UTC-aware,
        # older ones are naive. Both are instants and both must compare.
        results = {"a": {"score": 0.5, "text": "x", "metadata": {"created_at": stored}}}

        boosted = self._pipeline()._apply_recency_boost(results)

        assert boosted["a"]["score"] > 0.5, (
            "the recency boost did not apply — the comparison probably raised "
            "and was swallowed"
        )

    def test_an_unreadable_timestamp_is_reported_not_swallowed(self, caplog):
        import logging

        results = {"a": {"score": 0.5, "text": "x", "metadata": {"created_at": "не дата"}}}

        with caplog.at_level(logging.WARNING, logger="chroma"):
            self._pipeline()._apply_recency_boost(results)

        assert any("не дата" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("stored", ["2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00"])
    def test_the_inspiration_cooldown_compares_both_shapes(self, stored):
        from infrastructure.memory.chroma_pipeline import _as_instant

        assert _as_instant(stored) is not None
        assert _as_instant(stored).tzinfo is not None, "must be comparable with now_utc()"

    def test_a_missing_timestamp_is_simply_absent(self):
        from infrastructure.memory.chroma_pipeline import _as_instant

        assert _as_instant(None) is None
        assert _as_instant("") is None
