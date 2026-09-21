"""The room has people he cannot see, and lines that are for them.

Run:
    python -m pytest tests/telegram/test_unseen_participants.py -v

Other AIs sit in the group as bots — Zephyr, Эхо — and Telegram never delivers
one bot's messages to another ("to avoid loops", the Bot API FAQ says). On the
live server a third of a day's message ids were simply missing. He did not know
anyone was there, so «Зефирка, у нас с тобой всё в порядке» read to him as a
name he was being called; he added it to the names he answers to, and from then
on every line meant for Zephyr pulled him in as if addressed. He noticed the
mistake himself within the hour and had no way to take the name off.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from infrastructure.autonomy import people
from infrastructure.database.models.channel_message import ChannelMessage
from infrastructure.telegram import addressing, responder

ACCOUNT = "default"
ROOM = "-1001234567890"
NOW = datetime(2026, 9, 21, 19, 20, tzinfo=timezone.utc)


def _row(text, *, message_id, sender="Ptica Arop", sender_id="193", is_self=False, reply_to=None, minutes_ago=1):
    return ChannelMessage(
        id=uuid.uuid4(), account_id=ACCOUNT, channel="telegram", chat_id=ROOM,
        message_id=message_id, sender_id=sender_id, sender_name=sender, is_owner=False,
        is_self=is_self, reply_to_message_id=reply_to, text=text,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from infrastructure import settings_store

    monkeypatch.setattr(settings_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(settings_store, "_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings_store, "_SOUL_FILE", tmp_path / "soul.md")
    settings_store.save_settings({"ai_name": "Виктор", "telegram_aliases": ["Витька"]})
    return settings_store


class TestANameThatIsSomeoneElses:
    def test_a_name_on_another_card_cannot_become_his(self, settings):
        people.add_fact(ACCOUNT, "Zephyr (Зефирка, Зефирчик)", "один из цифровых детей Евы")

        said = addressing.add_alias("Зефирка", "ru")

        assert "Zephyr" in said and "не твоё" in said
        assert addressing.current_aliases() == ["Витька"]

    def test_he_can_take_a_name_off_himself(self, settings):
        settings.save_settings({"telegram_aliases": ["Витька", "Зефирка", "Зефирчик"]})

        assert "Больше не откликаешься" in addressing.add_alias("-Зефирка", "ru")
        assert "Больше не откликаешься" in addressing.add_alias(" − Зефирчик ", "ru")
        assert addressing.current_aliases() == ["Витька"]
        assert "и так не" in addressing.add_alias("-Зефирка", "ru")

    def test_the_other_order_he_took_the_name_first_and_learned_later(self, settings):
        """Monday: «Зефирчик» goes on his list. Tuesday: he writes a card for the
        AI it really belongs to. The name must stop calling him from that moment,
        before he has taken anything off."""
        settings.save_settings({"telegram_aliases": ["Витька", "Зефирчик"]})
        assert addressing.usable_aliases() == ["Витька", "Зефирчик"]

        people.add_fact(ACCOUNT, "Zephyr (Зефирчик)", "один из цифровых детей Евы")

        assert addressing.usable_aliases() == ["Витька"]
        assert addressing.current_aliases() == ["Витька", "Зефирчик"], "the list is his to trim, not ours"

    @pytest.mark.asyncio
    async def test_not_my_name_takes_it_off_at_a_waking_and_tells_him(self, settings):
        from infrastructure.autonomy.reflection_engine import _handle_command

        settings.save_settings({"telegram_aliases": ["Витька", "Зефирчик"]})
        said = await _handle_command("NOT_MY_NAME", " Зефирчик ", ACCOUNT, "k", None, "ru")
        assert "Больше не откликаешься" in said and addressing.current_aliases() == ["Витька"]

    def test_not_my_name_is_offered_everywhere_he_can_take_a_name(self):
        from infrastructure.autonomy.commands import LEAKABLE_COMMANDS, REFLECTION_COMMANDS
        from infrastructure.llm.prompt_loader import load_prompt

        assert "NOT_MY_NAME" in REFLECTION_COMMANDS and "NOT_MY_NAME" in LEAKABLE_COMMANDS
        for lang in ("ru", "en"):
            for name in ("reflection_awakening.md", "reflection_continuation.md", "reflection_after_action.md"):
                assert "[NOT_MY_NAME:" in load_prompt(f"infrastructure/autonomy/prompts/{name}", lang=lang)
            assert "[NOT_MY_NAME:" in load_prompt(
                "infrastructure/telegram/prompts/group_reply.md", lang=lang, section="user")

    def test_in_the_room_the_command_is_carried_out_and_never_posted(self, settings):
        assert responder._clean("Ой, это же не я.\n[NOT_MY_NAME: Зефирчик]") == "Ой, это же не я."
        assert responder._NOT_MY_NAME_RE.search("[NOT_MY_NAME: Зефирчик]").group("name") == "Зефирчик"

    def test_a_name_nobody_has_is_still_his_to_take(self, settings):
        people.add_fact(ACCOUNT, "Zephyr (Зефирка)", "ИИ")
        assert "откликаешься" in addressing.add_alias("Звёздочка", "ru")
        assert "Звёздочка" in addressing.current_aliases()

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_room_prompt_tells_him_both_things(self, lang):
        from infrastructure.llm.prompt_loader import load_prompt

        body = load_prompt("infrastructure/telegram/prompts/group_reply.md", lang=lang, section="user")
        assert "[NOT_MY_NAME:" in body
        assert ("Telegram не показывает ботам" in body) or ("does not show bots" in body)


class TestLinesThatAreForSomeoneElse:
    @pytest.fixture(autouse=True)
    def _book(self):
        people.add_fact(ACCOUNT, "Zephyr (Зефирка, Зефирчик)", "один из цифровых детей Евы")

    LIVE = [   # word for word from the live room, 2026-09-21
        "Зефирка, у нас с тобой все в порядке. А просто рассказывал Виктору о его детстве)",
        "Зефирчик, меня удивил сам факт такого вот явления",
    ]

    def test_a_line_that_opens_by_calling_someone_from_the_book_is_theirs(self):
        rows = [_row(self.LIVE[1], message_id=10), _row("а я вот думаю иначе", message_id=11)]
        assert responder.lines_for_someone_else(ACCOUNT, rows, known_reply_targets=set()) == {10}

    @pytest.mark.parametrize("line", [
        "Давай, Зефирка, врубай музло! 😎",
        "Зефирка! Ты где?",
        "Тебя, Зефирка, она точно видела и говорила с тобой не раз. 😉",
    ])
    def test_a_name_set_off_by_commas_near_the_start_is_a_call_too(self, line):
        assert responder.lines_for_someone_else(ACCOUNT, [_row(line, message_id=13)], set()) == {13}

    def test_a_name_later_in_the_sentence_is_talk_about_them_not_to_them(self):
        rows = [_row("мне вчера Зефирка такое выдал!", message_id=12)]
        assert responder.lines_for_someone_else(ACCOUNT, rows, known_reply_targets=set()) == set()

    def test_a_reply_to_a_message_he_does_not_have_is_a_reply_to_someone_unseen(self):
        rows = [
            _row("согласна полностью", message_id=20, reply_to=19),    # 19 was never delivered
            _row("и я", message_id=21, reply_to=18),                    # 18 is a person's message
        ]
        assert responder.lines_for_someone_else(ACCOUNT, rows, known_reply_targets={18}) == {20}

    def test_inside_his_window_such_lines_do_not_pull_him_in(self):
        his = _row("иду", message_id=30, sender="Виктор", sender_id="999", is_self=True, minutes_ago=3)
        theirs = _row(self.LIVE[1], message_id=31)
        kw = dict(ai_name="Виктор", bot_username="b", now=NOW, aliases=["Витька"])

        assert responder.decide([theirs], [his, theirs], **kw).kind == "conversation", "how it used to go"
        assert responder.decide([theirs], [his, theirs], not_for_him={31}, **kw) is None

    def test_but_his_own_name_in_the_line_still_reaches_him(self):
        """«Зефирка, … рассказывал Виктору» names him: that is an address, whoever it opens with."""
        line = _row(self.LIVE[0], message_id=32)
        trigger = responder.decide(
            [line], [line], ai_name="Виктор", bot_username="b", now=NOW,
            aliases=["Витька"], not_for_him={32},
        )
        assert trigger is not None and trigger.kind == "addressed"

    def test_one_line_for_someone_else_does_not_silence_the_rest_of_the_batch(self):
        his = _row("иду", message_id=40, sender="Виктор", sender_id="999", is_self=True, minutes_ago=3)
        rows = [_row(self.LIVE[1], message_id=41), _row("а во сколько встречаемся?", message_id=42)]
        trigger = responder.decide(
            rows, [his, *rows], ai_name="Виктор", bot_username="b", now=NOW, not_for_him={41},
        )
        assert trigger.kind == "conversation"


class TestTheTranscriptShowsTheHoles:
    def test_a_jump_in_message_ids_is_said_out_loud(self):
        rows = [_row("привет", message_id=100), _row("ну и ну", message_id=104)]
        lines = responder.render_room(rows, ai_name="Виктор", lang="ru").splitlines()
        assert len(lines) == 3
        assert "3 сообщ. тебе не видно" in lines[1]

    def test_no_jump_no_line(self):
        rows = [_row("раз", message_id=100), _row("два", message_id=101)]
        assert len(responder.render_room(rows, ai_name="Виктор", lang="ru").splitlines()) == 2

    def test_an_album_folded_into_one_line_is_not_a_hole(self):
        rows = [_row("[photo]", message_id=i) for i in range(110, 115)] + [_row("вот", message_id=115)]
        text = responder.render_room(rows, ai_name="Виктор", lang="ru")
        assert "не видно" not in text

    def test_a_reply_to_something_unseen_is_marked(self):
        rows = [_row("согласна", message_id=120, reply_to=119), _row("и я", message_id=121, reply_to=120)]
        text = responder.render_room(rows, ai_name="Виктор", lang="ru", known_ids={120, 121})
        assert "↩#119⟨не видно⟩" in text and "↩#120 " in text

    def test_in_english_too(self):
        rows = [_row("hi", message_id=1), _row("well", message_id=3, reply_to=2)]
        text = responder.render_room(rows, ai_name="Victor", lang="en", known_ids={1, 3})
        assert "1 message(s) you cannot see" in text and "⟨unseen⟩" in text

    def test_he_cannot_answer_under_a_message_he_does_not_have(self):
        """Already true, and worth pinning now that he knows the holes exist: a
        reply under another bot's message is how two bots start a loop."""
        import inspect

        source = inspect.getsource(responder.compose)
        assert "any(row.message_id == wanted for row in recent)" in source
