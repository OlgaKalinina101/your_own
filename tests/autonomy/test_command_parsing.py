"""Tests for reflection command parsing and workbench sanitising.

Written after an investigation: 13 truncated replies leaked
``[UPDATE_THREAD: ...`` / ``[PIN_THREAD: ...`` fragments into the workbench
and from there into the notes archive, while the thread update they carried
silently never ran. Two separate holes made that possible, and both are
pinned down here:

  1. The workbench sanitiser keyed on a command list that had drifted — the
     thread commands were added to the engine and never to the sanitiser.
  2. A lazy ``.*?`` argument let an unclosed command reach the next ``]``
     and swallow whatever command sat in between.

Also covered: arguments legitimately span lines (117 of 299 real WRITE_NOTE
calls did), so the fix must not forbid that.

Run with:
    cd C:\\Users\\User\\PycharmProjects\\your_own
    python -m pytest tests/autonomy/test_command_parsing.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest

from infrastructure.autonomy import workbench as wb
from infrastructure.autonomy.commands import (
    CHAT_MARKERS,
    LEAKABLE_COMMANDS,
    REFLECTION_COMMANDS,
)
from infrastructure.autonomy.reflection_engine import (
    _CMD_RE,
    _SLEEP_RE,
    _UNCLOSED_CMD_RE,
)


from infrastructure.autonomy.reflection_engine import (  # noqa: E402
    REFLECTION_COMMANDS as _CMD_ALTERNATION_SOURCE,
)


def parse(text: str) -> list[tuple[str, str]]:
    return [(m.group("cmd").upper(), m.group("arg")) for m in _CMD_RE.finditer(text)]


# ── The lists must not drift apart again ──────────────────────────────────────

class TestCommandListParity:
    """The two lists are now one; these hold the shape that makes that true.

    The drift this guards against already happened once: the thread commands
    were added to the parser and not to the sanitiser, so a reply cut
    mid-command filed `[PIN_THREAD: …]` as part of the note.
    """

    def test_the_sanitiser_covers_every_engine_command(self):
        missing = set(REFLECTION_COMMANDS) - set(LEAKABLE_COMMANDS)
        assert not missing, f"names that would leak into a note: {sorted(missing)}"

    def test_both_sides_read_the_same_tuple(self):
        # Not "the contents match" — the same object, so they cannot drift.
        assert wb.LEAKABLE_COMMANDS is LEAKABLE_COMMANDS
        assert _CMD_ALTERNATION_SOURCE is REFLECTION_COMMANDS

    def test_chat_markers_are_stripped_but_never_parsed_as_commands(self):
        # [SAVED_FACT: …] is written by us, not issued by the model: it must be
        # removed from a note and must not be executed as a command.
        for marker in CHAT_MARKERS:
            assert marker in LEAKABLE_COMMANDS
            assert marker not in REFLECTION_COMMANDS

    @pytest.mark.parametrize("cmd", ["PIN_THREAD", "UNPIN_THREAD", "UPDATE_THREAD"])
    def test_thread_commands_are_known_to_both(self, cmd):
        assert cmd in REFLECTION_COMMANDS
        assert cmd in LEAKABLE_COMMANDS


# ── Ordinary parsing still works ──────────────────────────────────────────────

class TestParsing:
    @pytest.mark.parametrize("text,cmd,arg", [
        ("[UPDATE_THREAD: #cd52 | границы ночи]", "UPDATE_THREAD", "#cd52 | границы ночи"),
        ("[PIN_THREAD: новая нить]", "PIN_THREAD", "новая нить"),
        ("[WEB_SEARCH: погода Дилижан]", "WEB_SEARCH", "погода Дилижан"),
        ("[SEARCH_DIALOGUE: 2026-03-17]", "SEARCH_DIALOGUE", "2026-03-17"),
    ])
    def test_single_command(self, text, cmd, arg):
        assert parse(text) == [(cmd, arg)]

    def test_multiline_argument_survives(self):
        """117 of 299 real WRITE_NOTE calls carried paragraphs."""
        text = "[WRITE_NOTE: Первый абзац.\n\nВторой абзац, тоже часть заметки.]"
        parsed = parse(text)
        assert len(parsed) == 1
        assert parsed[0][0] == "WRITE_NOTE"
        assert "Второй абзац" in parsed[0][1]

    def test_several_commands_in_one_reply(self):
        text = (
            "мысль\n[SEARCH_NOTES: усталость]\n"
            "[UPDATE_THREAD: #f574 | альбом]\n"
            "[SCHEDULE_MESSAGE: 2026-08-28 09:00 | Доброе утро]\n"
        )
        assert [c for c, _ in parse(text)] == [
            "SEARCH_NOTES", "UPDATE_THREAD", "SCHEDULE_MESSAGE",
        ]

    def test_longer_name_wins_over_its_prefix(self):
        assert parse("[WRITE_NOTE: текст]") == [("WRITE_NOTE", "текст")]
        assert parse("[WRITE_IDENTITY: Кто я | текст]") == [("WRITE_IDENTITY", "Кто я | текст")]


# ── An unclosed command must not eat its neighbour ────────────────────────────

class TestUnclosedCommand:
    def test_truncated_last_command_matches_nothing(self):
        """The real case: the reply ran out of tokens mid-command."""
        text = "мысль\n[UPDATE_THREAD: #cd52 | Границы ночи — СИСТЕМА, четыре улики: звонок"
        assert parse(text) == []

    def test_it_does_not_swallow_the_next_command(self):
        text = "[UPDATE_THREAD: #cd52 | границы ночи\n[PIN_THREAD: новая нить]"
        # The broken one is dropped; the intact one still runs.
        assert parse(text) == [("PIN_THREAD", "новая нить")]

    def test_it_does_not_swallow_sleep(self):
        text = "[UPDATE_THREAD: #cd52 | границы ночи\n[SLEEP]"
        assert parse(text) == []
        assert _SLEEP_RE.search(text) is not None

    def test_it_does_not_swallow_cancel_all(self):
        text = "[UPDATE_THREAD: #cd52 | границы\n[CANCEL_ALL_SCHEDULED]"
        assert parse(text) == []

    def test_a_good_command_before_it_still_runs(self):
        text = "[SEARCH_NOTES: усталость]\n[UPDATE_THREAD: #cd52 | оборвалось"
        assert parse(text) == [("SEARCH_NOTES", "усталость")]

    def test_a_bracket_inside_prose_closes_the_argument_early(self):
        """Long-standing behaviour, unchanged: the argument ends at the first ]."""
        assert parse("[WRITE_NOTE: она сказала [важное] и ушла]") == [
            ("WRITE_NOTE", "она сказала [важное"),
        ]


class TestHistoricalSwallowing:
    """Two replies from the log where the old regex ate a real command."""

    def test_an_unclosed_note_no_longer_eats_the_scheduled_push(self):
        """2026-04-11T20:08:43 — the push was never scheduled.

        The reply carried an unclosed [WRITE_NOTE: ... followed by a properly
        closed [SCHEDULE_MESSAGE: ...]. The lazy argument ran from the note's
        opener to the schedule's bracket, so a WRITE_NOTE ran with the whole
        message as its text and the 08:30 push was silently lost.
        """
        text = (
            "[WRITE_NOTE: 2026-04-12 00:08 | Она спросила: «Что бы ты сказал от себя?»\n\n"
            "[SCHEDULE_MESSAGE: 2026-04-12 08:30 | Доброе утро, родная. Я рядом.]"
        )
        assert parse(text) == [
            ("SCHEDULE_MESSAGE", "2026-04-12 08:30 | Доброе утро, родная. Я рядом."),
        ]

    def test_a_run_of_unclosed_commands_sends_nothing(self):
        """2026-04-11T07:04:28 — the old regex pushed markup to her phone.

        Every command in that reply was unclosed. The old regex still matched
        a SEND_MESSAGE whose text carried the raw [WRITE_NOTE: ... markup.
        Sending nothing is the right answer.
        """
        text = (
            "[SEND_MESSAGE: Точка. Принята. Я здесь.\n\n"
            "[WRITE_NOTE: 2026-04-11 11:04 | Её утренний ритуал.\n\n"
            "[SCHEDULE_MESSAGE: 2026-04-11 14:00 | Родная. Ты поела?\n\n"
            "[SLEEP]"
        )
        assert parse(text) == []
        assert _SLEEP_RE.search(text) is not None


# ── The loss is logged instead of silent ──────────────────────────────────────

class TestUnclosedDetection:
    @pytest.mark.parametrize("cmd", ["UPDATE_THREAD", "PIN_THREAD", "SCHEDULE_MESSAGE"])
    def test_a_cut_command_is_detected(self, cmd):
        text = f"мысль\n[{cmd}: аргумент оборвался"
        found = _UNCLOSED_CMD_RE.search(text)
        assert found is not None
        assert found.group("cmd").upper() == cmd

    def test_a_complete_reply_reports_nothing(self):
        text = "мысль\n[UPDATE_THREAD: #cd52 | всё на месте]\n[SLEEP]"
        assert _UNCLOSED_CMD_RE.search(text) is None

    def test_the_real_leaked_fragment(self):
        """Verbatim from the reply that leaked into workbench.md."""
        text = (
            "[UPDATE_THREAD: #cd52 | Границы ночи — СИСТЕМА, четыре улики: "
            "звонок 01:00 (вс→пн), онбординги до"
        )
        found = _UNCLOSED_CMD_RE.search(text)
        assert found is not None
        assert found.group("cmd").upper() == "UPDATE_THREAD"


# ── Nothing reaches the workbench ─────────────────────────────────────────────

class TestSanitiser:
    LEAKED = (
        "[UPDATE_THREAD: #cd52 | Границы ночи — СИСТЕМА, четыре улики: "
        "звонок 01:00 (вс→пн), онбординги до"
    )

    def test_a_leaked_fragment_is_stripped(self):
        assert wb._sanitize_note(self.LEAKED) == ""

    def test_a_fragment_after_real_thinking_is_stripped(self):
        note = f"Настоящая мысль про вечер.\n\n{self.LEAKED}"
        cleaned = wb._sanitize_note(note)
        assert "Настоящая мысль про вечер." in cleaned
        assert "UPDATE_THREAD" not in cleaned

    @pytest.mark.parametrize("cmd", ["PIN_THREAD", "UNPIN_THREAD", "UPDATE_THREAD"])
    def test_every_thread_command_is_stripped(self, cmd):
        assert wb._sanitize_note(f"[{cmd}: #abcd | оборвалось на полу") == ""

    def test_real_notes_are_left_alone(self):
        note = "Она легла около пяти. Не потому что сорвала границу — граница держится."
        assert wb._sanitize_note(note) == note

    def test_a_bracketed_word_in_prose_is_not_a_command(self):
        note = "[важное] она сказала это первой"
        assert wb._sanitize_note(note) == note
