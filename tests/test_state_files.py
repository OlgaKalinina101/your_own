"""State files: a crash mid-write, and a file that no longer parses.

Run:
    python -m pytest tests/test_state_files.py -v

Two properties, checked for every file under ``data/``:

  1. **A write is all-or-nothing.** No reader may ever see a half-written file.
  2. **A damaged file is not destroyed by the next write.** Every reader here
     falls back to empty on a parse error — on purpose, a corrupt panel must not
     take the reflection down. The loss happens afterwards, when the first write
     lands on top of it.

The second one has teeth: truncating settings.json used to empty the OpenRouter
key in silence, and the next save from the UI wrote that emptiness back.
"""
from __future__ import annotations

import json
import os

import pytest

from infrastructure.state_file import atomic_write_text, quarantine, read_json


class TestAtomicWrite:
    def test_the_content_lands(self, tmp_path):
        target = tmp_path / "a.txt"
        atomic_write_text(target, "привет\nмир")
        assert target.read_text(encoding="utf-8") == "привет\nмир"

    def test_it_creates_missing_parents(self, tmp_path):
        target = tmp_path / "deep" / "deeper" / "a.txt"
        atomic_write_text(target, "x")
        assert target.read_text(encoding="utf-8") == "x"

    def test_a_failed_write_leaves_the_old_content_intact(self, tmp_path, monkeypatch):
        target = tmp_path / "a.json"
        atomic_write_text(target, '{"key": "original"}')

        boom = OSError("disk full")

        def _explode(*_a, **_kw):
            raise boom

        monkeypatch.setattr(os, "replace", _explode)
        with pytest.raises(OSError):
            atomic_write_text(target, '{"key": "replacement"}')

        # The whole point: the old file is still whole, not truncated to zero.
        assert json.loads(target.read_text(encoding="utf-8")) == {"key": "original"}

    def test_it_does_not_leave_temp_files_behind(self, tmp_path, monkeypatch):
        target = tmp_path / "a.txt"
        atomic_write_text(target, "one")
        monkeypatch.setattr(os, "replace", lambda *_a, **_kw: (_ for _ in ()).throw(OSError()))
        with pytest.raises(OSError):
            atomic_write_text(target, "two")
        assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


class TestQuarantine:
    def test_the_damaged_file_is_kept(self, tmp_path):
        target = tmp_path / "vitals.json"
        target.write_text("{ half a fi", encoding="utf-8")

        dest = quarantine(target)

        assert dest is not None and dest.exists()
        assert dest.read_text(encoding="utf-8") == "{ half a fi"
        assert not target.exists()

    def test_read_json_quarantines_and_falls_back(self, tmp_path):
        target = tmp_path / "vitals.json"
        target.write_text('{"reflection": {"consecutive', encoding="utf-8")

        assert read_json(target, default={}) == {}
        assert not target.exists()
        assert len(list(tmp_path.glob("vitals.json.corrupt-*"))) == 1

    def test_a_missing_file_is_not_damage(self, tmp_path):
        assert read_json(tmp_path / "nope.json", default={"a": 1}) == {"a": 1}
        assert list(tmp_path.glob("*.corrupt-*")) == []


