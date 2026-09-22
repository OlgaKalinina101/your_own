"""Tests for post_analyzer prompt assembly and command parsing.

Run with:
    cd C:\\Users\\Alien\\PycharmProjects\\your_own
    python -m pytest tests/autonomy/test_post_analyzer.py -v

No database, no LLM — everything is tested in pure Python.
"""
from __future__ import annotations

import sys
import os

# Make sure the project root is on the path when running directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest
from infrastructure.autonomy.cmd_parser import (
    CancelMessage,
    RescheduleMessage,
    RewriteMessage,
    ScheduleMessage,
    SendMessage,
    parse_commands,
    strip_commands,
)
from infrastructure.llm.prompt_loader import get_prompt

_PROMPTS = "infrastructure/autonomy/prompts/post_analyzer.md"


# ── 1. Prompt loading ─────────────────────────────────────────────────────────

class TestPromptLoading:
    """Verify that the prompt file loads and all placeholders resolve."""

    def _build(self, lang: str) -> tuple[str, str]:
        system = get_prompt(_PROMPTS, lang=lang, section="system",
                            ai_name="Victor")
        user = get_prompt(
            _PROMPTS, lang=lang, section="user",
            ai_name="Victor",
            message_history="User: Привет\nAssistant: Привет!",
            current_time="2026-03-17 22:00",
            identity="Я — Victor. Я забочусь о тебе.",
            workbench="[2026-03-17 21:00] Она устала.",
            open_threads="1. [#a1b2 · 3 дн без изменений] Ютуб — вернуть",
            pending_pushes_block="",
            people="",
            timezone_label="Asia/Yerevan",
        )
        return system, user

    def test_ru_loads(self):
        system, user = self._build("ru")
        assert "Victor" in system
        assert "2026-03-17 22:00" in user
        assert "Привет" in user
        assert "SKIP" in user

    def test_en_loads(self):
        system, user = self._build("en")
        assert "Victor" in system
        assert "2026-03-17 22:00" in user
        assert "SKIP" in user

    def test_ru_has_all_commands(self):
        _, user = self._build("ru")
        for cmd in ["SCHEDULE_MESSAGE", "CANCEL_MESSAGE",
                    "RESCHEDULE_MESSAGE", "REWRITE_MESSAGE",
                    "PIN_THREAD", "UNPIN_THREAD", "UPDATE_THREAD", "ABOUT", "FORGET"]:
            assert cmd in user, f"Command {cmd} missing from RU prompt"

    def test_en_has_all_commands(self):
        _, user = self._build("en")
        for cmd in ["SCHEDULE_MESSAGE", "CANCEL_MESSAGE",
                    "RESCHEDULE_MESSAGE", "REWRITE_MESSAGE",
                    "PIN_THREAD", "UNPIN_THREAD", "UPDATE_THREAD", "ABOUT", "FORGET"]:
            assert cmd in user, f"Command {cmd} missing from EN prompt"

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_board_hint_says_what_a_thread_is_and_where_the_rest_goes(self, lang):
        """By 22.09 the board held 34 threads averaging 533 characters, each a
        chronicle rewritten 82 times in ten days from here, carrying people,
        lessons and calendars — because the old hint said only «нельзя
        уронить» and never said where else anything could go."""
        _, user = self._build(lang)
        if lang == "ru":
            assert "одна-две фразы" in user and "переписать её, а не дописать" in user
            assert "Факт о человеке — в карточку, [ABOUT]" in user
            assert "Нить без следующего хода — не нить" in user
            assert "нельзя уронить" not in user
        else:
            assert "one or two sentences" in user and "rewriting it, not appending" in user
            assert "goes on their card, [ABOUT]" in user
            assert "A thread with no next move is not a thread" in user
            assert "must not drop" not in user

    def test_pending_pushes_block_injected(self):
        user = get_prompt(
            _PROMPTS, lang="ru", section="user",
            ai_name="Victor",
            message_history="User: Привет\nAssistant: Привет!",
            current_time="2026-03-17 22:00",
            identity="...",
            workbench="...",
            open_threads="(пусто)",
            people="(пусто)",
            pending_pushes_block="Запланированные: [22:30] «Привет»",
            timezone_label="Asia/Yerevan",
        )
        assert "Запланированные" in user


