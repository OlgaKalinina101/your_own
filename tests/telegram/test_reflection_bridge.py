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
    @pytest.fixture
    def wired(self, monkeypatch, tmp_path):
        import infrastructure.database.repositories.channel_repo as channel_repo
        from infrastructure import settings_store
        from infrastructure.telegram import listener

        monkeypatch.setattr(settings_store, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(settings_store, "_SETTINGS_FILE", tmp_path / "settings.json")
        monkeypatch.setattr(settings_store, "_SOUL_FILE", tmp_path / "soul.md")
        listener.write_state(ACCOUNT, {"bot": {"id": 999, "username": "viktor_bot"}})

        class _Repo:
            recent: list[ChannelMessage] = []
            since_count = 0

            def __init__(self, _db):
                pass

            async def get_recent(self, account_id, chat_id, limit=30, before=None):
                return list(_Repo.recent)[-limit:]

            async def count_since(self, account_id, chat_id, since):
                return _Repo.since_count

        monkeypatch.setattr(channel_repo, "ChannelRepository", _Repo)
        return _Repo

    @pytest.mark.asyncio
    async def test_no_group_means_no_block_at_all(self, wired):
        from infrastructure import settings_store
        from infrastructure.autonomy.reflection_engine import _build_group_chat_block

        settings_store.save_settings({"telegram_chat_id": ""})
        assert await _build_group_chat_block(None, ACCOUNT, "ru", None) == ""

    @pytest.mark.asyncio
    async def test_the_block_counts_since_the_last_waking_and_marks_her(self, wired):
        from infrastructure import settings_store
        from infrastructure.autonomy.reflection_engine import _build_group_chat_block

        settings_store.save_settings({"telegram_chat_id": ROOM, "ai_name": "Виктор"})
        wired.recent = [
            _row("кто в Дилижан?", 1, minutes_ago=30),
            _row("мы!", 2, is_owner=True, sender="Оля", minutes_ago=20),
            _row("и я", 3, is_self=True, sender="Виктор", minutes_ago=10),
        ]
        wired.since_count = 41

        block = await _build_group_chat_block(
            None, ACCOUNT, "ru", datetime.now(timezone.utc) - timedelta(hours=12),
        )

        assert block.startswith("<group_chat>") and block.rstrip().endswith("</group_chat>")
        assert "@viktor_bot" in block and "41 сообщений" in block
        assert "Оля (она): мы!" in block
        assert "Виктор (ты): и я" in block

    @pytest.mark.asyncio
    async def test_a_quiet_room_says_so(self, wired):
        from infrastructure import settings_store
        from infrastructure.autonomy.reflection_engine import _build_group_chat_block

        settings_store.save_settings({"telegram_chat_id": ROOM})
        wired.recent, wired.since_count = [], 0
        block = await _build_group_chat_block(None, ACCOUNT, "en", None)
        assert "Quiet so far." in block

    def test_the_awakening_prompt_has_the_slot_in_both_languages(self):
        from infrastructure.llm.prompt_loader import load_prompt

        for lang in ("ru", "en"):
            body = load_prompt("infrastructure/autonomy/prompts/reflection_awakening.md", lang=lang)
            assert "{group_chat_block}" in body
