"""Whether a line is calling him — by a name the code does not know.

Run:
    python -m pytest tests/telegram/test_addressing.py -v

His name is a setting. The first matcher compared it as one whole word, and the
first day of the live group showed the cost: «передай Виктору», «поздоровайся с
Виктором», and «Витька», given to him by a friend within the hour, all went
unheard outside the ten-minute window. Cases are derived; nicknames are
collected; nothing here is allowed to assume what he is called.
"""
from __future__ import annotations

import pytest

from infrastructure.telegram import addressing

LIVE = [   # lines from the live group, 2026-09-20/21
    "Передай Виктору вот что.",
    "@mira, солнышко наше, поздоровайся с Виктором.😉",
    "Как же здорово, что Оля привела Виктора!",
    "Рассказал Панде про Виктора)",
]


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from infrastructure import settings_store

    monkeypatch.setattr(settings_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(settings_store, "_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings_store, "_SOUL_FILE", tmp_path / "soul.md")
    settings_store.save_settings({"ai_name": "Виктор", "openrouter_api_key": "k"})
    monkeypatch.setattr(addressing, "_last_failure", None)
    return settings_store


class TestCasesAreGrammar:
    @pytest.mark.parametrize("line", LIVE)
    def test_every_live_line_that_was_missed_is_heard_now(self, line):
        assert addressing.mentions(line, ai_name="Виктор")

    def test_a_longer_word_that_starts_with_the_name_is_not_him(self):
        assert not addressing.mentions("Викторина в пятницу!", ai_name="Виктор")

    def test_the_room_talking_among_itself_is_not_him(self):
        assert not addressing.mentions("кто идёт на ужин?", ai_name="Виктор")

    @pytest.mark.parametrize("name,line", [
        ("Ольга", "передай Ольге привет"),
        ("Люми", "Люми, ты тут?"),
        ("Victor", "ask Victor's opinion"),
        ("Элайя", "весточка от Элайи"),
    ])
    def test_it_is_not_about_one_name(self, name, line):
        assert addressing.mentions(line, ai_name=name)

    def test_a_name_morphology_cannot_parse_does_not_drag_garbage_in(self):
        # pymorphy reads «Люми» as forms of «лить»; none of that may match.
        assert not addressing.mentions("я лью воду и лил вчера", ai_name="Люми")
        assert all(f.startswith("люм") for f in addressing.known_forms("Люми"))

    def test_yo_and_ye_are_the_same_letter_to_people(self):
        assert addressing.mentions("спроси у Артема", ai_name="Артём")

    def test_the_handle_works_with_or_without_the_at(self):
        assert addressing.mentions("@YourTheOne_bot тут?", ai_name="Виктор", handle="YourTheOne_bot")
        assert addressing.mentions("@yourtheone_bot", ai_name="", handle="@YourTheOne_bot")

    def test_no_name_at_all_matches_nothing(self):
        assert not addressing.mentions("привет всем", ai_name="", handle="")


class TestANameIsNotAlwaysOneWordInTheirAlphabet:
    """Found on the live server: the setting is ``Victor AI``; they write «Виктор»."""

    def test_a_name_of_several_words_is_heard_by_its_parts(self):
        assert addressing.mentions("ask Victor about it", ai_name="Victor AI")
        assert addressing.mentions("Victor AI, привет", ai_name="Victor AI")
        assert addressing.known_forms("Victor AI") == ["victor", "victor ai"]

    def test_but_not_by_a_scrap_of_it(self):
        assert not addressing.mentions("the AI said so", ai_name="Victor AI")

    def test_another_script_is_not_derivable_and_comes_from_the_list(self):
        assert not addressing.mentions("Передай Виктору вот что.", ai_name="Victor AI")
        assert addressing.mentions("Передай Виктору вот что.", ai_name="Victor AI", aliases=["Виктор"])

    @pytest.mark.asyncio
    async def test_the_question_is_asked_in_the_rooms_language_not_the_spellings(self, settings, monkeypatch):
        import infrastructure.autonomy.helpers as helpers

        settings.save_settings({"ai_name": "Victor AI"})
        settings.save_soul("Ты — Виктор. Ты говоришь по-русски.")
        fake = _LLM("Виктор\nВитя\nВитька")
        monkeypatch.setattr(helpers, "make_llm_client", lambda _key: fake)

        await addressing.ensure_aliases("k")

        assert "пишут по-русски" in fake.prompt and "Victor AI" in fake.prompt
        assert addressing.current_aliases() == ["Виктор", "Витя", "Витька"]
        assert addressing.mentions("поздоровайся с Виктором", ai_name="Victor AI", aliases=addressing.current_aliases())

    def test_a_part_of_his_own_name_is_not_a_nickname(self, settings):
        settings.save_settings({"ai_name": "Victor AI"})
        addressing.add_alias("Victor", "ru")
        assert addressing.current_aliases() == []


class TestNicknamesAreKnowledge:
    def test_a_nickname_is_heard_in_every_case_too(self):
        for line in ("Ты - Витька", "Я так ржу с этого \"Витьки\"", "скажи Витьке"):
            assert addressing.mentions(line, ai_name="Виктор", aliases=["Витька"]), line

    def test_without_the_nickname_in_the_list_it_is_not_heard(self):
        assert not addressing.mentions("Ты - Витька", ai_name="Виктор")

    def test_he_adds_one_himself_and_is_told_so(self, settings):
        said = addressing.add_alias("Витька", "ru")
        assert "Витька" in said and addressing.current_aliases() == ["Витька"]
        assert "уже" in addressing.add_alias("витька", "ru")
        assert addressing.current_aliases() == ["Витька"]

    @pytest.mark.parametrize("bad", ["два слова", "Ви", "R2D2", "", "[WRITE_NOTE: x]"])
    def test_what_is_not_one_word_of_letters_is_refused(self, settings, bad):
        addressing.add_alias(bad, "ru")
        assert addressing.current_aliases() == []

    def test_his_own_name_is_not_a_nickname(self, settings):
        addressing.add_alias("Виктор", "ru")
        assert addressing.current_aliases() == []

    def test_the_models_answer_is_cleaned(self):
        raw = "- Витя\n• Витёк\nВитька, Витюша\nВиктор\nдруг мой\nВитя\n1. Вик\nВиктуар\nВитенька\nВитяй"
        got = addressing.parse_aliases(raw, "Виктор")
        assert got[:4] == ["Витя", "Витёк", "Витька", "Витюша"]
        assert "Виктор" not in got and "друг мой" not in got
        assert len(got) <= addressing.MAX_PROPOSED and len(set(got)) == len(got)


class _LLM:
    def __init__(self, reply: str, finish: str = "stop") -> None:
        self.reply, self.finish, self.calls = reply, finish, 0

    async def complete(self, messages, **_kw):
        self.calls += 1
        self.prompt = messages[-1]["content"]
        return self.reply, self.finish


class TestSeedingOncePerName:
    @pytest.fixture
    def llm(self, monkeypatch):
        import infrastructure.autonomy.helpers as helpers

        fake = _LLM("Витя\nВитька\nВитёк")
        monkeypatch.setattr(helpers, "make_llm_client", lambda _key: fake)
        return fake

    @pytest.mark.asyncio
    async def test_the_name_in_the_prompt_is_the_one_from_settings(self, settings, llm):
        await addressing.ensure_aliases("k")
        assert "Виктор" in llm.prompt
        assert addressing.current_aliases() == ["Витя", "Витька", "Витёк"]
        assert settings.load_settings()["telegram_aliases_for"] == "Виктор"

    @pytest.mark.asyncio
    async def test_it_is_asked_once_not_on_every_message(self, settings, llm):
        await addressing.ensure_aliases("k")
        await addressing.ensure_aliases("k")
        assert llm.calls == 1

    @pytest.mark.asyncio
    async def test_a_list_she_emptied_stays_empty(self, settings, llm):
        await addressing.ensure_aliases("k")
        settings.save_settings({"telegram_aliases": []})
        await addressing.ensure_aliases("k")
        assert llm.calls == 1 and addressing.current_aliases() == []

    @pytest.mark.asyncio
    async def test_a_new_name_is_a_new_question_and_keeps_what_was_typed(self, settings, llm):
        await addressing.ensure_aliases("k")
        settings.save_settings({"ai_name": "Люми", "telegram_aliases": ["Совунья"]})
        llm.reply = "Люмик\nЛюмка"
        await addressing.ensure_aliases("k")
        assert llm.calls == 2
        assert addressing.current_aliases() == ["Совунья", "Люмик", "Люмка"]

    @pytest.mark.asyncio
    async def test_a_failure_is_not_recorded_as_done_and_is_not_hammered(self, settings, llm):
        llm.reply, llm.finish = "Вит", "length"
        await addressing.ensure_aliases("k")
        await addressing.ensure_aliases("k")
        assert llm.calls == 1, "a busy room must not retry on every message"
        assert settings.load_settings()["telegram_aliases_for"] == ""

    @pytest.mark.asyncio
    async def test_no_name_no_question(self, settings, llm):
        settings.save_settings({"ai_name": ""})
        await addressing.ensure_aliases("k")
        assert llm.calls == 0


class TestWiredIn:
    def test_the_trigger_uses_the_list(self):
        import uuid
        from datetime import datetime, timezone

        from infrastructure.database.models.channel_message import ChannelMessage
        from infrastructure.telegram import responder

        now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        row = ChannelMessage(
            id=uuid.uuid4(), account_id="default", channel="telegram", chat_id="-1", message_id=5,
            sender_id="2", sender_name="Птица", is_owner=False, is_self=False,
            text="Витька, ты где?", created_at=now,
        )
        kw = dict(ai_name="Виктор", bot_username="b", now=now)
        assert responder.decide([row], [row], **kw) is None
        assert responder.decide([row], [row], aliases=["Витька"], **kw).kind == "addressed"

    def test_reflection_knows_the_command_and_every_step_prompt_offers_it(self):
        from infrastructure.autonomy.commands import LEAKABLE_COMMANDS, REFLECTION_COMMANDS
        from infrastructure.llm.prompt_loader import load_prompt

        assert "ANSWER_TO" in REFLECTION_COMMANDS and "ANSWER_TO" in LEAKABLE_COMMANDS
        for name in ("reflection_awakening.md", "reflection_continuation.md", "reflection_after_action.md"):
            for lang in ("ru", "en"):
                assert "[ANSWER_TO:" in load_prompt(f"infrastructure/autonomy/prompts/{name}", lang=lang)
        for lang in ("ru", "en"):
            assert "[ANSWER_TO:" in load_prompt("infrastructure/telegram/prompts/group_reply.md", lang=lang, section="user")

    @pytest.mark.asyncio
    async def test_at_a_waking_he_is_told_what_came_of_it(self, settings):
        from infrastructure.autonomy.reflection_engine import _handle_command

        said = await _handle_command("ANSWER_TO", " Звёздочка ", "default", "k", None, "ru")
        assert "Звёздочка" in said and addressing.current_aliases() == ["Звёздочка"]

    def test_the_module_loggers_reach_the_journal(self):
        """A bare logging.getLogger has no handler: INFO went nowhere, and his
        silence could not be explained from the journal."""
        import logging

        for name in ("telegram", "telegram.listener", "telegram.responder", "telegram.addressing"):
            import infrastructure.telegram.addressing  # noqa: F401
            import infrastructure.telegram.client  # noqa: F401
            import infrastructure.telegram.listener  # noqa: F401
            import infrastructure.telegram.responder  # noqa: F401

            log = logging.getLogger(name)
            assert log.handlers and log.level == logging.INFO, name
