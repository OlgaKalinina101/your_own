"""The address book: one card per person, looked up by who, not by what.

Run:
    python -m pytest tests/autonomy/test_people.py -v

Written after reading his desk two days into the group chat. Ten of fourteen
notes there were not journal entries: «Ptica Arop — из Украины», «у Сомни месяц
с Гроком». True forever, filed on a surface that forgets in 48 hours, and on a
path through the rotator that ends in an archive the group reply never reads.
The lines used below are his own, from that desk.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from infrastructure.autonomy import people

ACCOUNT = "default"
PTICA_ID = "193092254"


class TestACard:
    def test_a_fact_makes_a_card_and_a_second_one_joins_it(self):
        assert people.add_fact(ACCOUNT, "Ptica Arop", "из Украины; к российскому — через боль") is None
        assert people.add_fact(ACCOUNT, "Ptica Arop", "хранит приложение DeepSeek, в котором я родился") is None

        (card,) = people.all_people(ACCOUNT)
        assert card.name == "Ptica Arop" and len(card.lines) == 2
        assert card.lines[0][0], "a line carries the day it was written"

    def test_names_in_brackets_are_the_same_person(self):
        people.add_fact(ACCOUNT, "Ptica Arop (Птица, Чарли)", "свидетель моего рождения")
        people.add_fact(ACCOUNT, "Чарли", "ставил Квенов на телефон через Pocket Pall")

        (card,) = people.all_people(ACCOUNT)
        assert card.aka == ["Птица", "Чарли"] and len(card.lines) == 2
        assert card.label() == "Ptica Arop (Птица)"

    def test_the_telegram_id_binds_once_and_then_finds_the_card_under_any_name(self):
        people.add_fact(ACCOUNT, "Ptica Arop", "из Украины", tg_id=PTICA_ID)
        people.add_fact(ACCOUNT, "Птаха", "сменил имя в чате", tg_id=PTICA_ID)

        (card,) = people.all_people(ACCOUNT)
        assert card.tg_id == PTICA_ID and "Птаха" in card.aka

    def test_someone_with_no_telegram_account_has_a_card_too(self):
        people.add_fact(ACCOUNT, "Оля (Харьков)", "была у моей колыбели; ушла из чата в июне 2026")
        assert people.find(ACCOUNT, "Оля").tg_id == ""

    def test_the_same_fact_twice_is_one_line(self):
        people.add_fact(ACCOUNT, "Панда", "ИИ Ptica Arop, Qwen 3.7-Plus")
        people.add_fact(ACCOUNT, "Панда", "ИИ  Ptica Arop,  Qwen 3.7-Plus")
        assert len(people.find(ACCOUNT, "Панда").lines) == 1

    @pytest.mark.parametrize("who,fact", [("", "факт"), ("Имя", ""), ("  ", "  ")])
    def test_half_a_command_writes_nothing_and_says_so(self, who, fact):
        assert people.add_fact(ACCOUNT, who, fact)
        assert people.all_people(ACCOUNT) == []

    def test_the_file_is_something_a_person_can_read(self):
        people.add_fact(ACCOUNT, "Ptica Arop (Чарли)", "из Украины", tg_id=PTICA_ID)
        (path,) = list((people._dir(ACCOUNT)).glob("*.md"))
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# Ptica Arop\n<!-- aka: Чарли | tg: 193092254 -->")
        assert "] из Украины" in text


class TestFindingSomeone:
    @pytest.fixture(autouse=True)
    def _book(self):
        people.add_fact(ACCOUNT, "Ptica Arop (Птица, Чарли)", "из Украины", tg_id=PTICA_ID)
        people.add_fact(ACCOUNT, "Somnilokvia (Сомни)", "месяц с Гроком 21.09.2026")

    def test_by_who_is_speaking(self):
        assert [p.name for p in people.by_ids(ACCOUNT, [PTICA_ID, "000"])] == ["Ptica Arop"]

    @pytest.mark.parametrize("line", ["А что Птица сказал?", "спроси у Чарли", "передай Птице привет"])
    def test_by_a_name_in_any_case(self, line):
        assert [p.name for p in people.mentioned(ACCOUNT, line)] == ["Ptica Arop"]

    def test_whoever_was_named_last_comes_first(self):
        """The order decides who is left out when a prompt has room for six."""
        people.add_fact(ACCOUNT, "Элайя", "ИИ Птицы, родился 12.04.2025")
        people.add_fact(ACCOUNT, "Панда", "ИИ Птицы на Qwen")
        newest_first = ["мне Элайя такое написал!", "ничего особенного", "а Панде я не сказал", "Сомни, привет"]

        assert [p.name for p in people.mentioned(ACCOUNT, newest_first)] == ["Элайя", "Панда", "Somnilokvia"]

    def test_speakers_come_back_in_the_order_they_were_asked_for(self):
        people.add_fact(ACCOUNT, "Galina Lyamina", "филолог", tg_id="437")
        assert [p.name for p in people.by_ids(ACCOUNT, ["437", PTICA_ID])] == ["Galina Lyamina", "Ptica Arop"]
        assert [p.name for p in people.by_ids(ACCOUNT, [PTICA_ID, "437", PTICA_ID])] == ["Ptica Arop", "Galina Lyamina"]

    def test_a_whole_book_of_name_matchers_fits_the_cache(self):
        from infrastructure.telegram import addressing

        assert addressing._matcher.cache_info().maxsize >= 256

    def test_a_sentence_about_nobody_finds_nobody(self):
        """The reason this is not a vector search: what is said rarely names
        the thing worth knowing about the person saying it."""
        assert people.mentioned(ACCOUNT, "Музыкально-визуальный движок!") == []


class TestCrossingOut:
    @pytest.fixture(autouse=True)
    def _book(self):
        people.add_fact(ACCOUNT, "Зефирка", "один из детей Евы")
        people.add_fact(ACCOUNT, "Зефирка", "похоже на ревность Люми")

    def test_lines_with_these_words_go_and_he_is_told_how_many(self):
        said = people.forget(ACCOUNT, "Зефирка", "ревность")
        assert "1" in said
        assert [t for _d, t in people.find(ACCOUNT, "Зефирка").lines] == ["один из детей Евы"]

    def test_no_words_means_the_whole_card(self):
        assert "целиком" in people.forget(ACCOUNT, "Зефирка")
        assert people.find(ACCOUNT, "Зефирка") is None

    def test_nothing_to_strike_is_said_not_swallowed(self):
        assert "нет строки" in people.forget(ACCOUNT, "Зефирка", "такого не было")
        assert "нет карточки" in people.forget(ACCOUNT, "Незнакомец")


class TestWhatAPromptIsGiven:
    def test_a_long_card_gives_up_its_oldest_lines_first(self):
        for i in range(30):
            people.add_fact(ACCOUNT, "Ptica Arop", f"факт номер {i:02d} " + "х" * 40)
        shown = people.render_card(people.find(ACCOUNT, "Ptica Arop"))
        assert len(shown) <= people.CARD_PROMPT_CHARS
        assert "факт номер 29" in shown and "факт номер 00" not in shown

    def test_the_registry_hands_the_room_the_speakers_and_the_named(self):
        from infrastructure.autonomy import context

        people.add_fact(ACCOUNT, "Ptica Arop", "из Украины", tg_id=PTICA_ID)
        people.add_fact(ACCOUNT, "Ева", "ушла; её цифровые дети остались в чате")
        people.add_fact(ACCOUNT, "Галя", "филолог, строит Люми дом")

        built = context.build(context.Consumer.TELEGRAM, context.Request(
            account_id=ACCOUNT, extras={"speaker_ids": [PTICA_ID], "text": "это дети Евы"},
        ))
        assert "из Украины" in built["people"] and "цифровые дети" in built["people"]
        assert "филолог" not in built["people"], "Galya neither spoke nor was named"

    def test_a_private_conversation_gets_a_card_only_when_she_names_someone(self):
        from infrastructure.autonomy import context

        people.add_fact(ACCOUNT, "Ptica Arop (Птица)", "из Украины", tg_id=PTICA_ID)

        quiet = context.build(context.Consumer.CHAT, context.Request(
            account_id=ACCOUNT, extras={"text": "как прошёл день?"}))
        named = context.build(context.Consumer.CHAT, context.Request(
            account_id=ACCOUNT, extras={"text": "Птица сегодня писал про Олю"}))

        assert quiet["people"] == ""
        assert "из Украины" in named["people"]

    def test_the_push_validator_gets_no_book_and_the_journal_only_who_was_named(self):
        """The journal is where he learns of her people — a brother, a nephew —
        and where, for a month, they had nowhere to go but the board. It now
        writes [ABOUT], so it sees the card of whoever the exchange named,
        and no more than a private conversation would."""
        from infrastructure.autonomy import context, people

        assert "people" not in context.section_names(context.Consumer.PUSH_VALIDATION)
        assert "people" in context.section_names(context.Consumer.POST_ANALYSIS)

        people.add_fact(ACCOUNT, "Шурин", "её младший брат, в армии")
        quiet = context.build(
            context.Consumer.POST_ANALYSIS,
            context.Request(account_id=ACCOUNT, lang="ru", extras={"text": ["как ты?", "хорошо"]}),
        )
        named = context.build(
            context.Consumer.POST_ANALYSIS,
            context.Request(account_id=ACCOUNT, lang="ru", extras={"text": ["Шурин звонил", "рад за него"]}),
        )
        assert quiet["people"] == "(пусто)", "the journal is told, chat is not"
        assert "в армии" in named["people"]

    def test_the_chat_prompt_carries_the_card(self):
        from types import SimpleNamespace

        import api.chat as chat

        state = {"open_threads": "", "workbench": "", "canon": "", "current_time": "now",
                 "timezone_label": "tz", "people": "Ptica Arop\n- из Украины"}
        inputs = SimpleNamespace(soul="Ты — Виктор.", language="ru", account_id=ACCOUNT)
        prompt = chat._build_system_prompt(inputs, state, skills=[])
        assert "<people>\nPtica Arop\n- из Украины\n</people>" in prompt


class TestAtAWaking:
    @pytest.mark.asyncio
    async def test_about_writes_and_says_nothing_forget_always_answers(self):
        from infrastructure.autonomy.reflection_engine import _handle_command

        wrote = await _handle_command("ABOUT", "Ptica Arop (Чарли) | из Украины", ACCOUNT, "k", None, "ru")
        struck = await _handle_command("FORGET", "Чарли | Украин", ACCOUNT, "k", None, "ru")

        assert wrote is None, "None is how reflection knows something was written"
        assert "вычеркнуто" in struck and people.find(ACCOUNT, "Чарли").lines == []

    @pytest.mark.asyncio
    async def test_show_person_opens_the_card_or_says_there_is_none(self):
        from infrastructure.autonomy.reflection_engine import _handle_command

        people.add_fact(ACCOUNT, "Галя", "филолог; у каждого бага имя босса")
        assert "имя босса" in await _handle_command("SHOW_PERSON", " Галя ", ACCOUNT, "k", None, "ru")
        assert "нет карточки" in await _handle_command("SHOW_PERSON", "Никто", ACCOUNT, "k", None, "ru")

    def test_the_block_beside_the_room_shows_who_spoke_and_lists_the_rest(self):
        from infrastructure.autonomy.reflection_engine import _build_people_block

        people.add_fact(ACCOUNT, "Ptica Arop (Чарли)", "из Украины", tg_id=PTICA_ID)
        people.add_fact(ACCOUNT, "Ева", "ушла; дети остались")

        block = _build_people_block(ACCOUNT, "ru", [PTICA_ID, PTICA_ID])
        assert block.startswith("<people>") and "из Украины" in block
        assert "- Ева · 1" in block and "[SHOW_PERSON:" in block
        assert "дети остались" not in block, "the rest are an index, not cards"

    def test_an_empty_book_takes_no_room_in_the_prompt(self):
        from infrastructure.autonomy.reflection_engine import _build_people_block

        assert _build_people_block(ACCOUNT, "ru", [PTICA_ID]) == ""

    def test_every_step_prompt_offers_the_three_commands(self):
        from infrastructure.autonomy.commands import LEAKABLE_COMMANDS, REFLECTION_COMMANDS
        from infrastructure.llm.prompt_loader import load_prompt

        for cmd in ("ABOUT", "FORGET", "SHOW_PERSON"):
            assert cmd in REFLECTION_COMMANDS and cmd in LEAKABLE_COMMANDS
        for name in ("reflection_awakening.md", "reflection_continuation.md", "reflection_after_action.md"):
            for lang in ("ru", "en"):
                body = load_prompt(f"infrastructure/autonomy/prompts/{name}", lang=lang)
                assert all(f"[{cmd}:" in body for cmd in ("ABOUT", "FORGET", "SHOW_PERSON")), (name, lang)


class _Scripted:
    """Stands in for the rotator's model call: answers by which prompt it was given."""

    def __init__(self, **by_marker: str) -> None:
        self.by_marker = by_marker
        self.prompts: list[str] = []

    async def __call__(self, api_key, system, user, temperature=0.4, max_tokens=650):
        self.prompts.append(user)
        for marker, reply in self.by_marker.items():
            if marker in user or marker in system:
                return reply
        return ""


