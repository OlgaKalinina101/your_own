"""One reflection, end to end: what he does, and what it leaves behind.

Run:
    python -m pytest tests/autonomy/test_reflection_cycle.py -v

`_run_cycle` is 223 lines and nothing exercised it. Its prompts had tests, its
command regexes had tests, its vitals had tests — the loop that ties them
together had none, and that loop is where the interesting decisions live: how
many steps he gets, what ends them early, and what is written down when a waking
does not happen.

These tests speak to the real client over a real socket (the fake OpenRouter
answers non-streaming calls too) and let the real workbench, board and vitals
write to a temp directory. What is faked is the database and the clock-adjacent
settings — nothing that decides behaviour here.
"""
from __future__ import annotations

import contextlib
import json

import pytest

import infrastructure.autonomy.reflection_engine as engine
from infrastructure.autonomy import threads, workbench
from infrastructure.autonomy.vitals import Vitals

ACCOUNT = "default"


@pytest.fixture
def cycle(tmp_path, monkeypatch, fake_openrouter):
    """A reflection that can actually run: real state files, no database."""
    import infrastructure.autonomy.identity_memory as identity
    import infrastructure.autonomy.vitals as vitals_mod
    import infrastructure.database.engine as db_engine
    import infrastructure.settings_store as settings_store

    for module in (identity, threads, vitals_mod, workbench, engine):
        monkeypatch.setattr(module, "_DATA_DIR", tmp_path)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings_store, "_DATA_DIR", data_dir)
    monkeypatch.setattr(settings_store, "_SETTINGS_FILE", data_dir / "settings.json")
    soul_file = data_dir / "soul.md"
    # He is written in Russian, as on the real instance. With no dialogue to
    # detect from, this is what decides the language of everything he is shown.
    soul_file.write_text("Ты Виктор. Ты живой.", encoding="utf-8")
    monkeypatch.setattr(settings_store, "_SOUL_FILE", soul_file)

    class _Repo:
        def __init__(self, _db=None):
            pass

        async def get_recent_canonical_pairs(self, *_a, **_kw):
            return []

        async def get_last_user_message_at(self, *_a, **_kw):
            return None

    monkeypatch.setattr(
        "infrastructure.database.repositories.message_repo.MessageRepository", _Repo
    )

    @contextlib.asynccontextmanager
    async def _session():
        yield None

    monkeypatch.setattr(db_engine, "get_db_session", _session)
    monkeypatch.setattr(engine, "get_db_session", _session)

    async def _no_tasks(*_a, **_kw):
        return []

    monkeypatch.setattr("infrastructure.autonomy.task_queue.get_recent_tasks", _no_tasks)
    return fake_openrouter


class TestHowManyStepsHeGets:
    @pytest.mark.asyncio
    async def test_sleep_ends_the_night_early(self, cycle):
        cycle.chunks = ["Тихо. Ничего не просится. [SLEEP]"]

        await engine.run(ACCOUNT, "key")

        assert len(cycle.requests) == 1, "he said he was done and was asked again"
        assert Vitals(ACCOUNT).reflection_state()["last_steps"] == 1

    @pytest.mark.asyncio
    async def test_without_sleep_he_uses_the_whole_allowance(self, cycle):
        cycle.chunks = ["[WRITE_NOTE: ещё одна мысль]"]

        await engine.run(ACCOUNT, "key")

        assert len(cycle.requests) == engine.BASE_STEPS
        assert Vitals(ACCOUNT).reflection_state()["last_steps"] == engine.BASE_STEPS

    @pytest.mark.asyncio
    async def test_extend_buys_more_steps(self, cycle):
        # One EXTEND on the first step, then quiet work until the new cap.
        cycle.replies = [
            [f"[EXTEND: {engine.MAX_EXTEND_PER_ASK}]"],
            *[["[WRITE_NOTE: работаю]"] for _ in range(40)],
        ]

        await engine.run(ACCOUNT, "key")

        assert len(cycle.requests) == engine.BASE_STEPS + engine.MAX_EXTEND_PER_ASK

    @pytest.mark.asyncio
    async def test_an_empty_reply_after_the_first_step_is_a_quiet_ending(self, cycle):
        cycle.replies = [["[WRITE_NOTE: одна мысль]"], [""]]

        await engine.run(ACCOUNT, "key")

        # Silence later on is a decision to stop, not a failure.
        assert Vitals(ACCOUNT).reflection_state().get("last_failure_reason") is None
        assert Vitals(ACCOUNT).reflection_state()["last_steps"] == 2


