"""What actually happens when he issues a command.

Run:
    python -m pytest tests/autonomy/test_command_execution.py -v

Both callers meet in ``commands.execute``, and until now nothing drove that
path for real: the reflection tests stub ``_handle_command`` wholesale and the
post-analysis tests stub the queue. That gap is not theoretical — it let a
broken call signature into the shared executor pass a full green run, and only
the linter noticed.

So the stand here is cut as close to the outside world as it can be. Everything
between the command and the boundary is the real code: the argument split, the
typed command, the shared dispatcher, the helper, the local-time parsing, the
canonical row. What is faked is only what leaves the process — the task queue,
the push service, the database.
"""
from __future__ import annotations

import contextlib
import json
from datetime import datetime, timezone

import pytest

from infrastructure.autonomy import commands
from infrastructure.autonomy.cmd_parser import ScheduleMessage, SendMessage

ACCOUNT = "default"


class _Queue:
    """Everything that reached the task queue, and nothing that did not."""

    def __init__(self):
        self.created: list[dict] = []
        self.duplicates_cleared: list[datetime] = []
        self.cancelled: list[datetime] = []

    async def create_task(self, _db, *, account_id, trigger_type, payload, scheduled_at=None):
        self.created.append({
            "account_id": account_id,
            "trigger_type": trigger_type,
            "payload": json.loads(payload),
            "scheduled_at": scheduled_at,
        })

    async def cancel_duplicate_scheduled(self, _db, _account_id, scheduled_at, _source):
        self.duplicates_cleared.append(scheduled_at)
        return 0

    async def cancel_task_by_time(self, _db, _account_id, scheduled_at):
        self.cancelled.append(scheduled_at)
        return False


class _Pushy:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send(self, *, title, body):
        self.sent.append((title, body))


class _Repo:
    saved: list = []

    def __init__(self, _db=None):
        pass

    async def bulk_save(self, rows):
        type(self).saved.extend(rows)


@pytest.fixture
def stand(tmp_path, monkeypatch):
    """The real path, cut at the process boundary."""
    import infrastructure.autonomy.task_queue as task_queue
    import infrastructure.database.engine as db_engine
    import infrastructure.settings_store as settings_store

    monkeypatch.setattr(
        settings_store, "load_settings",
        lambda: {"user_timezone": "Asia/Yerevan", "ai_name": "Виктор"},
    )

    queue = _Queue()
    monkeypatch.setattr(task_queue, "create_task", queue.create_task)
    monkeypatch.setattr(task_queue, "cancel_duplicate_scheduled", queue.cancel_duplicate_scheduled)
    monkeypatch.setattr(task_queue, "cancel_task_by_time", queue.cancel_task_by_time)

    pushy = _Pushy()
    monkeypatch.setattr("infrastructure.pushy.client.get_client", lambda: pushy)

    _Repo.saved = []
    monkeypatch.setattr(
        "infrastructure.database.repositories.message_repo.MessageRepository", _Repo
    )

    @contextlib.asynccontextmanager
    async def _session():
        yield None

    monkeypatch.setattr(db_engine, "get_db_session", _session)

    queue.pushy = pushy
    queue.saved = _Repo.saved
    return queue


async def _run(cmd, *, source="reflection"):
    return await commands.execute(
        cmd, account_id=ACCOUNT, lang="ru", log_prefix="test", source=source
    )


class TestSendingAMessage:
    @pytest.mark.asyncio
    async def test_the_text_reaches_the_device_and_the_history(self, stand):
        result = await _run(SendMessage(text="Родная, я здесь."))

        assert result is None, "a message that went out has nothing to report"
        assert stand.pushy.sent == [("Виктор", "Родная, я здесь.")]
        assert [row.text for row in _Repo.saved] == ["Родная, я здесь."]

    @pytest.mark.asyncio
    async def test_it_is_saved_as_something_she_can_see(self, stand):
        await _run(SendMessage(text="привет"))

        row = _Repo.saved[0]
        assert row.role == "assistant"
        assert row.source == "push"

    @pytest.mark.asyncio
    async def test_an_unconfigured_push_service_still_records_the_message(
        self, stand, monkeypatch
    ):
        monkeypatch.setattr("infrastructure.pushy.client.get_client", lambda: None)

        await _run(SendMessage(text="никуда не ушло"))

        # Nothing was delivered, but he believes he said it, and the history has
        # to agree with him.
        assert stand.pushy.sent == []
        assert [row.text for row in _Repo.saved] == ["никуда не ушло"]


class TestSchedulingAMessage:
    @pytest.mark.asyncio
    async def test_the_time_he_writes_is_his_own_wall_clock(self, stand):
        # He writes 09:00 meaning nine in the morning where she is. Stored as an
        # instant, that is 05:00 UTC — the whole point of infrastructure.clock.
        await _run(ScheduleMessage(ts_str="2026-08-30 09:00", text="доброе утро"))

        assert stand.created[0]["scheduled_at"] == datetime(
            2026, 8, 30, 5, 0, tzinfo=timezone.utc
        )

    @pytest.mark.asyncio
    async def test_the_message_and_its_origin_are_carried_in_the_payload(self, stand):
        await _run(ScheduleMessage(ts_str="2026-08-30 09:00", text=" доброе утро "))

        payload = stand.created[0]["payload"]
        assert payload["message"] == "доброе утро"
        assert payload["source"] == "reflection"

    @pytest.mark.asyncio
    async def test_post_analysis_writes_its_own_origin(self, stand):
        # Three values live in this column and something reads them; a fourth
        # would appear silently.
        await _run(
            ScheduleMessage(ts_str="2026-08-30 09:00", text="привет"),
            source="postanalysis",
        )

        assert stand.created[0]["payload"]["source"] == "postanalysis"

    @pytest.mark.asyncio
    async def test_the_slot_is_cleared_before_it_is_filled(self, stand):
        await _run(ScheduleMessage(ts_str="2026-08-30 09:00", text="привет"))

        assert stand.duplicates_cleared == [stand.created[0]["scheduled_at"]], (
            "two messages would be sitting at the same minute"
        )

    @pytest.mark.asyncio
    async def test_a_time_he_wrote_wrong_is_not_silently_dropped(self, stand):
        with pytest.raises(ValueError):
            await _run(ScheduleMessage(ts_str="завтра утром", text="привет"))

        assert stand.created == [], "nothing was scheduled, as it should be"


class TestBothCallersReachTheSamePlace:
    """Reflection holds two strings, post-analysis a typed object. The command
    is the same command."""

    @pytest.mark.asyncio
    async def test_reflections_string_form_schedules_the_same_thing(self, stand):
        import infrastructure.autonomy.reflection_engine as engine

        await engine._handle_command(
            "SCHEDULE_MESSAGE", "2026-08-30 09:00 | доброе утро",
            ACCOUNT, "key", None, "ru",
        )

        assert stand.created[0]["scheduled_at"] == datetime(
            2026, 8, 30, 5, 0, tzinfo=timezone.utc
        )
        assert stand.created[0]["payload"]["message"] == "доброе утро"