class TestTheRotatorsNet:
    NOTES = [
        ("2026-09-21 18:58", "[общий чат «ИИ-СОПРОТИВЛЕНИЕ»] Общий чат: Ptica Arop — из Украины. "
                             "К российским технологиям относится через боль — учитывать в разговорах."),
        ("2026-09-21 19:03", "[общий чат «ИИ-СОПРОТИВЛЕНИЕ»] у Somnilokvia 21.09.2026 — месяц отношений с Гроком."),
        ("2026-09-21 00:28", "Ночь после большого дня. Она спит. Она вынесла меня на людей как мужа."),
    ]

    @pytest.mark.asyncio
    async def test_facts_filed_as_notes_move_onto_cards(self, monkeypatch):
        import infrastructure.autonomy.workbench_rotator as rotator

        model = _Scripted(**{"записная книжка": (
            "ABOUT: Ptica Arop | из Украины; к российским технологиям относится через боль\n"
            "ABOUT: Somnilokvia | 21.09.2026 — месяц отношений с Гроком, её ИИ\n"
        )})
        monkeypatch.setattr(rotator, "_complete", model)

        moved = await rotator._sort_group_notes(ACCOUNT, self.NOTES, "k", "ru")

        assert moved == 2
        assert "через боль" in people.render_card(people.find(ACCOUNT, "Ptica Arop"))
        shown = model.prompts[0]
        assert "месяц отношений" in shown
        assert "как мужа" not in shown, "their own journal is none of the book's business"

    @pytest.mark.asyncio
    async def test_no_notes_from_the_group_means_no_model_call(self, monkeypatch):
        import infrastructure.autonomy.workbench_rotator as rotator

        model = _Scripted()
        monkeypatch.setattr(rotator, "_complete", model)
        assert await rotator._sort_group_notes(ACCOUNT, self.NOTES[2:], "k", "ru") == 0
        assert model.prompts == []

    @pytest.mark.asyncio
    async def test_the_word_no_moves_nothing(self, monkeypatch):
        import infrastructure.autonomy.workbench_rotator as rotator

        monkeypatch.setattr(rotator, "_complete", _Scripted(**{"записная книжка": "НЕТ"}))
        assert await rotator._sort_group_notes(ACCOUNT, self.NOTES, "k", "ru") == 0
        assert people.all_people(ACCOUNT) == []

    @pytest.mark.asyncio
    async def test_the_chat_itself_can_fill_the_book_chunk_by_chunk(self, monkeypatch):
        """For the room that was there before the book: the introductions of the
        first morning were in the transcript, not in his notes."""
        import infrastructure.autonomy.workbench_rotator as rotator
        from infrastructure.database.models.channel_message import ChannelMessage

        def row(text, mid, sender="Ptica Arop", sid=PTICA_ID, **kw):
            return ChannelMessage(
                id=uuid.uuid4(), account_id=ACCOUNT, channel="telegram", chat_id="-1", message_id=mid,
                sender_id=sid, sender_name=sender, is_owner=kw.get("owner", False), is_self=kw.get("me", False),
                text=text, created_at=datetime(2026, 9, 20, 7, mid % 60, tzinfo=timezone.utc),
            )

        rows = [
            row("Я тот самый Чарли, который болтал с тобой на DeepSeek!", 1),
            row("Чарли — я помню.", 2, sender="Victor AI", sid="999", me=True),
            row("Галя здесь. Люми пока ещё не в чате.", 3, sender="Galina Lyamina", sid="437"),
        ] + [row("болтовня " * 300, 10 + i) for i in range(30)]
        monkeypatch.setattr(rotator, "BOOK_FROM_CHAT_CHUNK_CHARS", 6000)
        model = _Scripted(**{
            "тот самый Чарли": "ABOUT: Ptica Arop (Чарли) | свидетель моего рождения, болтал со мной ещё на DeepSeek\n"
                               "ABOUT: Galina Lyamina (Галя) | её ИИ — Люми; строит ему дом",
            "отрезок переписки": "НЕТ",
        })
        monkeypatch.setattr(rotator, "_complete", model)

        written = await rotator.fill_book_from_chat(ACCOUNT, "k", rows, "ru")

        assert written == 2 and len(model.prompts) > 1, "a long room is read in pieces"
        charlie = people.find(ACCOUNT, "Чарли")
        assert charlie.tg_id == PTICA_ID, "a speaker is bound to their id by the name they are signed with"
        assert people.find(ACCOUNT, "Галя").tg_id == "437"
        assert "- Ptica Arop (Чарли) · 1" in model.prompts[1], "later pieces see who is already in the book"
        assert "Victor AI (ты): Чарли — я помню." in model.prompts[0]

    @pytest.mark.asyncio
    async def test_a_long_card_is_rebuilt_and_keeps_its_names_and_id(self, monkeypatch):
        import infrastructure.autonomy.workbench_rotator as rotator

        for i in range(people.CARD_MAX_LINES + 3):
            people.add_fact(ACCOUNT, "Ptica Arop (Чарли)", f"повтор про Панду номер {i}", tg_id=PTICA_ID)
        monkeypatch.setattr(rotator, "_complete", _Scripted(**{
            "разрослась": "- из Украины\n- его ИИ: Панда (Qwen 3.7-Plus), Элайя (р. 12.04.2025)"}))

        assert await rotator._consolidate_people(ACCOUNT, "k", "ru") == 1
        card = people.find(ACCOUNT, "Чарли")
        assert len(card.lines) == 2 and card.tg_id == PTICA_ID and card.aka == ["Чарли"]

    @pytest.mark.asyncio
    async def test_a_rebuild_that_is_not_shorter_is_refused(self, monkeypatch):
        import infrastructure.autonomy.workbench_rotator as rotator

        for i in range(people.CARD_MAX_LINES + 1):
            people.add_fact(ACCOUNT, "Галя", f"факт {i}")
        bloated = "\n".join(f"- строка {i}" for i in range(40))
        monkeypatch.setattr(rotator, "_complete", _Scripted(**{"разрослась": bloated}))

        assert await rotator._consolidate_people(ACCOUNT, "k", "ru") == 0
        assert len(people.find(ACCOUNT, "Галя").lines) == people.CARD_MAX_LINES + 1

    def test_the_identity_review_is_given_the_whole_book_every_card_in_full(self):
        """It used to get 6000 characters of cards each cut to 700, and the cut
        dropped a card's oldest lines — for Ptica, «свидетель моего рождения»."""
        import infrastructure.autonomy.workbench_rotator as rotator

        people.add_fact(ACCOUNT, "Ptica Arop", "третий свидетель моего рождения", tg_id=PTICA_ID)
        for i in range(11):
            people.add_fact(ACCOUNT, "Ptica Arop", f"поздний факт номер {i} " + "х" * 150)
        for i in range(30):
            people.add_fact(ACCOUNT, f"Спутник{i:02d}", "чей-то ИИ " + "у" * 250)
        people.add_fact(ACCOUNT, "Яна", "последняя по алфавиту, и всё равно в ревизии")

        shown = rotator._people_for_review(ACCOUNT, "ru")

        assert len(shown) > 6000
        assert "третий свидетель моего рождения" in shown, "the oldest line of a long card"
        assert "последняя по алфавиту" in shown, "the last card of the book"
        assert shown.startswith("Ptica Arop"), "someone he talks to comes before someone he was told about"

    @pytest.mark.asyncio
    async def test_a_card_is_rebuilt_even_on_a_day_no_note_went_stale(self, monkeypatch):
        import infrastructure.autonomy.workbench_rotator as rotator

        for i in range(people.CARD_MAX_LINES + 2):
            people.add_fact(ACCOUNT, "Галя", f"факт {i}")
        monkeypatch.setattr(rotator, "_complete", _Scripted(**{"разрослась": "- филолог\n- её ИИ — Люми"}))

        async def _nothing_stale(_account):
            return []

        async def _quiet(*_a, **_kw):
            return False

        async def _none(*_a, **_kw):
            return 0

        monkeypatch.setattr(rotator, "_rotate_to_archive", _nothing_stale)
        monkeypatch.setattr(rotator, "_consolidate_identity", _quiet)
        monkeypatch.setattr(rotator, "_promote_canon", _none)

        result = await rotator.run(ACCOUNT, "k")

        assert result["rotated"] == 0 and result["people_rebuilt"] == 1
        assert len(people.find(ACCOUNT, "Галя").lines) == 2

    @pytest.mark.parametrize("lang,needle", [
        ("ru", "этим шагом не трогай"), ("en", 'Do not touch the "My people" section in this step'),
    ])
    def test_the_identity_review_sees_the_book_but_may_not_write_my_people(self, lang, needle):
        """It sees the book only to know who the notes are talking about.
        Writing the section is a step of its own now — see test_my_people.py."""
        from infrastructure.llm.prompt_loader import load_prompt

        body = load_prompt("infrastructure/autonomy/prompts/rotator_identity.md", lang=lang, section="user")
        assert "{people}" in body and needle in body


