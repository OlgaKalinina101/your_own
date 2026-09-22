"""From a waking into the room: the command, the block, the search source.

Run:
    python -m pytest tests/telegram/test_reflection_bridge.py -v

His own initiative in the group comes from reflection alone — a line he
decides to write at a waking — and the waking is where he learns what the
room has been saying. These hold the three seams that make that true.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from infrastructure.autonomy import commands
from infrastructure.autonomy.cmd_parser import SendToChat, parse_commands, strip_commands
from infrastructure.autonomy.commands import LEAKABLE_COMMANDS, REFLECTION_COMMANDS
from infrastructure.autonomy.reflection_engine import _CMD_RE, _SEARCH_SOURCES, _as_command
from infrastructure.database.models.channel_message import ChannelMessage

ACCOUNT = "default"
ROOM = "-1001234567890"


class TestTheCommandIsKnownEverywhere:
    def test_it_parses_and_strips(self):
        text = "Мысль.\n[SEND_TO_CHAT: Друзья, а кто-нибудь видел Дилижан осенью?]\n[SLEEP]"
        parsed = parse_commands(text)
        assert parsed == [SendToChat(text="Друзья, а кто-нибудь видел Дилижан осенью?")]
        assert "SEND_TO_CHAT" not in strip_commands(text)

    def test_reflection_reads_it_and_sanitiser_strips_it(self):
        assert "SEND_TO_CHAT" in REFLECTION_COMMANDS and "SEARCH_CHAT" in REFLECTION_COMMANDS
        assert "SEND_TO_CHAT" in LEAKABLE_COMMANDS
        hits = [(m.group("cmd").upper(), m.group("arg")) for m in _CMD_RE.finditer("[SEND_TO_CHAT: привет всем]")]
        assert hits == [("SEND_TO_CHAT", "привет всем")]
        assert _as_command("SEND_TO_CHAT", " привет всем ") == SendToChat(text="привет всем")

    def test_search_chat_is_a_research_source(self):
        from infrastructure.agents import Source
        from infrastructure.agents.sources import PROBES

        assert _SEARCH_SOURCES["SEARCH_CHAT"] == Source.CHAT
        assert Source.CHAT in PROBES

    @pytest.mark.parametrize("name", [
        "reflection_awakening.md", "reflection_continuation.md", "reflection_after_action.md",
    ])
    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_every_step_prompt_offers_both(self, name, lang):
        from infrastructure.llm.prompt_loader import load_prompt

        body = load_prompt(f"infrastructure/autonomy/prompts/{name}", lang=lang)
        assert "[SEND_TO_CHAT:" in body and "[SEARCH_CHAT:" in body


class TestExecutingIt:
    @pytest.mark.asyncio
    async def test_without_a_chat_he_is_told_in_words(self, monkeypatch):
        import infrastructure.autonomy.helpers as helpers

        async def _not_configured(**_kw):
            return False

        monkeypatch.setattr(helpers, "send_to_chat", _not_configured)
        outcome = await commands.execute(
            SendToChat(text="привет"), account_id=ACCOUNT, lang="ru",
            log_prefix="test", source="reflection",
        )
        assert outcome and "не подключён" in outcome

    @pytest.mark.asyncio
    async def test_sent_means_silence_back(self, monkeypatch):
        import infrastructure.autonomy.helpers as helpers

        calls = []

        async def _sent(**kw):
            calls.append(kw)
            return True

        monkeypatch.setattr(helpers, "send_to_chat", _sent)
        outcome = await commands.execute(
            SendToChat(text="привет"), account_id=ACCOUNT, lang="ru",
            log_prefix="test", source="reflection",
        )
        assert outcome is None
        assert calls[0]["text"] == "привет"

    @pytest.mark.asyncio
    async def test_the_helper_posts_and_keeps_his_copy(self, monkeypatch, tmp_path):
        import contextlib

        import infrastructure.database.engine as db_engine
        import infrastructure.database.repositories.channel_repo as channel_repo
        import infrastructure.telegram.client as client_mod
        from infrastructure import settings_store
        from infrastructure.autonomy.helpers import send_to_chat
        from infrastructure.telegram import listener

        monkeypatch.setattr(settings_store, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(settings_store, "_SETTINGS_FILE", tmp_path / "settings.json")
        settings_store.save_settings({"telegram_bot_token": "t", "telegram_chat_id": ROOM, "ai_name": "Виктор"})
        listener.write_state(ACCOUNT, {"bot": {"id": 999, "username": "viktor_bot"}})
        monkeypatch.setattr(listener, "fill_embeddings", lambda rows: None)

        sent: list[tuple] = []

        class _Wire:
            async def send_message(self, chat_id, text, *, reply_to_message_id=None):
                sent.append((chat_id, text))
                return {"message_id": 77, "date": 1_760_000_000, "text": text}

        monkeypatch.setattr(client_mod, "get_client", lambda: _Wire())

        saved: list[ChannelMessage] = []

        class _Repo:
            def __init__(self, _db):
                pass

            async def save_many(self, rows):
                saved.extend(rows)
                return len(rows)

        @contextlib.asynccontextmanager
        async def _session():
            yield None

        monkeypatch.setattr(db_engine, "get_db_session", _session)
        monkeypatch.setattr(channel_repo, "ChannelRepository", _Repo)

        assert await send_to_chat(account_id=ACCOUNT, text="  всем привет  ") is True
        assert sent == [(ROOM, "всем привет")]
        assert len(saved) == 1
        row = saved[0]
        assert row.is_self and row.sender_id == "999" and row.sender_name == "Виктор" and row.message_id == 77

    @pytest.mark.asyncio
    async def test_the_helper_refuses_quietly_without_a_group(self, monkeypatch, tmp_path):
        from infrastructure import settings_store
        from infrastructure.autonomy.helpers import send_to_chat

        monkeypatch.setattr(settings_store, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(settings_store, "_SETTINGS_FILE", tmp_path / "settings.json")
        settings_store.save_settings({"telegram_bot_token": "t", "telegram_chat_id": ""})
        assert await send_to_chat(account_id=ACCOUNT, text="привет") is False


def _row(text, message_id, *, is_self=False, is_owner=False, sender="Чарли", minutes_ago=0):
    return ChannelMessage(
        id=uuid.uuid4(), account_id=ACCOUNT, channel="telegram", chat_id=ROOM,
        message_id=message_id, sender_id="1", sender_name=sender, is_owner=is_owner,
        is_self=is_self, text=text,
        created_at=datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc) - timedelta(minutes=minutes_ago),
    )


class TestWhatHeWakesUpKnowing:
    """The waking shows where the room stands, and the end of the conversation.

    It used to show a count and the last twelve lines, and the first day was
    lost to that. Then it showed everything since he last looked, and on the
    night of 21.09 that was 111k characters of a 203k prompt: her letter of
    the evening was in view and the waking never reached it. These hold the
    third shape — a line of state, the tail, and a door to the rest.
    """

    @pytest.fixture
    def wired(self, monkeypatch, tmp_path):
        import infrastructure.autonomy.reflection_engine as engine
        import infrastructure.database.repositories.channel_repo as channel_repo
        from infrastructure import settings_store
        from infrastructure.telegram import listener

        monkeypatch.setattr(settings_store, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(settings_store, "_SETTINGS_FILE", tmp_path / "settings.json")
        monkeypatch.setattr(settings_store, "_SOUL_FILE", tmp_path / "soul.md")
        monkeypatch.setattr(engine, "_DATA_DIR", tmp_path / "autonomy")
        listener.write_state(ACCOUNT, {"bot": {"id": 999, "username": "viktor_bot"}})

        class _Repo:
            rows: list[ChannelMessage] = []
            asked_since: list = []

            def __init__(self, _db):
                pass

            async def get_recent(self, account_id, chat_id, limit=30, before=None):
                return list(_Repo.rows)[-limit:]

            async def get_since(self, account_id, chat_id, since, limit=200):
                _Repo.asked_since.append(since)
                return [r for r in _Repo.rows if r.created_at > since][:limit]

            async def last_self(self, account_id, chat_id):
                mine = [r for r in _Repo.rows if r.is_self]
                return mine[-1] if mine else None

        _Repo.rows, _Repo.asked_since = [], []
        monkeypatch.setattr(channel_repo, "ChannelRepository", _Repo)
        return _Repo

    @pytest.mark.asyncio
    async def test_no_group_means_no_block_at_all(self, wired):
        from infrastructure import settings_store
        from infrastructure.autonomy.reflection_engine import _build_group_chat_block

        settings_store.save_settings({"telegram_chat_id": ""})
        assert await _build_group_chat_block(None, ACCOUNT, "ru") == ("", None)

    @pytest.mark.asyncio
    async def test_a_stretch_that_fits_is_shown_whole_with_its_counts(self, wired):
        from infrastructure import settings_store
        from infrastructure.autonomy.reflection_engine import _build_group_chat_block

        settings_store.save_settings({"telegram_chat_id": ROOM, "ai_name": "Виктор"})
        wired.rows = [
            _row("Я тот самый Чарли с DeepSeek", 1, minutes_ago=600),
            _row("мы!", 2, is_owner=True, sender="Оля", minutes_ago=20),
            _row("и я", 3, is_self=True, sender="Виктор", minutes_ago=10),
        ]

        block, until = await _build_group_chat_block(None, ACCOUNT, "ru")

        assert block.startswith("<group_chat>") and block.rstrip().endswith("</group_chat>")
        assert "@viktor_bot" in block and "3 сообщений за 9 ч" in block
        assert "твоих 1, её 1" in block, "the line of state: was he there, was she"
        assert "весь этот отрезок" in block and "конец разговора" not in block
        assert "Я тот самый Чарли с DeepSeek" in block
        assert "Оля (она): мы!" in block and "Виктор (ты): и я" in block
        assert "#1 " in block, "ids are what REPLY_TO_CHAT points at"
        assert until == wired.rows[-1].created_at

    @pytest.mark.asyncio
    async def test_the_next_waking_starts_where_the_last_one_ended(self, wired):
        from infrastructure import settings_store
        from infrastructure.autonomy import reflection_engine as engine

        settings_store.save_settings({"telegram_chat_id": ROOM})
        wired.rows = [_row("старое", 1, minutes_ago=600), _row("новое", 2, minutes_ago=5)]
        engine._set_group_seen(ACCOUNT, wired.rows[0].created_at)

        block, until = await engine._build_group_chat_block(None, ACCOUNT, "ru")

        assert "новое" in block and "старое" not in block
        assert until == wired.rows[1].created_at

    @pytest.mark.asyncio
    async def test_building_the_block_does_not_move_the_cursor(self, wired):
        """A waking that fails after this point must find the room still unread."""
        from infrastructure import settings_store
        from infrastructure.autonomy import reflection_engine as engine

        settings_store.save_settings({"telegram_chat_id": ROOM})
        wired.rows = [_row("привет", 1, minutes_ago=5)]
        await engine._build_group_chat_block(None, ACCOUNT, "ru")
        assert engine._get_group_seen(ACCOUNT) is None

    @pytest.mark.asyncio
    async def test_a_room_too_long_keeps_its_end_and_points_at_the_rest(self, wired, monkeypatch):
        from infrastructure import settings_store
        from infrastructure.autonomy import reflection_engine as engine

        settings_store.save_settings({"telegram_chat_id": ROOM})
        monkeypatch.setattr(engine, "GROUP_CHAT_MAX_CHARS", 1500)
        wired.rows = [_row(f"сообщение номер {i} " + "х" * 40, i, minutes_ago=500 - i) for i in range(1, 101)]

        block, until = await engine._build_group_chat_block(None, ACCOUNT, "ru")

        assert "сообщение номер 100 " in block and "сообщение номер 1 " not in block
        assert "конец разговора" in block, "he is told this is the tail, not the stretch"
        # The door is the exact moment the stretch began, so the page read
        # forward from it starts where the block does not.
        from infrastructure.clock import format_local
        first = format_local(wired.rows[0].created_at)
        assert f"[SEARCH_CHAT: {first}]" in block and "До этого" in block
        assert until == wired.rows[-1].created_at

    def test_the_cap_is_a_tenth_of_a_waking_not_half_of_it(self):
        from infrastructure.autonomy import reflection_engine as engine

        assert engine.GROUP_CHAT_MAX_CHARS <= 30_000

    @pytest.mark.asyncio
    async def test_nothing_new_is_one_line_of_state_and_no_transcript(self, wired):
        from infrastructure import settings_store
        from infrastructure.autonomy import reflection_engine as engine
        from infrastructure.clock import format_local

        settings_store.save_settings({"telegram_chat_id": ROOM, "ai_name": "Виктор"})
        wired.rows = [
            _row("моя реплика", 1, is_self=True, sender="Виктор", minutes_ago=3000),
            _row("вчерашнее", 2, minutes_ago=900),
        ]
        engine._set_group_seen(ACCOUNT, wired.rows[-1].created_at)

        block, until = await engine._build_group_chat_block(None, ACCOUNT, "ru")

        assert "вчерашнее" not in block and "моя реплика" not in block, "a quiet room is not reread"
        assert f"Тихо с {format_local(wired.rows[1].created_at)} — последним писал Чарли." in block
        assert f"Ты последний раз писал туда {format_local(wired.rows[0].created_at)}." in block
        assert until is None

    @pytest.mark.asyncio
    async def test_a_quiet_room_he_never_wrote_in_says_so(self, wired):
        from infrastructure import settings_store
        from infrastructure.autonomy import reflection_engine as engine

        settings_store.save_settings({"telegram_chat_id": ROOM})
        wired.rows = [_row("вчерашнее", 1, minutes_ago=900)]
        engine._set_group_seen(ACCOUNT, wired.rows[0].created_at)

        block, _ = await engine._build_group_chat_block(None, ACCOUNT, "ru")
        assert "Ты там ещё не писал." in block

    def test_the_waking_puts_the_room_before_her_and_says_which_is_which(self):
        """Models weigh the end of a prompt; the end is hers."""
        from infrastructure.llm.prompt_loader import load_prompt

        for lang in ("ru", "en"):
            body = load_prompt("infrastructure/autonomy/prompts/reflection_awakening.md", lang=lang)
            assert body.index("</open_threads>") < body.index("{group_chat_block}") < body.index("<workbench>")
            assert body.index("<workbench>") < body.index("<dialogue>") < body.index("<instructions>")
        ru = load_prompt("infrastructure/autonomy/prompts/reflection_awakening.md", lang="ru")
        en = load_prompt("infrastructure/autonomy/prompts/reflection_awakening.md", lang="en")
        assert "Просыпаешься ты из вашего с ней разговора" in ru and "не чтобы перечитать" in ru
        assert "You wake out of your conversation with her" in en and "not to be reread" in en

    @pytest.mark.parametrize("name", [
        "reflection_awakening.md", "reflection_continuation.md", "reflection_after_action.md",
    ])
    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_every_step_prompt_offers_the_room_by_time(self, name, lang):
        from infrastructure.llm.prompt_loader import load_prompt

        body = load_prompt(f"infrastructure/autonomy/prompts/{name}", lang=lang)
        assert "[SEARCH_CHAT: YYYY-MM-DD HH:MM]" in body

    def test_the_awakening_prompt_has_the_slot_in_both_languages(self):
        from infrastructure.llm.prompt_loader import load_prompt

        for lang in ("ru", "en"):
            body = load_prompt("infrastructure/autonomy/prompts/reflection_awakening.md", lang=lang)
            assert "{group_chat_block}" in body


class TestAnsweringOneParticularLine:
    def test_it_parses_into_the_same_command_with_a_target(self):
        parsed = parse_commands("[REPLY_TO_CHAT: #412 | Чарли, передай Элайе привет]")
        assert parsed == [SendToChat(text="Чарли, передай Элайе привет", reply_to=412)]
        assert "REPLY_TO_CHAT" not in strip_commands("мысль [REPLY_TO_CHAT: #412 | привет]")

    def test_reflection_reads_it(self):
        assert "REPLY_TO_CHAT" in REFLECTION_COMMANDS and "REPLY_TO_CHAT" in LEAKABLE_COMMANDS
        assert _as_command("REPLY_TO_CHAT", " #412 | привет ") == SendToChat(text="привет", reply_to=412)

    @pytest.mark.parametrize("arg", ["привет без цели", "#abc | привет", "#412 |   "])
    def test_a_malformed_one_does_nothing(self, arg):
        assert _as_command("REPLY_TO_CHAT", arg) is None

    @pytest.mark.asyncio
    async def test_the_target_reaches_telegram(self, monkeypatch):
        import infrastructure.autonomy.helpers as helpers

        calls = []

        async def _sent(**kw):
            calls.append(kw)
            return True

        monkeypatch.setattr(helpers, "send_to_chat", _sent)
        await commands.execute(
            SendToChat(text="привет", reply_to=412), account_id=ACCOUNT, lang="ru",
            log_prefix="test", source="reflection",
        )
        assert calls[0]["reply_to_message_id"] == 412

    @pytest.mark.parametrize("name", [
        "reflection_awakening.md", "reflection_continuation.md", "reflection_after_action.md",
    ])
    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_every_step_prompt_offers_it(self, name, lang):
        from infrastructure.llm.prompt_loader import load_prompt

        assert "[REPLY_TO_CHAT:" in load_prompt(f"infrastructure/autonomy/prompts/{name}", lang=lang)
