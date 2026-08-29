"""Background workers: the liveness record, and what a cycle leaves behind.

Run:
    python -m pytest tests/test_workers.py -v

The heartbeat is the only evidence, from inside, that the system was running.
It used to be the first statement of the reflection tick, so a forty-minute
reflection was recorded as forty minutes of downtime and shown to him at his
next waking as a fact. These tests hold the two apart.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

import main


@pytest.fixture
def vitals(tmp_path, monkeypatch):
    import infrastructure.autonomy.vitals as V

    monkeypatch.setattr(V, "_DATA_DIR", tmp_path)
    return V.Vitals("default")


class _Log:
    """Collects warnings instead of printing them."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)

    def info(self, msg, *args):
        pass

    debug = info


class TestHeartbeatIsIndependent:
    def test_a_tick_records_liveness(self, vitals):
        main._heartbeat_tick(vitals, _Log())
        assert vitals._read().get("last_seen")

    def test_a_long_reflection_is_not_recorded_as_downtime(self, vitals, monkeypatch):
        """The regression, stated as its symptom.

        Two ticks separated by a reflection longer than the gap threshold. The
        heartbeat runs on its own schedule, so the stretch in between is covered
        and no gap is recorded.
        """
        import infrastructure.autonomy.vitals as V

        log = _Log()
        main._heartbeat_tick(vitals, log)

        # A reflection running for well over HEARTBEAT_GAP_MINUTES, with the
        # heartbeat still ticking every minute underneath it. `base` is read
        # once: reading it inside the loop would compound the offsets and
        # manufacture the very gaps this test denies.
        base = V._now()
        for minute in range(1, 41):
            now = base + timedelta(minutes=minute)
            monkeypatch.setattr(V, "_now", lambda now=now: now)
            main._heartbeat_tick(vitals, log)

        assert vitals.gaps() == [], "a reflection was written down as downtime"
        assert vitals.pending_events() == []

    def test_a_real_stop_is_still_recorded(self, vitals, monkeypatch):
        """The other half: the instrument must not go blind to actual downtime."""
        import infrastructure.autonomy.vitals as V

        main._heartbeat_tick(vitals, _Log())
        later = V._now() + timedelta(minutes=V.HEARTBEAT_GAP_MINUTES + 30)
        monkeypatch.setattr(V, "_now", lambda: later)

        main._heartbeat_tick(vitals, _Log())

        gaps = vitals.gaps()
        assert len(gaps) == 1 and gaps[0].minutes >= V.HEARTBEAT_GAP_MINUTES

    def test_a_failing_tick_does_not_kill_the_loop(self, monkeypatch):
        class Broken:
            def heartbeat(self):
                raise RuntimeError("disk gone")

        log = _Log()
        main._heartbeat_tick(Broken(), log)  # must not raise
        assert any("disk gone" in w for w in log.warnings)

    @pytest.mark.asyncio
    async def test_the_heartbeat_ticks_while_a_reflection_is_running(
        self, vitals, monkeypatch
    ):
        """Both loops on one event loop; the slow one must not starve the fast one."""
        monkeypatch.setattr(main, "TICK_SECONDS", 0.01)
        monkeypatch.setattr(main, "REFLECTION_SETTLE_SECONDS", 0)

        ticks = 0

        def _count(_vitals, _wlog):
            nonlocal ticks
            ticks += 1

        monkeypatch.setattr(main, "_heartbeat_tick", _count)

        reflection_ran = asyncio.Event()

        async def _slow_tick(_wlog):
            reflection_ran.set()
            await asyncio.sleep(0.5)  # stands in for a multi-minute cycle

        monkeypatch.setattr(main, "_reflection_tick", _slow_tick)

        heart = asyncio.create_task(main._heartbeat_worker())
        reflect = asyncio.create_task(main._reflection_worker())
        try:
            await asyncio.wait_for(reflection_ran.wait(), timeout=2)
            await asyncio.sleep(0.3)
            during = ticks
        finally:
            heart.cancel()
            reflect.cancel()
            for task in (heart, reflect):
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        assert during > 5, f"heartbeat only ticked {during} times during a reflection"


class TestReflectionTickIsCallable:
    """Question 5 of the worker checklist: the tick runs without a 60s sleep."""

    @pytest.mark.asyncio
    async def test_no_api_key_is_a_quiet_no_op(self, monkeypatch):
        import infrastructure.settings_store as settings_store

        monkeypatch.setattr(settings_store, "load_settings", lambda: {"openrouter_api_key": ""})
        # Reaching the database would mean the guard did not hold.
        await main._reflection_tick(_Log())


