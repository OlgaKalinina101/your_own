"""When the room is his to answer, and what he is shown when it is.

Run:
    python -m pytest tests/telegram/test_responder.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from infrastructure.database.models.channel_message import ChannelMessage
from infrastructure.telegram import responder

ACCOUNT = "default"
ROOM = "-1001234567890"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _row(
    text, *, message_id, sender="Галина", sender_id="222", is_owner=False, is_self=False,
    reply_to=None, minutes_ago=0,
) -> ChannelMessage:
    return ChannelMessage(
        id=uuid.uuid4(), account_id=ACCOUNT, channel="telegram", chat_id=ROOM,
        message_id=message_id, sender_id=sender_id, sender_name=sender,
        is_owner=is_owner, is_self=is_self, reply_to_message_id=reply_to,
        text=text, created_at=NOW - timedelta(minutes=minutes_ago),
    )


HIS = dict(sender="Виктор", sender_id="999", is_self=True)


class TestWhenTheRoomIsHis:
    def _decide(self, new, recent):
        return responder.decide(new, recent, ai_name="Виктор", bot_username="viktor_bot", now=NOW)

    def test_his_name_in_a_line_is_an_address(self):
        new = [_row("Виктор, а ты что думаешь?", message_id=5)]
        trigger = self._decide(new, new)
        assert trigger is not None and trigger.kind == "addressed" and trigger.reply_to == 5

    def test_his_handle_is_an_address(self):
        new = [_row("@viktor_bot привет", message_id=6)]
        assert self._decide(new, new).kind == "addressed"

    def test_a_reply_to_his_message_is_an_address(self):
        recent = [_row("я тут", message_id=3, **HIS), _row("о, ты тут!", message_id=4, reply_to=3)]
        trigger = self._decide(recent[1:], recent)
        assert trigger.kind == "addressed" and trigger.reply_to == 4

    def test_a_name_inside_another_word_is_not_an_address(self):
        new = [_row("Викторина в пятницу!", message_id=7)]
        assert self._decide(new, new) is None

    def test_the_room_talking_among_itself_is_left_alone(self):
        new = [_row("кто идёт на ужин?", message_id=8), _row("я", message_id=9, sender="Чарли")]
        assert self._decide(new, new) is None

    def test_after_he_spoke_the_next_minutes_are_a_conversation(self):
        recent = [_row("иду", message_id=10, minutes_ago=3, **HIS), _row("а во сколько?", message_id=11)]
        trigger = self._decide(recent[1:], recent)
        assert trigger.kind == "conversation" and trigger.reply_to is None

    def test_but_not_once_the_window_has_closed(self):
        recent = [_row("иду", message_id=10, minutes_ago=40, **HIS), _row("а во сколько?", message_id=11)]
        assert self._decide(recent[1:], recent) is None

    def test_the_latest_address_is_the_one_answered_under(self):
        new = [_row("Виктор?", message_id=12), _row("Виктор, ау", message_id=13, sender="Чарли")]
        assert self._decide(new, new).reply_to == 13

    def test_his_own_lines_never_trigger_him(self):
        new = [_row("Виктор — это я", message_id=14, **HIS)]
        assert self._decide(new, new) is None


class TestWhatHeIsShown:
    def test_she_and_he_are_marked_and_nobody_else_is(self):
        rows = [
            _row("привет всем", message_id=1, sender="Оля", sender_id="111", is_owner=True),
            _row("привет", message_id=2, sender="Чарли"),
            _row("и вам", message_id=3, reply_to=1, **HIS),
        ]
        room = responder.render_room(rows, ai_name="Виктор", lang="ru")
        lines = room.splitlines()
        assert "Оля (она): привет всем" in lines[0]
        assert lines[1].endswith("Чарли: привет")
        assert "Виктор (ты): и вам" in lines[2]
        assert "↩#1" in lines[2]

    def test_her_name_is_what_she_last_called_herself(self):
        rows = [_row("…", message_id=1, sender="Оля", is_owner=True), _row("…", message_id=2, sender="Чарли")]
        assert responder._her_name(rows, "ru") == "Оля"

    def test_the_prompt_says_why_he_is_looking(self):
        from infrastructure.llm.prompt_loader import load_prompt

        for lang in ("ru", "en"):
            template = load_prompt("infrastructure/telegram/prompts/group_reply.md", lang=lang, section="user")
            for slot in ("identity", "workbench", "memories", "room", "her_name", "bot_username", "why"):
                assert "{" + slot + "}" in template, f"{lang}: no slot for {slot}"
            assert "{open_threads}" not in template, "the board must not reach the room"
            assert "SILENT" in template


class _LLM:
    def __init__(self, reply: str, finish: str = "stop") -> None:
        self.reply, self.finish = reply, finish
        self.calls: list[list[dict]] = []

    async def complete(self, messages, **_kw):
        self.calls.append(messages)
        return self.reply, self.finish


class _Wire:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, *, reply_to_message_id=None):
        self.sent.append({"chat_id": chat_id, "text": text, "reply_to": reply_to_message_id})
        return {"message_id": 500, "date": int(NOW.timestamp()), "text": text}


class _Repo:
    recent: list[ChannelMessage] = []
    saved: list[ChannelMessage] = []

    def __init__(self, _db) -> None:
        pass

    async def get_recent(self, account_id, chat_id, limit=30, before=None):
        return list(_Repo.recent)

    async def save_many(self, rows):
        _Repo.saved.extend(rows)
        return len(rows)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    import contextlib

    import infrastructure.database.engine as db_engine
    import infrastructure.database.repositories.channel_repo as channel_repo
    import infrastructure.telegram.client as client_mod
    from infrastructure import settings_store
    from infrastructure.telegram import listener

    monkeypatch.setattr(settings_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(settings_store, "_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings_store, "_SOUL_FILE", tmp_path / "soul.md")
    settings_store.save_settings({
        "openrouter_api_key": "k", "telegram_bot_token": "t", "telegram_chat_id": ROOM,
        "telegram_owner_user_id": "111", "ai_name": "Виктор",
    })
    listener.write_state(ACCOUNT, {"bot": {"id": 999, "username": "viktor_bot"}})
    monkeypatch.setattr(listener, "fill_embeddings", lambda rows: None)

    @contextlib.asynccontextmanager
    async def _session():
        yield None

    monkeypatch.setattr(db_engine, "get_db_session", _session)
    monkeypatch.setattr(channel_repo, "ChannelRepository", _Repo)
    _Repo.recent, _Repo.saved = [], []

    # No Chroma in tests: the recall path must degrade to "nothing surfaced".
    import infrastructure.memory.chroma_pipeline as chroma

    def _down():
        raise RuntimeError("chroma disabled in tests")

    monkeypatch.setattr(chroma, "get_chroma_pipeline", _down)

    wire = _Wire()
    monkeypatch.setattr(client_mod, "get_client", lambda: wire)

    def with_llm(reply: str, finish: str = "stop") -> _LLM:
        llm = _LLM(reply, finish)
        monkeypatch.setattr(responder, "make_llm_client", lambda _key: llm)
        return llm

    return wire, with_llm


class TestTheWholeThing:
    @pytest.mark.asyncio
    async def test_an_address_gets_an_answer_under_the_line_and_a_row_of_his_own(self, wired):
        wire, with_llm = wired
        llm = with_llm("Думаю, в пятницу — идеально.")
        new = [_row("Виктор, пятница подходит?", message_id=21, sender="Оля", sender_id="111", is_owner=True)]
        _Repo.recent = new

        said = await responder.consider(ACCOUNT, new)

        assert said == "Думаю, в пятницу — идеально."
        assert wire.sent == [{"chat_id": ROOM, "text": said, "reply_to": 21}]
        assert len(_Repo.saved) == 1 and _Repo.saved[0].is_self and _Repo.saved[0].message_id == 500
        prompt = responder.prompt_text(llm.calls[0][1])
        assert "Оля (она): Виктор, пятница подходит?" in prompt
        assert "@viktor_bot" in prompt

    @pytest.mark.asyncio
    async def test_silence_is_a_decision_and_nothing_is_sent(self, wired):
        wire, with_llm = wired
        with_llm("SILENT")
        new = [_row("Виктор, ты тут?", message_id=22)]
        _Repo.recent = new

        assert await responder.consider(ACCOUNT, new) is None
        assert wire.sent == [] and _Repo.saved == []

    @pytest.mark.asyncio
    async def test_the_room_talking_among_itself_costs_no_model_call(self, wired):
        wire, with_llm = wired
        llm = with_llm("не должно быть вызвано")
        new = [_row("кто идёт?", message_id=23)]
        _Repo.recent = new

        assert await responder.consider(ACCOUNT, new) is None
        assert llm.calls == [] and wire.sent == []

    @pytest.mark.asyncio
    async def test_a_clipped_reply_is_not_posted(self, wired):
        wire, with_llm = wired
        with_llm("начало фразы, которая", finish="length")
        new = [_row("Виктор?", message_id=24)]
        _Repo.recent = new

        assert await responder.consider(ACCOUNT, new) is None
        assert wire.sent == []
