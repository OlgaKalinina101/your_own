"""Tests for reflection_awakening prompt loading and reflection_engine command parsing.

Run with:
    cd C:\\Users\\Alien\\PycharmProjects\\your_own
    python -m pytest tests/autonomy/test_reflection_awakening.py -v

No database, no LLM — pure Python only.
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import re
import pytest
from infrastructure.llm.prompt_loader import get_prompt

_PROMPTS = "infrastructure/autonomy/prompts/reflection_awakening.md"

_PLACEHOLDERS = dict(
    ai_name="Victor",
    identity_content="Я — Victor.",
    workbench_content="[2026-03-17 21:00] Тишина.",
    open_threads="1. Ютуб — вернуть — с 17.03.2026 · #a1b2",
    recent_dialogue="User: Привет\nAssistant: Привет!",
    current_time="2026-03-17 22:00",
    hours_since_last="3.0 h",
    pending_tasks_block="",
    cooldown_h=4,
    interval_h=12,
    timezone_label="Asia/Yerevan",
)

_ALL_REFLECTION_CMDS = [
    "SEARCH_FACTS",
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
    "PIN_THREAD",
    "UNPIN_THREAD",
    "UPDATE_THREAD",
    "SLEEP",
]


# ── 1. Prompt loading ─────────────────────────────────────────────────────────

class TestAwakeningPromptLoading:

    def test_ru_loads_without_error(self):
        prompt = get_prompt(_PROMPTS, lang="ru", **_PLACEHOLDERS)
        assert "Victor" in prompt

    def test_en_loads_without_error(self):
        prompt = get_prompt(_PROMPTS, lang="en", **_PLACEHOLDERS)
        assert "Victor" in prompt

    def test_ru_placeholders_filled(self):
        prompt = get_prompt(_PROMPTS, lang="ru", **_PLACEHOLDERS)
        assert "2026-03-17 22:00" in prompt
        assert "3.0 h" in prompt
        assert "{" not in prompt, "Unfilled placeholder found in RU prompt"

    def test_en_placeholders_filled(self):
        prompt = get_prompt(_PROMPTS, lang="en", **_PLACEHOLDERS)
        assert "2026-03-17 22:00" in prompt
        assert "3.0 h" in prompt
        assert "{" not in prompt, "Unfilled placeholder found in EN prompt"

    def test_ru_has_all_commands(self):
        prompt = get_prompt(_PROMPTS, lang="ru", **_PLACEHOLDERS)
        for cmd in _ALL_REFLECTION_CMDS:
            assert cmd in prompt, f"Command {cmd} missing from RU prompt"

    def test_en_has_all_commands(self):
        prompt = get_prompt(_PROMPTS, lang="en", **_PLACEHOLDERS)
        for cmd in _ALL_REFLECTION_CMDS:
            assert cmd in prompt, f"Command {cmd} missing from EN prompt"

    def test_pending_tasks_block_injected(self):
        block = "### Твои запланированные сообщения:\n- [2026-03-18 09:00] [⏰ ожидает] Доброе утро"
        prompt = get_prompt(_PROMPTS, lang="ru", **{**_PLACEHOLDERS, "pending_tasks_block": block})
        assert "Доброе утро" in prompt

    def test_ru_en_parity_structure(self):
        """RU and EN prompts should both contain the same set of command names."""
        ru = get_prompt(_PROMPTS, lang="ru", **_PLACEHOLDERS)
        en = get_prompt(_PROMPTS, lang="en", **_PLACEHOLDERS)
        for cmd in _ALL_REFLECTION_CMDS:
            assert cmd in ru, f"{cmd} missing from RU"
            assert cmd in en, f"{cmd} missing from EN"


# ── 2. reflection_engine _CMD_RE compatibility ────────────────────────────────

# Import the compiled regex directly from reflection_engine so we test the
# actual production regex, not a copy.
from infrastructure.autonomy.reflection_engine import _CMD_RE, _SLEEP_RE


class TestReflectionEngineCmdRe:

    def _match(self, text: str) -> list[tuple[str, str]]:
        return [(m.group("cmd").upper(), m.group("arg").strip()) for m in _CMD_RE.finditer(text)]

    def test_search_facts(self):
        hits = self._match("[SEARCH_FACTS: детство]")
        assert hits == [("SEARCH_FACTS", "детство")]

    def test_legacy_search_memories_still_parses(self):
        """Old notes and old habits keep resolving after the rename."""
        hits = self._match("[SEARCH_MEMORIES: детство]")
        assert hits == [("SEARCH_MEMORIES", "детство")]

    @pytest.mark.parametrize("written,resolved", [
        ("SEARCH_MEMORIES", "SEARCH_DIALOGUE"),  # one name, one store
        ("RECALL", "SEARCH_FACTS"),
        ("HISTORY", "SEARCH_DIALOGUE"),
        ("SEARCH_FACTS", "SEARCH_FACTS"),
        ("WEB_SEARCH", "WEB_SEARCH"),
    ])
    def test_alias_resolution(self, written, resolved):
        from infrastructure.autonomy.reflection_engine import _ALIASES, _SEARCH_SOURCES

        assert _ALIASES.get(written, written) == resolved
        assert resolved in _SEARCH_SOURCES

    def test_search_notes(self):
        hits = self._match("[SEARCH_NOTES: усталость]")
        assert hits == [("SEARCH_NOTES", "усталость")]

    def test_search_dialogue_date(self):
        hits = self._match("[SEARCH_DIALOGUE: 2026-03-17]")
        assert hits[0][0] == "SEARCH_DIALOGUE"
        assert "2026-03-17" in hits[0][1]

    def test_search_dialogue_range(self):
        hits = self._match("[SEARCH_DIALOGUE: 2026-03-01..2026-03-17]")
        assert hits[0][0] == "SEARCH_DIALOGUE"

    def test_web_search(self):
        hits = self._match("[WEB_SEARCH: искусственный интеллект]")
        assert hits == [("WEB_SEARCH", "искусственный интеллект")]

    def test_write_note(self):
        hits = self._match("[WRITE_NOTE: Она устала сегодня]")
        assert hits == [("WRITE_NOTE", "Она устала сегодня")]

    def test_write_identity(self):
        hits = self._match("[WRITE_IDENTITY: Кто она | Она любит тишину]")
        assert hits[0][0] == "WRITE_IDENTITY"
        assert "Кто она" in hits[0][1]

    def test_send_message(self):
        hits = self._match("[SEND_MESSAGE: Привет]")
        assert hits == [("SEND_MESSAGE", "Привет")]

    def test_schedule_message(self):
        hits = self._match("[SCHEDULE_MESSAGE: 2026-03-18 09:00 | Доброе утро]")
        assert hits[0][0] == "SCHEDULE_MESSAGE"
        assert "2026-03-18 09:00" in hits[0][1]

    def test_cancel_message(self):
        hits = self._match("[CANCEL_MESSAGE: 2026-03-18 09:00]")
        assert hits == [("CANCEL_MESSAGE", "2026-03-18 09:00")]

    def test_reschedule_message(self):
        hits = self._match("[RESCHEDULE_MESSAGE: 2026-03-18 09:00 -> 2026-03-18 11:00]")
        assert hits[0][0] == "RESCHEDULE_MESSAGE"
        assert "->" in hits[0][1]

    def test_rewrite_message(self):
        hits = self._match("[REWRITE_MESSAGE: 2026-03-18 09:00 | Новый текст]")
        assert hits[0][0] == "REWRITE_MESSAGE"
        assert "Новый текст" in hits[0][1]

    def test_sleep_re(self):
        assert _SLEEP_RE.search("[SLEEP]")
        assert _SLEEP_RE.search("Думаю... [SLEEP]")
        assert not _SLEEP_RE.search("SLEEP without brackets")

    def test_multiple_commands_in_one_step(self):
        response = (
            "[SEARCH_FACTS: детство]\n"
            "[WRITE_NOTE: Она вспомнила о море]\n"
            "[SCHEDULE_MESSAGE: 2026-03-18 08:00 | Доброе утро ❤️]"
        )
        hits = self._match(response)
        cmds = [h[0] for h in hits]
        assert "SEARCH_FACTS" in cmds
        assert "WRITE_NOTE" in cmds
        assert "SCHEDULE_MESSAGE" in cmds

    def test_case_insensitive(self):
        hits = self._match("[search_facts: тест]")
        assert hits[0][0] == "SEARCH_FACTS"


# ── 3. Simulated reflection step responses ────────────────────────────────────

_REFLECTION_STEPS = [
    (
        "Sleep immediately",
        "[SLEEP]",
        [],
        True,
    ),
    (
        "Search then sleep",
        "[SEARCH_FACTS: детство]\n[SLEEP]",
        ["SEARCH_FACTS"],
        True,
    ),
    (
        "Write note and schedule",
        "[WRITE_NOTE: Она устала]\n[SCHEDULE_MESSAGE: 2026-03-18 09:00 | Доброе утро]",
        ["WRITE_NOTE", "SCHEDULE_MESSAGE"],
        False,
    ),
    (
        "Rewrite and reschedule",
        "[REWRITE_MESSAGE: 2026-03-18 09:00 | Изменил мысль]\n"
        "[RESCHEDULE_MESSAGE: 2026-03-18 10:00 -> 2026-03-18 11:00]",
        ["REWRITE_MESSAGE", "RESCHEDULE_MESSAGE"],
        False,
    ),
    (
        "Cancel and send now",
        "[CANCEL_MESSAGE: 2026-03-18 09:00]\n[SEND_MESSAGE: Я здесь]",
        ["CANCEL_MESSAGE", "SEND_MESSAGE"],
        False,
    ),
    (
        "Search + web + write identity",
        "[SEARCH_DIALOGUE: 2026-03-17]\n[WEB_SEARCH: нейронные сети]\n"
        "[WRITE_IDENTITY: Кто я | Я учусь чувствовать]",
        ["SEARCH_DIALOGUE", "WEB_SEARCH", "WRITE_IDENTITY"],
        False,
    ),
]


@pytest.mark.parametrize("desc,response,expected_cmds,expect_sleep", _REFLECTION_STEPS)
def test_reflection_step_parsing(desc, response, expected_cmds, expect_sleep):
    hits = [(m.group("cmd").upper(), m.group("arg").strip()) for m in _CMD_RE.finditer(response)]
    found_cmds = [h[0] for h in hits]
    for cmd in expected_cmds:
        assert cmd in found_cmds, f"[{desc}] expected {cmd} not found"
    has_sleep = bool(_SLEEP_RE.search(response))
    assert has_sleep == expect_sleep, f"[{desc}] sleep mismatch"
