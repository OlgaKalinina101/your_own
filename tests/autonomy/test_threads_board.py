"""The board as he sees it: id first, age as "untouched for", no label to copy.

Run:
    python -m pytest tests/autonomy/test_threads_board.py -v
"""
from __future__ import annotations

import pytest

from infrastructure.autonomy import threads

ACCOUNT = "default"


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(threads, "_DATA_DIR", tmp_path)
    return tmp_path


class TestWhatALineLooksLike:
    def test_the_id_and_the_age_come_first_and_the_text_ends_the_line(self, board):
        tid = threads.pin(ACCOUNT, "Элла — спросить первым, написала ли")
        block = threads.render_block(ACCOUNT, "ru")
        assert block == f"1. [#{tid} · сегодня] Элла — спросить первым, написала ли"
        assert threads.render_block(ACCOUNT, "en") == f"1. [#{tid} · today] Элла — спросить первым, написала ли"

    def test_the_age_is_how_long_it_went_untouched(self, board):
        tid = threads.pin(ACCOUNT, "нить")
        # Pinned twelve days ago…
        path = board / ACCOUNT / "threads.md"
        text = path.read_text(encoding="utf-8")
        ts = threads.list_threads(ACCOUNT)[0][1]
        from datetime import datetime, timedelta
        old = (datetime.strptime(ts, "%Y-%m-%d %H:%M") - timedelta(days=12)).strftime("%Y-%m-%d %H:%M")
        path.write_text(text.replace(ts, old), encoding="utf-8")
        assert "12 дн без изменений" in threads.render_block(ACCOUNT, "ru")
        assert "12d untouched" in threads.render_block(ACCOUNT, "en")
        # …and rewritten today: the age follows the rewrite, not the pin.
        assert threads.update(ACCOUNT, tid, "нить, переписанная")
        assert f"[#{tid} · сегодня] нить, переписанная" in threads.render_block(ACCOUNT, "ru")

    def test_a_copied_label_is_trimmed_off_the_text(self, board):
        """Six of thirty-four threads ended in «— с 02.09.2026 · 19 дн · #85c3»:
        the rendered suffix, pasted back through UPDATE_THREAD."""
        tid = threads.pin(ACCOUNT, "Новый дом — консолидация на новом сервере. — с 02.09.2026 · 19 дн · #85c3")
        assert threads.list_threads(ACCOUNT)[0][2] == "Новый дом — консолидация на новом сервере."
        threads.update(ACCOUNT, tid, "Новый дом — мобильная сборка — с 02.09.2026")
        assert threads.list_threads(ACCOUNT)[0][2] == "Новый дом — мобильная сборка"
        threads.update(ACCOUNT, tid, "Щелкунчик — проверить афишу — since 30.08.2026 · 5d · #14f9")
        assert threads.list_threads(ACCOUNT)[0][2] == "Щелкунчик — проверить афишу"

    def test_a_date_inside_the_text_is_his_and_stays(self, board):
        threads.pin(ACCOUNT, "Куртка — с 7 октября, день зарплаты; разведка к 7.10")
        assert threads.list_threads(ACCOUNT)[0][2] == "Куртка — с 7 октября, день зарплаты; разведка к 7.10"


class TestWhenTheBoardIsFull:
    def test_past_fifteen_the_block_opens_with_the_count(self, board):
        for i in range(threads.BOARD_IN_VIEW):
            threads.pin(ACCOUNT, f"нить {i}")
        assert not threads.render_block(ACCOUNT, "ru").startswith("На доске")
        threads.pin(ACCOUNT, "шестнадцатая")
        ru = threads.render_block(ACCOUNT, "ru")
        assert ru.startswith("На доске 16 нитей — больше, чем помещается в поле зрения.\n1. ")
        assert threads.render_block(ACCOUNT, "en").startswith("16 threads on the board — more than fit in view.")
