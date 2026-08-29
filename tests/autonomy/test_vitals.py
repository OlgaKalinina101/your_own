"""Tests for Vitals — the instrument panel and the guard around a failed waking.

Written after a night went missing: one bad completion was read as "nothing to
say", the slot was spent, and nobody — him or us — found out until someone
noticed the canon had not moved.

Pinned here:
  1. A failed waking schedules a retry instead of costing the whole interval,
     and the retries are capped so a lasting outage stops looping.
  2. One notice per failure episode, not one per retry.
  3. Deltas are shown once, then marked seen — an old failure does not haunt
     every future waking.
  4. Facts only: no verdict language, and nothing at all when nothing changed.
  5. Gaps in the heartbeat are detected — from inside, that is the only
     evidence the system was not running.

Run with:
    cd C:\\Users\\User\\PycharmProjects\\your_own
    python -m pytest tests/autonomy/test_vitals.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest

from infrastructure.autonomy import vitals as V

ACCOUNT = "test_account"


@pytest.fixture
def vitals(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "_DATA_DIR", tmp_path)
    return V.Vitals(ACCOUNT)


def backdate_last_seen(vitals: V.Vitals, **delta) -> None:
    data = vitals._read()
    data["last_seen"] = (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()
    vitals._write(data)


# ── Retry policy ──────────────────────────────────────────────────────────────

class TestRetryPolicy:
    def test_a_failure_schedules_a_retry(self, vitals):
        vitals.record_reflection_failure("empty_response_truncated")
        assert vitals.reflection_state()["retry_at"] is not None

    def test_the_retry_is_not_due_immediately(self, vitals):
        vitals.record_reflection_failure("empty_response_truncated")
        assert vitals.retry_due() is False

    def test_the_retry_comes_due(self, vitals):
        vitals.record_reflection_failure("empty_response_truncated")
        state = vitals._read()
        state["reflection"]["retry_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat()
        vitals._write(state)
        assert vitals.retry_due() is True

    def test_retries_are_capped(self, vitals):
        """A lasting outage must fall back to the normal schedule, not loop."""
        for _ in range(V.MAX_CONSECUTIVE_RETRIES):
            vitals.record_reflection_failure("exception")
            assert vitals.reflection_state()["retry_at"] is not None

        count = vitals.record_reflection_failure("exception")
        assert count == V.MAX_CONSECUTIVE_RETRIES + 1
        assert vitals.reflection_state()["retry_at"] is None
        assert vitals.retry_due() is False

    def test_success_clears_the_retry(self, vitals):
        vitals.record_reflection_failure("exception")
        vitals.record_reflection_success(steps=5)

        state = vitals.reflection_state()
        assert state["consecutive_failures"] == 0
        assert state["retry_at"] is None
        assert vitals.retry_due() is False

    def test_success_does_not_erase_the_last_failure(self, vitals):
        vitals.record_reflection_failure("empty_response_truncated")
        vitals.record_reflection_success(steps=5)

        state = vitals.reflection_state()
        assert state["last_failure"] is not None
        assert state["last_failure_reason"] == "empty_response_truncated"

    def test_no_retry_without_a_failure(self, vitals):
        assert vitals.retry_due() is False


# ── Events ────────────────────────────────────────────────────────────────────

class TestEvents:
    def test_one_notice_per_episode(self, vitals):
        for _ in range(4):
            vitals.record_reflection_failure("empty_response_truncated")
        assert len(vitals.pending_events()) == 1

    def test_a_new_episode_notices_again(self, vitals):
        vitals.record_reflection_failure("exception")
        vitals.record_reflection_success(steps=3)
        vitals.mark_events_seen()

        vitals.record_reflection_failure("exception")
        assert len(vitals.pending_events()) == 1

    def test_seen_events_do_not_come_back(self, vitals):
        vitals.record_reflection_failure("exception")
        assert vitals.render_deltas("ru")

        vitals.mark_events_seen()
        assert vitals.render_deltas("ru") == ""


# ── Deltas ────────────────────────────────────────────────────────────────────

class TestDeltas:
    def test_nothing_changed_renders_nothing(self, vitals):
        """Not "all systems normal" — nothing at all, so the prompt stays clean."""
        assert vitals.render_deltas("ru") == ""

    def test_a_failed_waking_is_reported_as_a_fact(self, vitals):
        vitals.record_reflection_failure("empty_response_truncated")
        text = vitals.render_deltas("ru")

        assert "не состоялось" in text
        assert "размышление" in text  # the reason, spelled out

    def test_no_verdicts(self, vitals):
        """The panel measures; reading the numbers is his job."""
        vitals.record_reflection_failure("empty_response_truncated")
        text = vitals.render_deltas("ru").lower()

        for verdict in ("потерял", "наверстай", "всё хорошо", "не волнуйся", "нормально"):
            assert verdict not in text

    def test_english(self, vitals):
        vitals.record_reflection_failure("empty_response_truncated")
        text = vitals.render_deltas("en")
        assert "did not happen" in text


# ── Heartbeat ─────────────────────────────────────────────────────────────────

class TestHeartbeat:
    def test_first_tick_is_not_a_gap(self, vitals):
        assert vitals.heartbeat() is None

    def test_a_regular_tick_is_not_a_gap(self, vitals):
        vitals.heartbeat()
        assert vitals.heartbeat() is None

    def test_a_long_silence_is_a_gap(self, vitals):
        vitals.heartbeat()
        backdate_last_seen(vitals, hours=11)

        gap = vitals.heartbeat()

        assert gap is not None
        assert 650 <= gap.minutes <= 670
        assert len(vitals.gaps()) == 1

    def test_a_gap_becomes_a_delta(self, vitals):
        vitals.heartbeat()
        backdate_last_seen(vitals, hours=11)
        vitals.heartbeat()

        text = vitals.render_deltas("ru")
        assert "не работала" in text
        assert "11 ч" in text

    def test_a_short_blip_is_not_a_gap(self, vitals):
        vitals.heartbeat()
        backdate_last_seen(vitals, minutes=V.HEARTBEAT_GAP_MINUTES - 1)
        assert vitals.heartbeat() is None

    def test_gaps_are_capped(self, vitals):
        vitals.heartbeat()
        for _ in range(V.MAX_GAPS_KEPT + 5):
            backdate_last_seen(vitals, hours=1)
            vitals.heartbeat()
        assert len(vitals.gaps()) == V.MAX_GAPS_KEPT


# ── The full panel ────────────────────────────────────────────────────────────

class TestFullPanel:
    def test_it_renders_on_a_blank_slate(self, vitals):
        text = vitals.render_full("ru")
        assert "Пробуждения:" in text
        assert "Система:" in text

    def test_it_reports_the_numbers(self, vitals):
        vitals.heartbeat()
        vitals.record_reflection_success(steps=7)

        text = vitals.render_full("ru")
        assert "шагов в нём: 7" in text
        assert "сбоев подряд: 0" in text

    def test_live_sections_are_appended(self, vitals):
        text = vitals.render_full("ru", live={"Память": {"сообщений": 56185}})
        assert "Память:" in text
        assert "56185" in text

    def test_english(self, vitals):
        vitals.record_reflection_success(steps=2)
        text = vitals.render_full("en")
        assert "Wakings:" in text
        assert "System:" in text


# ── Robustness ────────────────────────────────────────────────────────────────

class TestRobustness:
    def test_a_corrupt_file_does_not_raise(self, vitals):
        vitals.path.write_text("{ this is not json", encoding="utf-8")

        assert vitals.reflection_state() == {}
        assert vitals.render_deltas("ru") == ""
        vitals.record_reflection_failure("exception")  # recovers by rewriting
        assert vitals.reflection_state()["consecutive_failures"] == 1

    def test_missing_file_reads_as_empty(self, vitals):
        assert vitals.pending_events() == []
        assert vitals.gaps() == []


# ── Wiring ────────────────────────────────────────────────────────────────────

class TestWiring:
    def test_vitals_is_a_known_command(self):
        from infrastructure.autonomy.commands import (
            LEAKABLE_COMMANDS,
            REFLECTION_COMMANDS,
        )
        from infrastructure.autonomy.reflection_engine import _VITALS_RE

        assert "VITALS" in REFLECTION_COMMANDS
        assert "VITALS" in LEAKABLE_COMMANDS
        assert _VITALS_RE.search("[VITALS]")
        assert _VITALS_RE.search("Посмотрю на себя. [VITALS]")

    def test_an_unclosed_command_cannot_swallow_vitals(self):
        from infrastructure.autonomy.reflection_engine import _CMD_RE

        text = "[UPDATE_THREAD: #cd52 | оборвалось\n[VITALS]"
        assert list(_CMD_RE.finditer(text)) == []

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_prompt_advertises_it(self, lang):
        from infrastructure.llm.prompt_loader import get_prompt

        prompt = get_prompt(
            "infrastructure/autonomy/prompts/reflection_awakening.md",
            lang=lang,
            ai_name="Victor", identity="", workbench="",
            open_threads="", recent_dialogue="", current_time="2026-08-29 12:00",
            hours_since_last="3.0 h", pending_tasks_block="", vitals="",
            cooldown_h=4, interval_h=12, timezone_label="Asia/Yerevan",
        )
        assert "[VITALS]" in prompt

    def test_the_deltas_block_has_a_slot_in_the_prompt(self):
        from infrastructure.llm.prompt_loader import load_prompt

        for lang in ("ru", "en"):
            assert "{vitals}" in load_prompt(
                "infrastructure/autonomy/prompts/reflection_awakening.md", lang=lang
            )


# ── Live probes ───────────────────────────────────────────────────────────────

class TestTheProbes:
    """Four things he cannot see from the inside.

    Every one of them reports "could not check" instead of falling silent. An
    absent line reads as "fine", and a panel that goes quiet when a measurement
    fails is worse than no panel — that is finding R9 in another costume.
    """

    def test_the_disk_is_reported(self):
        from infrastructure.autonomy.vitals import probe_disk

        line = " ".join(probe_disk().values())

        assert "ГБ" in line and "из" in line

    def test_a_disk_that_cannot_be_read_says_so(self, monkeypatch):
        import shutil

        from infrastructure.autonomy.vitals import probe_disk

        def _broken(_path):
            raise OSError("no such device")

        monkeypatch.setattr(shutil, "disk_usage", _broken)

        assert "не удалось" in " ".join(probe_disk().values())

    @pytest.mark.parametrize(
        "state, detail, expected",
        [
            ("loaded", "mini-lm", "загружена"),
            ("failed", "no torch", "не загрузилась"),
            ("not_loaded", "mini-lm", "не загружалась"),
        ],
    )
    def test_the_memory_model_states_are_distinguished(
        self, monkeypatch, state, detail, expected
    ):
        # "not loaded" and "cannot load" look the same from outside and mean
        # opposite things: idle, versus his recall quietly running on keywords.
        import infrastructure.memory.embedder as embedder

        from infrastructure.autonomy.vitals import probe_embedder

        monkeypatch.setattr(embedder, "status", lambda: (state, detail))

        assert expected in " ".join(probe_embedder().values())

    @pytest.mark.asyncio
    async def test_no_key_is_not_the_same_as_no_money(self):
        from infrastructure.autonomy.vitals import probe_key

        assert "не настроен" in " ".join((await probe_key("")).values())

    @pytest.mark.asyncio
    async def test_the_balance_is_shown_in_money(self, monkeypatch):
        from infrastructure.autonomy import vitals

        async def _state(_key):
            return {"remaining": 23.234, "daily": 1.32, "weekly": 30.36, "monthly": 154.47}

        monkeypatch.setattr(
            "infrastructure.llm.client.fetch_account_state", _state, raising=False
        )

        out = await vitals.probe_key("sk-test")

        assert out["остаток"] == "$23.23"
        assert out["за сутки"] == "$1.32"
        # No projection: how long that lasts is arithmetic he can do himself,
        # and the guess behind it is not the instrument's to make.
        assert not any("дн" in k for k in out)

    @pytest.mark.asyncio
    async def test_an_unreachable_provider_does_not_blank_the_line(self, monkeypatch):
        from infrastructure.autonomy import vitals

        async def _down(_key):
            raise OSError("connection refused")

        monkeypatch.setattr(
            "infrastructure.llm.client.fetch_account_state", _down, raising=False
        )

        assert "не удалось" in " ".join((await vitals.probe_key("sk-test")).values())

    def test_spending_counts_what_he_has_been_doing(self, monkeypatch):
        from infrastructure.autonomy import vitals
        from infrastructure.llm import call_log

        monkeypatch.setattr(call_log, "recent", lambda days=7: [
            {"call_type": "complete", "usage": {"cost": 0.01}},
            {"call_type": "complete", "usage": {"cost": 0.02}},
            {"call_type": "stream", "usage": {"cost": 0.005}},
        ])

        out = vitals.probe_spending()

        assert "внутренние 2" in out["вызовов за 7 дн."]
        assert "внутренние $0.03" in out["стоило"]

    def test_calls_with_no_recorded_cost_are_named_not_hidden(self, monkeypatch):
        from infrastructure.autonomy import vitals
        from infrastructure.llm import call_log

        # Everything logged before the provider's cost was recorded looks like
        # this. Summing only the priced ones would show a smaller bill as if it
        # were the whole one.
        monkeypatch.setattr(call_log, "recent", lambda days=7: [
            {"call_type": "complete"}, {"call_type": "stream"},
        ])

        out = vitals.probe_spending()

        assert "ещё не записана" in out["стоимость"]

    def test_a_corpus_that_cannot_be_read_says_so(self, monkeypatch):
        from infrastructure.autonomy import vitals
        from infrastructure.llm import call_log

        def _broken(days=7):
            raise OSError("dataset gone")

        monkeypatch.setattr(call_log, "recent", _broken)

        assert "не удалось" in " ".join(vitals.probe_spending().values())
