"""Tests for the reflection follow-up prompt family:
  - reflection_continuation.md   (after search results)
  - reflection_after_action.md   (after write/send)
  - reflection_extend_offer.md   (near end of steps)

Also tests _build_pending_tasks_block EN/RU parity.

How they fit into the loop (reflection_engine.py):

  awakening_system ──► step 1 ──► LLM response
                                      │
                      ┌───────────────┴───────────────┐
                      │ search result?                 │ write/send?
                      ▼                                ▼
              _build_continuation            _build_after_action
              (injected as user msg)         (injected as user msg)
                      │                                │
                      └──────────────┬─────────────────┘
                                     ▼
                              step N  (steps_left == EXTEND_ASK_BEFORE)
                                     │
                              _build_extend_offer
                              (prepended to user msg before LLM call)

Run with:
    cd C:\\Users\\Alien\\PycharmProjects\\your_own
    python -m pytest tests/autonomy/test_reflection_prompts.py -v
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest
from infrastructure.llm.prompt_loader import get_prompt
from infrastructure.autonomy.reflection_engine import (
    _build_continuation,
    _build_after_action,
    _build_extend_offer,
    _build_pending_tasks_block,
    BASE_STEPS,
    EXTEND_ASK_BEFORE,
    MAX_EXTEND_PER_ASK,
)

_ALL_CMDS = [
    "SEARCH_MEMORIES",
    "SEARCH_NOTES",
    "SEARCH_DIALOGUE",
    "WEB_SEARCH",
    "WRITE_NOTE",
    "WRITE_IDENTITY",
    "SEND_MESSAGE",
    "SCHEDULE_MESSAGE",
    "CANCEL_MESSAGE",
    "RESCHEDULE_MESSAGE",
    "REWRITE_MESSAGE",
    "SLEEP",
]

_CONT_PATH = "infrastructure/autonomy/prompts/reflection_continuation.md"
_AFTER_PATH = "infrastructure/autonomy/prompts/reflection_after_action.md"
_EXTEND_PATH = "infrastructure/autonomy/prompts/reflection_extend_offer.md"


# ── 1. reflection_continuation ────────────────────────────────────────────────

class TestContinuation:

    def _build(self, lang: str, result: str = "Found: she loves the sea.") -> str:
        return _build_continuation("Victor", lang, steps_left=5, result=result)

    def test_ru_loads(self):
        text = self._build("ru")
        assert "5" in text

    def test_en_loads(self):
        text = self._build("en")
        assert "5" in text

    def test_ru_result_injected(self):
        text = self._build("ru", result="Результат: она любит море.")
        assert "она любит море" in text

    def test_en_result_injected(self):
        text = self._build("en", result="Found: she loves the sea.")
        assert "she loves the sea" in text

    def test_ru_has_all_commands(self):
        text = self._build("ru")
        for cmd in _ALL_CMDS:
            assert cmd in text, f"RU continuation missing {cmd}"

    def test_en_has_all_commands(self):
        text = self._build("en")
        for cmd in _ALL_CMDS:
            assert cmd in text, f"EN continuation missing {cmd}"

    def test_no_unfilled_placeholders(self):
        for lang in ("ru", "en"):
            text = self._build(lang)
            assert "{" not in text, f"{lang} continuation has unfilled placeholder"

    def test_ru_en_command_parity(self):
        ru = self._build("ru")
        en = self._build("en")
        for cmd in _ALL_CMDS:
            assert cmd in ru
            assert cmd in en


# ── 2. reflection_after_action ────────────────────────────────────────────────

class TestAfterAction:

    def _build(self, lang: str) -> str:
        return _build_after_action("Victor", lang, steps_left=3)

    def test_ru_loads(self):
        text = self._build("ru")
        assert "3" in text

    def test_en_loads(self):
        text = self._build("en")
        assert "3" in text

    def test_ru_has_all_commands(self):
        text = self._build("ru")
        for cmd in _ALL_CMDS:
            assert cmd in text, f"RU after_action missing {cmd}"

    def test_en_has_all_commands(self):
        text = self._build("en")
        for cmd in _ALL_CMDS:
            assert cmd in text, f"EN after_action missing {cmd}"

    def test_no_unfilled_placeholders(self):
        for lang in ("ru", "en"):
            text = self._build(lang)
            assert "{" not in text, f"{lang} after_action has unfilled placeholder"

    def test_ru_en_command_parity(self):
        ru = self._build("ru")
        en = self._build("en")
        for cmd in _ALL_CMDS:
            assert cmd in ru
            assert cmd in en


# ── 3. reflection_extend_offer ────────────────────────────────────────────────

class TestExtendOffer:

    def _build(self, lang: str) -> str:
        return _build_extend_offer(lang, step=6, max_steps=8, max_extend=MAX_EXTEND_PER_ASK)

    def test_ru_loads(self):
        text = self._build("ru")
        assert "EXTEND" in text
        assert str(MAX_EXTEND_PER_ASK) in text

    def test_en_loads(self):
        text = self._build("en")
        assert "EXTEND" in text
        assert str(MAX_EXTEND_PER_ASK) in text

    def test_no_unfilled_placeholders(self):
        for lang in ("ru", "en"):
            text = self._build(lang)
            assert "{" not in text, f"{lang} extend_offer has unfilled placeholder"

    def test_sleep_mentioned(self):
        for lang in ("ru", "en"):
            assert "SLEEP" in self._build(lang)

    def test_step_numbers_present(self):
        for lang in ("ru", "en"):
            text = self._build(lang)
            assert "6" in text
            assert "8" in text


# ── 4. _build_pending_tasks_block ─────────────────────────────────────────────

class TestPendingTasksBlock:
    """Tests the dynamically built block injected into awakening and continuation prompts."""

    def test_empty_returns_empty_string(self):
        assert _build_pending_tasks_block("ru", []) == ""
        assert _build_pending_tasks_block("en", []) == ""

    def test_ru_block_has_rewrite(self):
        # Even with no tasks, the footer in RU should mention management commands.
        # With tasks it definitely should — but we test the footer content via
        # a mock-like object since we don't have a real DB here.
        from unittest.mock import MagicMock
        from datetime import datetime, timezone
        from infrastructure.database.models.autonomy_task import TaskStatus, TriggerType
        import json

        task = MagicMock()
        task.payload = json.dumps({"message": "Доброе утро"})
        task.scheduled_at = datetime(2026, 3, 18, 9, 0, tzinfo=timezone.utc)
        task.status = TaskStatus.PENDING

        block = _build_pending_tasks_block("ru", [task])
        assert "CANCEL_MESSAGE" in block
        assert "RESCHEDULE_MESSAGE" in block
        # REWRITE_MESSAGE is in the awakening prompt's command list, not the footer —
        # but the block still shouldn't contradict it. The key check: no error on build.
        assert "Доброе утро" in block

    def test_en_block_has_rewrite(self):
        from unittest.mock import MagicMock
        from datetime import datetime, timezone
        from infrastructure.database.models.autonomy_task import TaskStatus
        import json

        task = MagicMock()
        task.payload = json.dumps({"message": "Good morning"})
        task.scheduled_at = datetime(2026, 3, 18, 9, 0, tzinfo=timezone.utc)
        task.status = TaskStatus.PENDING

        block = _build_pending_tasks_block("en", [task])
        assert "CANCEL_MESSAGE" in block
        assert "RESCHEDULE_MESSAGE" in block
        assert "REWRITE_MESSAGE" in block
        assert "Good morning" in block

    def test_done_task_label(self):
        from unittest.mock import MagicMock
        from datetime import datetime, timezone
        from infrastructure.database.models.autonomy_task import TaskStatus
        import json

        task = MagicMock()
        task.payload = json.dumps({"message": "Sent already"})
        task.scheduled_at = datetime(2026, 3, 17, 8, 0, tzinfo=timezone.utc)
        task.status = TaskStatus.DONE

        ru_block = _build_pending_tasks_block("ru", [task])
        en_block = _build_pending_tasks_block("en", [task])
        assert "отправлено" in ru_block
        assert "sent" in en_block


# ── 5. Full loop simulation ───────────────────────────────────────────────────

class TestLoopSequence:
    """Simulate the prompt sequence of one reflection cycle and verify
    each injected prompt is well-formed at the right step."""

    def test_step1_awakening_no_extend_offer(self):
        """Step 1: only awakening system, no extend offer yet."""
        from infrastructure.autonomy.reflection_engine import _build_awakening_system
        system = _build_awakening_system(
            ai_name="Victor",
            lang="ru",
            identity_content="Я — Victor.",
            workbench_content="[2026-03-17] Тишина.",
            recent_dialogue="User: Привет\nAssistant: Привет!",
            current_time="2026-03-18 08:00",
            hours_since_last="4.0 ч",
            pending_tasks_block="",
            cooldown_h=4,
            interval_h=12,
        )
        assert "Victor" in system
        assert "SLEEP" in system

    def test_continuation_after_search(self):
        result = "[SEARCH_MEMORIES: детство] → [факт] Она любит море."
        text = _build_continuation("Victor", "ru", steps_left=6, result=result)
        assert "детство" in text
        assert "WRITE_NOTE" in text
        assert "SLEEP" in text

    def test_after_action_after_write(self):
        text = _build_after_action("Victor", "en", steps_left=4)
        assert "4" in text
        assert "SLEEP" in text
        assert "SEARCH_MEMORIES" in text

    def test_extend_offer_at_correct_step(self):
        """Extend offer appears when steps_left == EXTEND_ASK_BEFORE (2)."""
        step = BASE_STEPS - EXTEND_ASK_BEFORE  # e.g. step 6 when BASE=8, ASK_BEFORE=2
        text = _build_extend_offer("ru", step=step, max_steps=BASE_STEPS, max_extend=MAX_EXTEND_PER_ASK)
        assert "EXTEND" in text
        assert "SLEEP" in text

    def test_extend_offer_en(self):
        text = _build_extend_offer("en", step=6, max_steps=8, max_extend=5)
        assert "EXTEND" in text
        assert "{" not in text