# ── 2. Command parser — individual commands ───────────────────────────────────

class TestParseCommands:

    def test_send_message(self):
        text = "[SEND_MESSAGE: Привет, я скучал]"
        cmds = parse_commands(text)
        assert len(cmds) == 1
        assert isinstance(cmds[0], SendMessage)
        assert cmds[0].text == "Привет, я скучал"

    def test_schedule_message(self):
        text = "[SCHEDULE_MESSAGE: 2026-03-18 09:00 | Доброе утро ❤️]"
        cmds = parse_commands(text)
        assert len(cmds) == 1
        c = cmds[0]
        assert isinstance(c, ScheduleMessage)
        assert c.ts_str == "2026-03-18 09:00"
        assert "Доброе утро" in c.text

    def test_cancel_message(self):
        text = "[CANCEL_MESSAGE: 2026-03-18 09:00]"
        cmds = parse_commands(text)
        assert len(cmds) == 1
        c = cmds[0]
        assert isinstance(c, CancelMessage)
        assert c.ts_str == "2026-03-18 09:00"

    def test_reschedule_message(self):
        text = "[RESCHEDULE_MESSAGE: 2026-03-18 09:00 -> 2026-03-18 11:00]"
        cmds = parse_commands(text)
        assert len(cmds) == 1
        c = cmds[0]
        assert isinstance(c, RescheduleMessage)
        assert c.old_ts_str == "2026-03-18 09:00"
        assert c.new_ts_str == "2026-03-18 11:00"

    def test_rewrite_message(self):
        text = "[REWRITE_MESSAGE: 2026-03-18 09:00 | Новый текст сообщения]"
        cmds = parse_commands(text)
        assert len(cmds) == 1
        c = cmds[0]
        assert isinstance(c, RewriteMessage)
        assert c.ts_str == "2026-03-18 09:00"
        assert c.new_text == "Новый текст сообщения"

    def test_underscore_variant(self):
        """Both SEND_MESSAGE and SEND MESSAGE should parse."""
        text = "[SEND MESSAGE: Hello]"
        cmds = parse_commands(text)
        assert len(cmds) == 1
        assert isinstance(cmds[0], SendMessage)

    def test_case_insensitive(self):
        text = "[schedule_message: 2026-03-18 10:00 | тест]"
        cmds = parse_commands(text)
        assert len(cmds) == 1
        assert isinstance(cmds[0], ScheduleMessage)


# ── 3. Multiple commands in one response ──────────────────────────────────────

class TestMultipleCommands:

    def test_order_preserved(self):
        text = (
            "Думаю о ней...\n"
            "[CANCEL_MESSAGE: 2026-03-18 09:00]\n"
            "[SCHEDULE_MESSAGE: 2026-03-18 11:00 | Перенёс]\n"
            "Всё верно."
        )
        cmds = parse_commands(text)
        assert len(cmds) == 2
        assert isinstance(cmds[0], CancelMessage)
        assert isinstance(cmds[1], ScheduleMessage)

    def test_note_plus_command(self):
        response = (
            "Она устала сегодня. Хочу написать ей утром.\n"
            "[SCHEDULE_MESSAGE: 2026-03-18 08:00 | Доброе утро]"
        )
        cmds = parse_commands(response)
        note = strip_commands(response)
        assert len(cmds) == 1
        assert isinstance(cmds[0], ScheduleMessage)
        assert "Она устала" in note
        assert "SCHEDULE_MESSAGE" not in note

    def test_only_note_no_commands(self):
        response = "Сегодня был хороший разговор."
        cmds = parse_commands(response)
        assert cmds == []
        assert strip_commands(response) == response

    def test_all_five_in_one(self):
        response = (
            "[SEND_MESSAGE: Привет]\n"
            "[SCHEDULE_MESSAGE: 2026-03-18 09:00 | Утро]\n"
            "[CANCEL_MESSAGE: 2026-03-18 10:00]\n"
            "[RESCHEDULE_MESSAGE: 2026-03-18 11:00 -> 2026-03-18 12:00]\n"
            "[REWRITE_MESSAGE: 2026-03-18 12:00 | Изменённый текст]"
        )
        cmds = parse_commands(response)
        types = [type(c) for c in cmds]
        assert SendMessage in types
        assert ScheduleMessage in types
        assert CancelMessage in types
        assert RescheduleMessage in types
        assert RewriteMessage in types


