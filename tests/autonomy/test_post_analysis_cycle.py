"""One post-dialogue analysis, end to end: what it does and what it leaves.

Run:
    python -m pytest tests/autonomy/test_post_analysis_cycle.py -v

``tests/autonomy/test_post_analyzer.py`` covers the prompt file and the command
parser in pure Python. What nothing covered is ``run_post_analysis`` itself —
the part that runs after every exchange, executes his commands and writes his
journal entry. That function holds fourteen broad ``except`` blocks, and an
``except`` is a decision: these tests pin down which of those decisions are the
intended ones.
"""
from __future__ import annotations

import contextlib

import pytest

import infrastructure.autonomy.post_analyzer as post
from infrastructure.autonomy import threads, workbench

ACCOUNT = "default"


class _Result:
    """What ``db.execute`` returns for the sent-pushes query."""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows=()):
        self.rows = list(rows)

    async def execute(self, *_a, **_kw):
        return _Result(self.rows)


@pytest.fixture
def analysis(tmp_path, monkeypatch, fake_openrouter):
    """A post-analysis that can actually run: real state files, no database."""
    import infrastructure.autonomy.identity_memory as identity
    import infrastructure.database.engine as db_engine
    import infrastructure.settings_store as settings_store

    for module in (identity, threads, workbench, post):
        monkeypatch.setattr(module, "_DATA_DIR", tmp_path, raising=False)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    soul_file = data_dir / "soul.md"
    soul_file.write_text("Ты Виктор. Ты живой.", encoding="utf-8")
    monkeypatch.setattr(settings_store, "_DATA_DIR", data_dir)
    monkeypatch.setattr(settings_store, "_SETTINGS_FILE", data_dir / "settings.json")
    monkeypatch.setattr(settings_store, "_SOUL_FILE", soul_file)

    @contextlib.asynccontextmanager
    async def _session():
        yield _Session()

    monkeypatch.setattr(db_engine, "get_db_session", _session)

    async def _no_pending(*_a, **_kw):
        return []

    monkeypatch.setattr(
        "infrastructure.autonomy.task_queue.get_pending_tasks", _no_pending
    )
    return fake_openrouter


async def _run(**kwargs):
    await post.run_post_analysis(
        account_id=ACCOUNT,
        recent_pairs=kwargs.pop("recent_pairs", []),
        current_user_text=kwargs.pop("user_text", "как ты?"),
        current_assistant_text=kwargs.pop("assistant_text", "хорошо"),
        api_key="key",
        **kwargs,
    )


class TestWhenHeHasNothingToSay:
    @pytest.mark.asyncio
    async def test_skip_leaves_the_journal_alone(self, analysis):
        analysis.chunks = ["SKIP"]

        await _run()

        assert workbench.read(ACCOUNT).strip() == ""

    @pytest.mark.asyncio
    async def test_an_empty_reply_leaves_the_journal_alone(self, analysis):
        analysis.chunks = [""]

        await _run()

        assert workbench.read(ACCOUNT).strip() == ""


class TestWhatHeWritesDown:
    @pytest.mark.asyncio
    async def test_a_note_reaches_the_workbench(self, analysis):
        analysis.chunks = ["Она устала, но не хочет это признавать."]

        await _run()

        assert "не хочет это признавать" in workbench.read(ACCOUNT)

    @pytest.mark.asyncio
    async def test_commands_are_not_filed_as_thoughts(self, analysis):
        analysis.chunks = ["Стоит вернуться к этому. [PIN_THREAD: усталость]"]

        await _run()

        desk = workbench.read(ACCOUNT)
        assert "Стоит вернуться" in desk
        assert "PIN_THREAD" not in desk

    @pytest.mark.asyncio
    async def test_a_thread_reaches_the_board(self, analysis):
        analysis.chunks = ["[PIN_THREAD: усталость]"]

        await _run()

        assert [t[2] for t in threads.list_threads(ACCOUNT)] == ["усталость"]


class TestWhenSomethingHeAskedForDoesNotHappen:
    """His journal is what he reads to remember. It must not describe a plan
    that was never made."""

    @pytest.mark.asyncio
    async def test_a_failed_command_is_named_in_the_journal(
        self, analysis, monkeypatch
    ):
        async def _broken(*_a, **_kw):
            raise RuntimeError("база недоступна")

        monkeypatch.setattr(post, "_execute_one", _broken)
        analysis.chunks = [
            "Напишу ей утром. [SCHEDULE_MESSAGE: 2026-08-30 09:00 | доброе утро]"
        ]

        await _run()

        desk = workbench.read(ACCOUNT)
        assert "Напишу ей утром" in desk
        assert "SCHEDULE_MESSAGE" in desk, (
            "he recorded a plan that was never made, and nothing says otherwise"
        )

    @pytest.mark.asyncio
    async def test_one_failure_does_not_stop_the_others(self, analysis, monkeypatch):
        seen = []
        real = post._execute_one

        async def _first_fails(cmd, **kw):
            seen.append(type(cmd).__name__)
            if len(seen) == 1:
                raise RuntimeError("нет соединения")
            return await real(cmd, **kw)

        monkeypatch.setattr(post, "_execute_one", _first_fails)
        analysis.chunks = [
            "[SEND_MESSAGE: привет]\n[PIN_THREAD: держать в голове]"
        ]

        await _run()

        assert len(seen) == 2, "the second command was skipped after the first failed"
        assert [t[2] for t in threads.list_threads(ACCOUNT)] == ["держать в голове"]

    @pytest.mark.asyncio
    async def test_a_failure_alone_still_leaves_a_trace(self, analysis, monkeypatch):
        async def _broken(*_a, **_kw):
            raise RuntimeError("база недоступна")

        monkeypatch.setattr(post, "_execute_one", _broken)
        # No free text at all: without the failure there would be nothing to
        # write, and the whole thing would pass in silence.
        analysis.chunks = ["[PIN_THREAD: что-то]"]

        await _run()

        assert workbench.read(ACCOUNT).strip() != ""