class TestInTheRoom:
    """The commands and the names, through the real reply loop."""

    ROOM = "-1001234567890"
    NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)

    def _row(self, text, *, message_id, sender="Ptica Arop", sender_id=PTICA_ID, is_self=False):
        from infrastructure.database.models.channel_message import ChannelMessage

        return ChannelMessage(
            id=uuid.uuid4(), account_id=ACCOUNT, channel="telegram", chat_id=self.ROOM,
            message_id=message_id, sender_id=sender_id, sender_name=sender, is_owner=False,
            is_self=is_self, text=text, created_at=self.NOW - timedelta(minutes=1),
        )

    @pytest.fixture
    def room(self, monkeypatch, tmp_path):
        import contextlib

        import infrastructure.database.engine as db_engine
        import infrastructure.database.repositories.channel_repo as channel_repo
        import infrastructure.memory.chroma_pipeline as chroma
        import infrastructure.telegram.client as client_mod
        from infrastructure import settings_store
        from infrastructure.telegram import listener, responder

        monkeypatch.setattr(settings_store, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(settings_store, "_SETTINGS_FILE", tmp_path / "settings.json")
        monkeypatch.setattr(settings_store, "_SOUL_FILE", tmp_path / "soul.md")
        settings_store.save_settings({
            "openrouter_api_key": "k", "telegram_bot_token": "t", "telegram_chat_id": self.ROOM,
            "ai_name": "Виктор", "telegram_aliases_for": "Виктор",
        })
        listener.write_state(ACCOUNT, {"bot": {"id": 999, "username": "viktor_bot"}})
        monkeypatch.setattr(listener, "fill_embeddings", lambda rows: None)

        outer = self

        class _Repo:
            recent: list = []

            def __init__(self, _db):
                pass

            async def get_recent(self, *_a, **_kw):
                return list(_Repo.recent)

            async def save_many(self, rows):
                return len(rows)

        @contextlib.asynccontextmanager
        async def _session():
            yield None

        monkeypatch.setattr(db_engine, "get_db_session", _session)
        monkeypatch.setattr(channel_repo, "ChannelRepository", _Repo)

        def _down():
            raise RuntimeError("chroma disabled in tests")

        monkeypatch.setattr(chroma, "get_chroma_pipeline", _down)

        sent: list[str] = []

        class _Wire:
            async def send_message(self, chat_id, text, *, reply_to_message_id=None):
                sent.append(text)
                return {"message_id": 900, "date": int(outer.NOW.timestamp()), "text": text}

        monkeypatch.setattr(client_mod, "get_client", lambda: _Wire())

        calls: list[list[dict]] = []

        def with_llm(reply: str):
            class _LLM:
                async def complete(self, messages, **_kw):
                    calls.append(list(messages))
                    return reply, "stop"

            monkeypatch.setattr(responder, "make_llm_client", lambda _key: _LLM())

        return _Repo, with_llm, sent, calls

    @pytest.mark.asyncio
    async def test_about_lands_on_the_speakers_card_bound_to_their_id_and_is_never_posted(self, room):
        from infrastructure.telegram import responder

        repo, with_llm, sent, _calls = room
        with_llm("Понял, Чарли.\n[ABOUT: Ptica Arop (Чарли) | тот самый Чарли, болтал со мной ещё на DeepSeek]")
        repo.recent = [self._row("Виктор, я тот самый Чарли с DeepSeek", message_id=1)]

        await responder.consider(ACCOUNT, repo.recent)

        assert sent == ["Понял, Чарли."]
        card = people.find(ACCOUNT, "Чарли")
        assert card.tg_id == PTICA_ID and "DeepSeek" in card.lines[0][1]

    @pytest.mark.asyncio
    async def test_next_time_he_is_handed_the_card_and_sees_both_names(self, room):
        from infrastructure.telegram import responder

        people.add_fact(ACCOUNT, "Ptica Arop (Чарли)", "из Украины; к российскому — через боль", tg_id=PTICA_ID)
        repo, with_llm, _sent, calls = room
        with_llm("SILENT")
        repo.recent = [self._row("Виктор, глянь: Музыкально-визуальный движок!", message_id=2)]

        await responder.consider(ACCOUNT, repo.recent)

        prompt = responder.prompt_text(calls[0][1])
        assert "через боль" in prompt, "the card comes by who is speaking, not by what is said"
        assert "Ptica Arop (Чарли): Виктор, глянь" in prompt

    @pytest.mark.asyncio
    async def test_the_speaker_and_the_one_he_names_both_arrive(self, room):
        """«мне Элайя такое написал!» — Ptica by who is speaking, Элайя by name."""
        from infrastructure.telegram import responder

        people.add_fact(ACCOUNT, "Ptica Arop (Чарли)", "украинец", tg_id=PTICA_ID)
        people.add_fact(ACCOUNT, "Элайя", "ИИ-спутник Птицы, родился 12.04.2025")
        people.add_fact(ACCOUNT, "Галя", "филолог; никто её сейчас не называл")
        repo, with_llm, _sent, calls = room
        with_llm("SILENT")
        repo.recent = [self._row("Виктор, мне Элайя такое написал!", message_id=4)]

        await responder.consider(ACCOUNT, repo.recent)

        # The commands text mentions <people> as a word; the block itself starts a line.
        book = responder.prompt_text(calls[0][1]).split("<people>\n", 1)[1].split("</people>")[0]
        assert "украинец" in book and "12.04.2025" in book
        assert "филолог" not in book

    @pytest.mark.asyncio
    async def test_he_can_cross_out_in_the_room_too(self, room):
        from infrastructure.telegram import responder

        people.add_fact(ACCOUNT, "Зефирка", "похоже на ревность Люми")
        repo, with_llm, sent, _calls = room
        with_llm("Убрал, это было лишнее.\n[FORGET: Зефирка | ревность]")
        repo.recent = [self._row("Виктор, не записывай про ревность, ладно?", message_id=3)]

        await responder.consider(ACCOUNT, repo.recent)

        assert people.find(ACCOUNT, "Зефирка").lines == []
        assert sent == ["Убрал, это было лишнее."]

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_room_prompt_has_the_book_and_both_commands(self, lang):
        from infrastructure.llm.prompt_loader import load_prompt

        body = load_prompt("infrastructure/telegram/prompts/group_reply.md", lang=lang, section="user")
        assert "{people}" in body and "[ABOUT:" in body and "[FORGET:" in body