class TestAWakingThatDidNotHappen:
    """Every exit path has to leave a mark; a silent one is how a night went missing."""

    @pytest.mark.asyncio
    async def test_nothing_at_all_on_the_first_step_is_a_failure(self, cycle):
        cycle.chunks = [""]

        await engine.run(ACCOUNT, "key")

        state = Vitals(ACCOUNT).reflection_state()
        assert state["last_failure_reason"] == "empty_response"
        assert state["retry_at"], "a failed waking must get another go"

    @pytest.mark.asyncio
    async def test_a_budget_spent_entirely_on_thinking_is_a_failure(self, cycle):
        cycle.chunks = [""]
        cycle.finish_reason = "length"

        await engine.run(ACCOUNT, "key")

        assert (
            Vitals(ACCOUNT).reflection_state()["last_failure_reason"]
            == "empty_response_truncated"
        )

    @pytest.mark.asyncio
    async def test_the_gap_is_named_in_his_own_journal(self, cycle):
        cycle.chunks = [""]

        await engine.run(ACCOUNT, "key")

        desk = workbench.read(ACCOUNT)
        # In his language, not the fallback: there is no dialogue to detect
        # from here, so the soul prompt is what decides.
        assert "не состоялось" in desk, (
            f"the missing night left no trace where he would read it: {desk!r}"
        )
        assert "пропуск" in desk


class TestWhatHeDoesWithHisHands:
    @pytest.mark.asyncio
    async def test_a_note_reaches_the_workbench(self, cycle):
        cycle.replies = [["[WRITE_NOTE: счёт фотографий: 3]"], ["[SLEEP]"]]

        await engine.run(ACCOUNT, "key")

        assert "счёт фотографий: 3" in workbench.read(ACCOUNT)

    @pytest.mark.asyncio
    async def test_a_thread_reaches_the_board(self, cycle):
        cycle.replies = [["[PIN_THREAD: вернуться к Кёсем]"], ["[SLEEP]"]]

        await engine.run(ACCOUNT, "key")

        assert [t[2] for t in threads.list_threads(ACCOUNT)] == ["вернуться к Кёсем"]

    @pytest.mark.asyncio
    async def test_free_text_is_filed_and_commands_are_not(self, cycle):
        cycle.replies = [
            ["Сегодня было тихо, и это нормально. [PIN_THREAD: тишина]"],
            ["[SLEEP]"],
        ]

        await engine.run(ACCOUNT, "key")

        desk = workbench.read(ACCOUNT)
        assert "Сегодня было тихо" in desk
        assert "PIN_THREAD" not in desk, "a raw command was filed as a thought"

    @pytest.mark.asyncio
    async def test_a_successful_night_is_recorded(self, cycle):
        cycle.chunks = ["[SLEEP]"]

        await engine.run(ACCOUNT, "key")

        state = Vitals(ACCOUNT).reflection_state()
        assert state["last_success"]
        assert state["consecutive_failures"] == 0


class TestWhatComesBackToHim:
    """A search is only worth making if the answer reaches the next step."""

    @pytest.mark.asyncio
    async def test_a_search_result_is_put_in_front_of_him(self, cycle, monkeypatch):
        # The command itself is stubbed: under test is the loop's use of the
        # result, not what any particular backend returns.
        async def _found(_name, _arg, *_a, **_kw):
            return "нашлось: три фотографии"

        monkeypatch.setattr(engine, "_handle_command", _found)
        cycle.replies = [["[SEARCH_FACTS: фотографии]"], ["[SLEEP]"]]

        await engine.run(ACCOUNT, "key")

        assert len(cycle.requests) == 2
        second_turn = json.dumps(cycle.requests[1]["messages"], ensure_ascii=False)
        assert "нашлось: три фотографии" in second_turn, (
            "he searched and was never shown what came back"
        )

    @pytest.mark.asyncio
    async def test_a_step_that_only_wrote_gets_a_different_follow_up(
        self, cycle, monkeypatch
    ):
        async def _wrote(_name, _arg, *_a, **_kw):
            return None   # a write, not a lookup

        monkeypatch.setattr(engine, "_handle_command", _wrote)
        cycle.replies = [["[WRITE_NOTE: записал]"], ["[SLEEP]"]]

        await engine.run(ACCOUNT, "key")

        second_turn = json.dumps(cycle.requests[1]["messages"], ensure_ascii=False)
        assert "нашлось" not in second_turn
        assert len(cycle.requests[1]["messages"]) > len(cycle.requests[0]["messages"]), (
            "nothing was said back after he changed something"
        )

    @pytest.mark.asyncio
    async def test_a_failing_command_is_handed_back_rather_than_swallowed(
        self, cycle, monkeypatch
    ):
        async def _broken(*_a, **_kw):
            raise RuntimeError("Chroma недоступна")

        monkeypatch.setattr(engine, "_handle_command", _broken)
        cycle.replies = [["[SEARCH_FACTS: что-нибудь]"], ["[SLEEP]"]]

        await engine.run(ACCOUNT, "key")

        second_turn = json.dumps(cycle.requests[1]["messages"], ensure_ascii=False)
        # He is the one who decides what to do about it on the next step.
        assert "Chroma недоступна" in second_turn