class TestCorruptSettingsDoesNotEatTheKey:
    """The exact failure: truncate settings.json, lose the OpenRouter key."""

    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        import infrastructure.settings_store as settings_store

        monkeypatch.setattr(settings_store, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(settings_store, "_SETTINGS_FILE", tmp_path / "settings.json")
        monkeypatch.setattr(settings_store, "_SOUL_FILE", tmp_path / "soul.md")
        return settings_store

    def test_a_truncated_file_is_set_aside_not_overwritten(self, store, tmp_path):
        store.save_settings({"openrouter_api_key": "sk-or-REAL", "ai_name": "Виктор"})
        raw = (tmp_path / "settings.json").read_text(encoding="utf-8")
        (tmp_path / "settings.json").write_text(raw[: len(raw) // 2], encoding="utf-8")

        # Reading still degrades to defaults — a broken file must not take the
        # app down — but the damaged copy survives for recovery.
        assert store.load_settings()["openrouter_api_key"] == ""
        damaged = list(tmp_path.glob("settings.json.corrupt-*"))
        assert len(damaged) == 1
        assert "sk-or-REAL" in damaged[0].read_text(encoding="utf-8")

    def test_a_later_save_cannot_erase_the_damaged_copy(self, store, tmp_path):
        store.save_settings({"openrouter_api_key": "sk-or-REAL"})
        raw = (tmp_path / "settings.json").read_text(encoding="utf-8")
        (tmp_path / "settings.json").write_text(raw[: len(raw) // 2], encoding="utf-8")

        store.save_settings({"temperature": 0.8})  # this is what used to cement the loss

        damaged = list(tmp_path.glob("settings.json.corrupt-*"))
        assert len(damaged) == 1, "the only copy of the key was destroyed"
        assert "sk-or-REAL" in damaged[0].read_text(encoding="utf-8")

    def test_it_says_so_out_loud(self, store, tmp_path, caplog):
        import logging

        store.save_settings({"openrouter_api_key": "sk-or-REAL"})
        raw = (tmp_path / "settings.json").read_text(encoding="utf-8")
        (tmp_path / "settings.json").write_text(raw[: len(raw) // 2], encoding="utf-8")

        with caplog.at_level(logging.ERROR):
            store.load_settings()

        assert caplog.records, "a lost API key must not be silent"


class TestCorruptVitalsKeepsItsHistory:
    @pytest.fixture
    def vitals(self, tmp_path, monkeypatch):
        import infrastructure.autonomy.vitals as V

        monkeypatch.setattr(V, "_DATA_DIR", tmp_path)
        return V.Vitals("default")

    def test_a_truncated_panel_does_not_raise(self, vitals):
        vitals.heartbeat()
        raw = vitals.path.read_text(encoding="utf-8")
        vitals.path.write_text(raw[: len(raw) // 2], encoding="utf-8")

        assert vitals.reflection_state() == {}
        assert vitals.gaps() == []
        assert vitals.retry_due() is False

    def test_the_recorded_gaps_survive_as_a_copy(self, vitals, tmp_path):
        vitals.record_reflection_failure("exception")
        vitals.heartbeat()
        raw = vitals.path.read_text(encoding="utf-8")
        assert "consecutive_failures" in raw
        vitals.path.write_text(raw[: len(raw) // 2], encoding="utf-8")

        vitals.heartbeat()  # the write that used to destroy the evidence

        damaged = list((tmp_path / "default").glob("vitals.json.corrupt-*"))
        assert len(damaged) == 1
        assert "consecutive_failures" in damaged[0].read_text(encoding="utf-8")


class TestLastReflectionIsPerAccount:
    @pytest.fixture
    def engine(self, tmp_path, monkeypatch):
        import infrastructure.autonomy.reflection_engine as re_engine

        monkeypatch.setattr(re_engine, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(
            re_engine, "_LEGACY_REFLECTION_TS_FILE", tmp_path / "last_reflection.txt"
        )
        return re_engine

    def test_two_accounts_do_not_share_a_cooldown(self, engine):
        engine._set_last_reflection_ts("default")

        assert engine._get_last_reflection_ts("default") is not None
        assert engine._get_last_reflection_ts("second") is None

    def test_the_old_global_file_is_migrated_not_dropped(self, engine, tmp_path):
        stamp = "2026-08-29T10:00:00+00:00"
        (tmp_path / "last_reflection.txt").write_text(stamp, encoding="utf-8")

        # A fresh file would read as "never reflected" and fire a reflection on
        # the very next tick after the upgrade.
        got = engine._get_last_reflection_ts("default")

        assert got is not None and got.isoformat() == stamp
        assert (tmp_path / "default" / "last_reflection.txt").exists()
        assert not (tmp_path / "last_reflection.txt").exists()