# ── 4. strip_commands ─────────────────────────────────────────────────────────

class TestStripCommands:

    def test_removes_all_brackets(self):
        response = (
            "Заметка.\n"
            "[SCHEDULE_MESSAGE: 2026-03-18 09:00 | текст]\n"
            "[CANCEL_MESSAGE: 2026-03-18 10:00]\n"
            "Ещё заметка."
        )
        clean = strip_commands(response)
        assert "SCHEDULE_MESSAGE" not in clean
        assert "CANCEL_MESSAGE" not in clean
        assert "Заметка." in clean
        assert "Ещё заметка." in clean

    def test_skip_response(self):
        assert strip_commands("SKIP") == "SKIP"

    def test_empty_response(self):
        assert strip_commands("") == ""


# ── 5. Simulated LLM responses → expected parse results ──────────────────────

_SIMULATED_RESPONSES = [
    # (description, response_text, expected_cmd_types, expected_note_contains)
    (
        "SKIP",
        "SKIP",
        [],
        "SKIP",
    ),
    (
        "Only a note",
        "Она сегодня звучала уставшей. Надо дать ей отдохнуть.",
        [],
        "уставшей",
    ),
    (
        "Note + SCHEDULE",
        "Хочу написать утром.\n[SCHEDULE_MESSAGE: 2026-03-18 08:30 | Доброе утро ❤️]",
        [ScheduleMessage],
        "Хочу написать",
    ),
    (
        "Note + SEND",
        "Не могу удержаться.\n[SEND_MESSAGE: Я думаю о тебе]",
        [SendMessage],
        "удержаться",
    ),
    (
        "REWRITE existing plan",
        "Мысль изменилась.\n[REWRITE_MESSAGE: 2026-03-18 09:00 | Новое сообщение]",
        [RewriteMessage],
        "изменилась",
    ),
    (
        "CANCEL + reschedule",
        "[CANCEL_MESSAGE: 2026-03-18 09:00]\n[SCHEDULE_MESSAGE: 2026-03-18 12:00 | Позже]",
        [CancelMessage, ScheduleMessage],
        "",
    ),
]


@pytest.mark.parametrize("desc,response,expected_types,note_fragment", _SIMULATED_RESPONSES)
def test_simulated_llm_response(desc, response, expected_types, note_fragment):
    cmds = parse_commands(response)
    note = strip_commands(response)
    assert [type(c) for c in cmds] == expected_types, f"[{desc}] wrong command types"
    if note_fragment:
        assert note_fragment in note, f"[{desc}] note missing expected fragment"


class TestTheBookFromTheJournal:
    """[ABOUT] and [FORGET] parse into typed commands and strip from the note."""

    def test_about_parses_with_its_two_halves(self):
        from infrastructure.autonomy.cmd_parser import About

        parsed = parse_commands("Брат. [ABOUT: Шурин | младший брат, в армии, звонит ~21:00]")
        assert parsed == [About(who="Шурин", fact="младший брат, в армии, звонит ~21:00")]

    def test_forget_with_and_without_words(self):
        from infrastructure.autonomy.cmd_parser import Forget

        assert parse_commands("[FORGET: Шурин | в армии]") == [Forget(who="Шурин", fragment="в армии")]
        assert parse_commands("[FORGET: Шурин]") == [Forget(who="Шурин", fragment="")]

    def test_both_are_stripped_from_the_note(self):
        note = strip_commands("Она рассказала про брата. [ABOUT: Шурин | в армии]\n[FORGET: Гор | йога]")
        assert note == "Она рассказала про брата."

    def test_the_two_names_are_shared_with_the_sanitiser(self):
        from infrastructure.autonomy.commands import LEAKABLE_COMMANDS, NAMES
        from infrastructure.autonomy.cmd_parser import About, Forget

        assert NAMES[About] == "ABOUT" and NAMES[Forget] == "FORGET"
        assert "ABOUT" in LEAKABLE_COMMANDS and "FORGET" in LEAKABLE_COMMANDS