class TestInterruptedCycleLeavesAMark:
    @pytest.mark.asyncio
    async def test_cancellation_is_recorded_as_a_failed_waking(self, tmp_path, monkeypatch):
        import infrastructure.autonomy.reflection_engine as engine
        import infrastructure.autonomy.vitals as V
        import infrastructure.autonomy.workbench as wb

        monkeypatch.setattr(V, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(wb, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(engine, "_DATA_DIR", tmp_path)

        async def _never_finishes(*_a, **_kw):
            await asyncio.sleep(60)

        monkeypatch.setattr(engine, "_run_cycle", _never_finishes)

        task = asyncio.create_task(engine.run("default", "key"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        state = V.Vitals("default").reflection_state()
        # The timestamp at the top of _run_cycle already says "reflected", so
        # without this the interrupted night reads as one with nothing to say.
        assert state.get("last_failure_reason") == "interrupted"
        assert state.get("retry_at"), "an interrupted waking must get another go"

    @pytest.mark.asyncio
    async def test_the_gap_is_named_in_his_own_journal(self, tmp_path, monkeypatch):
        import infrastructure.autonomy.reflection_engine as engine
        import infrastructure.autonomy.vitals as V
        import infrastructure.autonomy.workbench as wb

        monkeypatch.setattr(V, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(wb, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(engine, "_DATA_DIR", tmp_path)

        async def _never_finishes(*_a, **_kw):
            await asyncio.sleep(60)

        monkeypatch.setattr(engine, "_run_cycle", _never_finishes)

        task = asyncio.create_task(engine.run("default", "key"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert "Пробуждение" in wb.read("default")


class TestArchiveRotationIsIdempotent:
    def test_the_same_note_gets_the_same_id(self):
        from infrastructure.memory.chroma_pipeline import ChromaMemoryPipeline

        first = ChromaMemoryPipeline.archive_entry_id("default", "заметка", "2026-08-29 10:00")
        again = ChromaMemoryPipeline.archive_entry_id("default", "заметка", "2026-08-29 10:00")
        assert first == again

    def test_different_notes_get_different_ids(self):
        from infrastructure.memory.chroma_pipeline import ChromaMemoryPipeline

        ids = {
            ChromaMemoryPipeline.archive_entry_id("default", "заметка", "2026-08-29 10:00"),
            ChromaMemoryPipeline.archive_entry_id("default", "другая", "2026-08-29 10:00"),
            ChromaMemoryPipeline.archive_entry_id("default", "заметка", "2026-08-29 11:00"),
            ChromaMemoryPipeline.archive_entry_id("second", "заметка", "2026-08-29 10:00"),
        }
        assert len(ids) == 4

    @pytest.mark.asyncio
    async def test_a_crash_before_remove_stale_does_not_duplicate(self, monkeypatch):
        """Archive, crash, retry: the second pass must land on the first rows.

        Rotation is Chroma-write then file-truncate with nothing spanning the
        two. A crash in between replays the archive step, and random ids turned
        that replay into permanent duplicates.
        """
        import infrastructure.autonomy.workbench_rotator as rotator

        stale = [("2026-08-29 10:00", "первая"), ("2026-08-29 11:00", "вторая")]
        written: list[str] = []

        class FakePipeline:
            def add_archive_entry(self, account_id, text, timestamp):
                from infrastructure.memory.chroma_pipeline import ChromaMemoryPipeline

                doc_id = ChromaMemoryPipeline.archive_entry_id(account_id, text, timestamp)
                written.append(doc_id)
                return doc_id

        monkeypatch.setattr(rotator.wb, "get_stale_entries", lambda _a: stale)
        monkeypatch.setattr(rotator, "get_chroma_pipeline", lambda: FakePipeline())

        crashed = {"yet": False}

        def _crash_once(_account):
            if not crashed["yet"]:
                crashed["yet"] = True
                raise RuntimeError("power cut before the file was truncated")

        monkeypatch.setattr(rotator.wb, "remove_stale", _crash_once)

        with pytest.raises(RuntimeError):
            await rotator._rotate_to_archive("default")
        await rotator._rotate_to_archive("default")

        assert len(written) == 4, "both passes ran"
        assert len(set(written)) == 2, f"the replay created new rows: {written}"