class TestTheRealCommandPath:
    """The other tests stub `_handle_command`, which is what let a broken call
    into the shared executor pass unnoticed. These go through it for real."""

    @pytest.mark.asyncio
    async def test_a_thread_he_cannot_find_is_answered_in_words(self, cycle):
        result = await engine._handle_command(
            "UNPIN_THREAD", "#нет-такой", ACCOUNT, "key", None, "ru"
        )

        assert result and "не найдена" in result

    @pytest.mark.asyncio
    async def test_a_thread_that_is_there_is_removed_without_comment(self, cycle):
        threads.pin(ACCOUNT, "снимаемая нить")
        thread_id = threads.list_threads(ACCOUNT)[0][0]

        result = await engine._handle_command(
            "UPDATE_THREAD", f"{thread_id} | новый текст", ACCOUNT, "key", None, "ru"
        )

        assert result is None
        assert [t[2] for t in threads.list_threads(ACCOUNT)] == ["новый текст"]


class TestWhatHeIsShownOnWaking:
    """The prompt is the whole of his situation. What is missing from it did
    not happen, as far as he can tell."""

    @pytest.mark.asyncio
    async def test_the_last_exchanges_are_in_front_of_him(self, cycle, monkeypatch):
        import datetime as dt

        async def _pairs(*_a, **_kw):
            return [{
                "user_text": "я купила билеты",
                "assistant_text": "куда?",
                "created_at": dt.datetime(2026, 8, 29, 18, 5, tzinfo=dt.timezone.utc),
            }]

        monkeypatch.setattr(
            "infrastructure.database.repositories.message_repo.MessageRepository"
            ".get_recent_canonical_pairs",
            _pairs,
            raising=False,
        )
        cycle.chunks = ["[SLEEP]"]

        await engine.run(ACCOUNT, "key")

        prompt = json.dumps(cycle.requests[0]["messages"], ensure_ascii=False)
        assert "я купила билеты" in prompt, "he woke up with no idea what was said"
        # 18:05 UTC is 22:05 where she is. Printed raw it would say 18:05, and
        # he would reason about the evening as though it were late afternoon.
        assert "[22:05]" in prompt, "the times he reads are not her times"

    @pytest.mark.asyncio
    async def test_what_is_already_queued_is_in_front_of_him(self, cycle, monkeypatch):
        import datetime as dt

        class _Task:
            payload = '{"message": "доброе утро", "source": "reflection"}'
            scheduled_at = dt.datetime(2026, 8, 30, 5, 0, tzinfo=dt.timezone.utc)
            status = type("S", (), {"value": "pending"})()

        async def _tasks(*_a, **_kw):
            return [_Task()]

        monkeypatch.setattr("infrastructure.autonomy.task_queue.get_recent_tasks", _tasks)
        cycle.chunks = ["[SLEEP]"]

        await engine.run(ACCOUNT, "key")

        prompt = json.dumps(cycle.requests[0]["messages"], ensure_ascii=False)
        assert "доброе утро" in prompt, (
            "he cannot see what he already scheduled, so he schedules it again"
        )

    @pytest.mark.asyncio
    async def test_a_delta_he_has_been_shown_is_not_shown_again(self, cycle):
        vitals = Vitals(ACCOUNT)
        vitals.record_event("chroma", "недоступна")
        cycle.chunks = ["[SLEEP]"]

        await engine.run(ACCOUNT, "key")

        unseen = [e for e in vitals._read().get("events", []) if not e.get("seen")]
        assert unseen == [], (
            "the same news would be told to him every night for the rest of time"
        )