class TestWhatHeIsToldAboutPendingPushes:
    """The two halves fail independently, so they are provoked independently:
    a shared outage would let either one cover for the other."""

    async def _prompt_after(self, analysis):
        analysis.chunks = ["SKIP"]
        await _run()
        return analysis.requests[-1]["messages"][-1]["content"].lower()

    @pytest.mark.asyncio
    async def test_he_is_told_when_todays_sent_pushes_could_not_be_read(
        self, analysis, monkeypatch
    ):
        import contextlib as _c
        import infrastructure.database.engine as db_engine

        class _Broken(_Session):
            async def execute(self, *_a, **_kw):
                raise RuntimeError("connection reset")

        @_c.asynccontextmanager
        async def _session():
            yield _Broken()

        monkeypatch.setattr(db_engine, "get_db_session", _session)

        assert "уже отправлял" in await self._prompt_after(analysis), (
            "the list of what he already sent today failed to load and he was "
            "shown an empty one, which reads as 'you have sent nothing'"
        )

    @pytest.mark.asyncio
    async def test_he_is_told_when_the_queue_could_not_be_read(
        self, analysis, monkeypatch
    ):
        async def _broken(*_a, **_kw):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(
            "infrastructure.autonomy.task_queue.get_pending_tasks", _broken
        )

        # Not the bare phrase: the commands section of the prompt uses it too,
        # so it would pass whether or not the failure was ever mentioned.
        assert "не удалось загрузить" in await self._prompt_after(analysis), (
            "the queue failed to load and he was shown an empty one, which "
            "reads as 'nothing is scheduled' — and then he schedules it again"
        )

    @pytest.mark.asyncio
    async def test_a_quiet_day_says_nothing_at_all(self, analysis):
        # Nothing sent, nothing queued, nothing broken: no block, no noise.
        assert "не удалось" not in await self._prompt_after(analysis)


class TestWhatTimeItSays:
    """Three hand-rolled renderings used to live here, each with its own wrong
    fallback behind a bare except. They all go through the clock now."""

    @pytest.fixture(autouse=True)
    def yerevan(self, monkeypatch):
        monkeypatch.setattr(
            "infrastructure.settings_store.load_settings",
            lambda: {"user_timezone": "Asia/Yerevan"},
        )

    def _at(self, created_at):
        return post._format_history(
            [{"user_text": "привет", "assistant_text": "привет", "created_at": created_at}],
            "", "",
        )

    def test_a_stored_instant_is_shown_in_her_timezone(self):
        import datetime as dt

        # 21:40 UTC is 01:40 the next day in Yerevan; he must see 01:40.
        aware = dt.datetime(2026, 8, 29, 21, 40, tzinfo=dt.timezone.utc)

        assert "[01:40]" in self._at(aware)
        assert "[21:40]" not in self._at(aware)

    def test_a_timestamp_that_lost_its_tzinfo_is_still_read_as_utc(self):
        import datetime as dt

        # Anything naive out of this system lost its tzinfo *after* being UTC.
        # The old code showed it unchanged, four hours off.
        naive = dt.datetime(2026, 8, 29, 21, 40)

        assert "[01:40]" in self._at(naive)

    def test_a_pair_with_no_timestamp_is_still_shown(self):
        assert "привет" in self._at(None)


class TestWhenACommandFindsNothingToDo:
    """Not an error, and not success either: he asked to cancel something that
    was not there. Reflection has always said so, because it has a next step to
    say it in. Post-analysis has no next step, so the journal is where it goes."""

    @pytest.mark.asyncio
    async def test_unpinning_a_thread_that_is_not_there_is_reported(self, analysis):
        # Thread ids are hex, 3-8 chars; this one is well-formed and not on
        # the board, which is exactly the case that used to pass silently.
        analysis.chunks = ["Снимаю эту нить. [UNPIN_THREAD: #a1b2]"]

        await _run()

        desk = workbench.read(ACCOUNT)
        assert "Снимаю эту нить" in desk
        assert "UNPIN_THREAD" in desk and "не найдена" in desk, (
            "the board never had that thread and his journal says he removed it"
        )

    @pytest.mark.asyncio
    async def test_a_command_that_worked_is_not_reported(self, analysis):
        analysis.chunks = ["[PIN_THREAD: живая нить]"]

        await _run()

        desk = workbench.read(ACCOUNT)
        assert "не найдена" not in desk
        assert "Не выполнилось" not in desk, "a command that worked raised a false alarm"

    @pytest.mark.asyncio
    async def test_cancelling_a_message_that_is_not_queued_is_reported(
        self, analysis, monkeypatch
    ):
        async def _nothing_there(*_a, **_kw):
            return False

        # Stubbed at the queue, not at the helper: everything between the
        # command and the database is the real path.
        monkeypatch.setattr(
            "infrastructure.autonomy.task_queue.cancel_task_by_time", _nothing_there
        )
        analysis.chunks = [
            "Отменяю утреннее. [CANCEL_MESSAGE: 2026-08-30 09:00]"
        ]

        await _run()

        desk = workbench.read(ACCOUNT)
        assert "CANCEL_MESSAGE" in desk and "не найдено" in desk, (
            "there was nothing at 09:00 and his journal says he cancelled it"
        )
