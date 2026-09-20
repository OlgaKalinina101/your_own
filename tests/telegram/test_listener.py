"""The listener: what the room said, written down — and nothing more.

Run:
    python -m pytest tests/telegram -v

The listener is the only reader of Telegram's update format, so the shape of
a message, who counts as *her*, which room is *the* room and where the cursor
lands are all decided in one place. These tests hold that place.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from infrastructure.telegram import listener

ACCOUNT = "default"
ROOM = "-1001234567890"
OWNER = "111"


def _msg(
    *, text=None, sender_id="222", chat_id=ROOM, message_id=10, date=1_760_000_000,
    first_name="Галина", last_name=None, username=None, reply_to=None, **extra,
):
    message = {
        "message_id": message_id,
        "date": date,
        "chat": {"id": int(chat_id), "type": "supergroup", "title": "Друзья"},
        "from": {"id": int(sender_id), "first_name": first_name, "last_name": last_name, "username": username},
    }
    if text is not None:
        message["text"] = text
    if reply_to is not None:
        message["reply_to_message"] = {"message_id": reply_to}
    message.update(extra)
    return message


def _update(update_id: int, message: dict | None) -> dict:
    out = {"update_id": update_id}
    if message is not None:
        out["message"] = message
    return out


class TestOneMessageBecomesOneRow:
    def test_text_sender_time_and_reply_are_kept(self):
        row = listener.row_from_message(
            _msg(text="привет всем", last_name="из Нячанга", reply_to=7),
            account_id=ACCOUNT, owner_user_id=OWNER,
        )
        assert row is not None
        assert row.chat_id == ROOM
        assert row.message_id == 10
        assert row.sender_id == "222"
        assert row.sender_name == "Галина из Нячанга"
        assert row.text == "привет всем"
        assert row.reply_to_message_id == 7
        assert row.created_at == datetime.fromtimestamp(1_760_000_000, tz=timezone.utc)
        assert row.is_self is False

    def test_she_is_marked_and_nobody_else_is(self):
        hers = listener.row_from_message(
            _msg(text="я тут", sender_id=OWNER), account_id=ACCOUNT, owner_user_id=OWNER,
        )
        theirs = listener.row_from_message(
            _msg(text="и я"), account_id=ACCOUNT, owner_user_id=OWNER,
        )
        assert hers.is_owner is True
        assert theirs.is_owner is False

    def test_without_an_owner_configured_nobody_is_her(self):
        row = listener.row_from_message(
            _msg(text="я тут", sender_id=OWNER), account_id=ACCOUNT, owner_user_id="",
        )
        assert row.is_owner is False

    def test_a_picture_is_kept_as_a_token_and_a_caption_wins_over_it(self):
        photo = listener.row_from_message(
            _msg(photo=[{"file_id": "x"}]), account_id=ACCOUNT, owner_user_id=OWNER,
        )
        captioned = listener.row_from_message(
            _msg(photo=[{"file_id": "x"}], caption="смотрите"), account_id=ACCOUNT, owner_user_id=OWNER,
        )
        assert photo.text == "[photo]"
        assert captioned.text == "смотрите"

    def test_a_service_message_is_dropped(self):
        joined = _msg()
        joined["new_chat_members"] = [{"id": 5}]
        assert listener.row_from_message(joined, account_id=ACCOUNT, owner_user_id=OWNER) is None

    def test_a_name_falls_back_to_the_handle(self):
        row = listener.row_from_message(
            _msg(text="…", first_name=None, username="charlie"), account_id=ACCOUNT, owner_user_id=OWNER,
        )
        assert row.sender_name == "charlie"


class TestOnlyTheChosenRoomIsKept:
    def test_other_rooms_are_remembered_but_not_stored(self):
        state: dict = {}
        rows, offset = listener.rows_from_updates(
            [
                _update(1, _msg(text="в нашей", message_id=1)),
                _update(2, _msg(text="в чужой", message_id=2, chat_id="-100999")),
            ],
            account_id=ACCOUNT, chat_id=ROOM, owner_user_id=OWNER, state=state,
        )
        assert [r.text for r in rows] == ["в нашей"]
        assert set(state["chats"]) == {ROOM, "-100999"}
        assert state["chats"][ROOM]["title"] == "Друзья"
        assert offset == 3

    def test_with_no_room_chosen_nothing_is_stored_but_the_rooms_are_seen(self):
        state: dict = {}
        rows, offset = listener.rows_from_updates(
            [_update(5, _msg(text="эй", message_id=1))],
            account_id=ACCOUNT, chat_id="", owner_user_id=OWNER, state=state,
        )
        assert rows == []
        assert ROOM in state["chats"]
        assert offset == 6

    def test_the_cursor_moves_past_updates_that_carry_no_message(self):
        """An update we did not ask for still has to be acknowledged, or it
        comes back on every poll forever."""
        rows, offset = listener.rows_from_updates(
            [_update(8, None), _update(9, None)],
            account_id=ACCOUNT, chat_id=ROOM, owner_user_id=OWNER, state={},
        )
        assert rows == []
        assert offset == 10

    def test_no_updates_means_no_cursor_change(self):
        rows, offset = listener.rows_from_updates(
            [], account_id=ACCOUNT, chat_id=ROOM, owner_user_id=OWNER, state={},
        )
        assert rows == [] and offset is None


class TestState:
    def test_state_round_trips_and_is_empty_by_default(self):
        assert listener.read_state(ACCOUNT) == {}
        listener.write_state(ACCOUNT, {"offset": 42, "chats": {ROOM: {"title": "Друзья"}}})
        assert listener.read_state(ACCOUNT)["offset"] == 42


class _Client:
    """A Bot API that answers from a script and records what it was asked."""

    def __init__(self, updates: list[dict]) -> None:
        self.updates = updates
        self.offsets: list[int | None] = []

    async def get_me(self) -> dict:
        return {"id": 999, "username": "viktor_bot", "first_name": "Viktor"}

    async def get_updates(self, offset, timeout=25) -> list[dict]:
        self.offsets.append(offset)
        return self.updates


class _Repo:
    saved: list = []

    def __init__(self, _db) -> None:
        pass

    async def save_many(self, rows) -> int:
        _Repo.saved.extend(rows)
        return len(rows)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Settings on disk, a scripted client, a recording repository, no torch."""
    import contextlib

    import infrastructure.database.engine as db_engine
    import infrastructure.database.repositories.channel_repo as channel_repo
    import infrastructure.telegram.client as client_mod
    from infrastructure import settings_store

    monkeypatch.setattr(settings_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(settings_store, "_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(listener, "fill_embeddings", lambda rows: None)

    @contextlib.asynccontextmanager
    async def _session():
        yield None

    monkeypatch.setattr(db_engine, "get_db_session", _session)
    monkeypatch.setattr(channel_repo, "ChannelRepository", _Repo)
    _Repo.saved = []

    def wire(updates: list[dict]) -> _Client:
        client = _Client(updates)
        monkeypatch.setattr(client_mod, "get_client", lambda: client)
        return client

    return wire


class TestOneTick:
    @pytest.mark.asyncio
    async def test_without_a_token_nothing_is_polled(self, wired, monkeypatch):
        import infrastructure.telegram.client as client_mod

        monkeypatch.setattr(client_mod, "get_client", lambda: None)
        assert await listener.tick(ACCOUNT) is False

    @pytest.mark.asyncio
    async def test_a_poll_stores_the_room_and_advances_the_cursor(self, wired):
        from infrastructure.settings_store import save_settings

        save_settings({"telegram_bot_token": "t", "telegram_chat_id": ROOM, "telegram_owner_user_id": OWNER})
        client = wired([
            _update(100, _msg(text="ужин в пятницу?", message_id=1)),
            _update(101, _msg(text="да!", message_id=2, sender_id=OWNER)),
        ])

        assert await listener.tick(ACCOUNT) is True

        assert [r.text for r in _Repo.saved] == ["ужин в пятницу?", "да!"]
        assert [r.is_owner for r in _Repo.saved] == [False, True]
        state = listener.read_state(ACCOUNT)
        assert state["offset"] == 102
        assert state["bot"]["username"] == "viktor_bot"
        assert client.offsets == [None]

    @pytest.mark.asyncio
    async def test_the_next_poll_starts_from_the_cursor(self, wired):
        from infrastructure.settings_store import save_settings

        save_settings({"telegram_bot_token": "t", "telegram_chat_id": ROOM})
        listener.write_state(ACCOUNT, {"offset": 57, "bot": {"id": 999, "username": "viktor_bot"}})
        client = wired([])

        await listener.tick(ACCOUNT)

        assert client.offsets == [57]
        assert listener.read_state(ACCOUNT)["offset"] == 57
