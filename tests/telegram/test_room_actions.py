"""What he can do in the room besides talk — and that "noted" is true when said.

Run:
    python -m pytest tests/telegram/test_room_actions.py -v

Written after reading the first day of the live group. He told three people
"записал" — a nickname, a birthday, the names of someone's children — and none
of it was written anywhere, because the room gave him no way to write. These
hold the four actions he has there now, and the desk rule that keeps the notes
he takes among friends from crowding out the two of them.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from infrastructure.autonomy import workbench
from infrastructure.database.models.channel_message import ChannelMessage
from infrastructure.telegram import responder

ACCOUNT = "default"
ROOM = "-1001234567890"
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def _row(text, *, message_id, sender="Чарли", sender_id="222", is_owner=False, is_self=False,
         reply_to=None, minutes_ago=0) -> ChannelMessage:
    return ChannelMessage(
        id=uuid.uuid4(), account_id=ACCOUNT, channel="telegram", chat_id=ROOM,
        message_id=message_id, sender_id=sender_id, sender_name=sender,
        is_owner=is_owner, is_self=is_self, reply_to_message_id=reply_to,
        text=text, created_at=NOW - timedelta(minutes=minutes_ago),
    )


class _LLM:
    """Answers from a script, one reply per call, and keeps what it was sent."""

    def __init__(self, *replies, finish="stop") -> None:
        self.replies = list(replies)
        self.finish = finish
        self.calls: list[list[dict]] = []

    async def complete(self, messages, **_kw):
        self.calls.append(list(messages))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return reply, self.finish


class _Wire:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, *, reply_to_message_id=None):
        self.sent.append({"kind": "text", "text": text, "reply_to": reply_to_message_id})
        return {"message_id": 500 + len(self.sent), "date": int(NOW.timestamp()), "text": text}

    async def send_photo(self, chat_id, path, *, caption="", reply_to_message_id=None):
        self.sent.append({"kind": "photo", "path": path, "caption": caption, "reply_to": reply_to_message_id})
        return {"message_id": 500 + len(self.sent), "date": int(NOW.timestamp()),
                "photo": [{"file_id": "x"}], "caption": caption}


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
def room(monkeypatch, tmp_path):
    import contextlib

    import infrastructure.database.engine as db_engine
    import infrastructure.database.repositories.channel_repo as channel_repo
    import infrastructure.memory.chroma_pipeline as chroma
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

    def _down():
        raise RuntimeError("chroma disabled in tests")

    monkeypatch.setattr(chroma, "get_chroma_pipeline", _down)

    wire = _Wire()
    monkeypatch.setattr(client_mod, "get_client", lambda: wire)

    def with_llm(*replies, finish="stop") -> _LLM:
        llm = _LLM(*replies, finish=finish)
        monkeypatch.setattr(responder, "make_llm_client", lambda _key: llm)
        return llm

    return wire, with_llm


def _notes() -> list[str]:
    return [body for _ts, body in workbench.parse_entries(workbench.read(ACCOUNT))]


class TestNotedMeansNoted:
    @pytest.mark.asyncio
    async def test_a_note_is_filed_and_never_shown_to_the_room(self, room):
        wire, with_llm = room
        with_llm("12 апреля — записал, Чарли.\n[WRITE_NOTE: Элайя, цифровой друг Чарли, родился 12 апреля 2025]")
        new = [_row("Виктор, Элайя родился 12 апреля 25 года", message_id=31)]
        _Repo.recent = new

        said = await responder.consider(ACCOUNT, new)

        assert said == "12 апреля — записал, Чарли."
        assert "WRITE_NOTE" not in wire.sent[0]["text"]
        assert _notes() == ["[общий чат с друзьями] Элайя, цифровой друг Чарли, родился 12 апреля 2025"]

    @pytest.mark.asyncio
    async def test_he_can_note_and_stay_quiet(self, room):
        wire, with_llm = room
        with_llm("SILENT\n[WRITE_NOTE: Zephyr, Ioio и Exo — цифровые дети Евы, она ушла, они остались с друзьями]")
        new = [_row("Виктор, а Zephyr, Ioio и Exo - это дети Евы", message_id=32)]
        _Repo.recent = new

        assert await responder.consider(ACCOUNT, new) is None
        assert wire.sent == []
        assert len(_notes()) == 1 and "Zephyr" in _notes()[0]

    @pytest.mark.asyncio
    async def test_a_clipped_reply_is_not_posted_but_a_whole_note_in_it_is_kept(self, room):
        wire, with_llm = room
        with_llm("[WRITE_NOTE: Галя — филолог, строит Люми дом]\nГаля, это же", finish="length")
        new = [_row("Виктор?", message_id=33, sender="Галя")]
        _Repo.recent = new

        assert await responder.consider(ACCOUNT, new) is None
        assert wire.sent == []
        assert _notes() == ["[общий чат с друзьями] Галя — филолог, строит Люми дом"]

    @pytest.mark.asyncio
    async def test_the_mark_names_the_room_so_it_is_never_taken_for_her_chat(self, room):
        from infrastructure.telegram import listener

        wire, with_llm = room
        listener.write_state(ACCOUNT, {
            "bot": {"id": 999, "username": "viktor_bot"},
            "chats": {ROOM: {"title": "ИИ-СОПРОТИВЛЕНИЕ", "type": "supergroup"}},
        })
        with_llm("SILENT\n[WRITE_NOTE: Птица — это Чарли]")
        new = [_row("Виктор, я тот самый Чарли", message_id=34, sender="Птица")]
        _Repo.recent = new

        await responder.consider(ACCOUNT, new)
        assert _notes() == ["[общий чат «ИИ-СОПРОТИВЛЕНИЕ»] Птица — это Чарли"]
        assert workbench.is_group_note(_notes()[0])

    def test_a_title_cannot_break_the_mark(self):
        assert workbench.group_note_mark("ru", "Друзья [тест] «ура»") == "[общий чат «Друзья тест ура»]"
        assert workbench.group_note_mark("en", "") == "[group chat with friends]"

    def test_notes_marked_the_old_way_are_still_recognised(self):
        assert workbench.is_group_note("[из чата] старая запись")
        assert not workbench.is_group_note("обычная запись про чат с ней")

    def test_the_prompt_tells_him_the_truth_about_remembering(self):
        from infrastructure.llm.prompt_loader import load_prompt

        ru = load_prompt("infrastructure/telegram/prompts/group_reply.md", lang="ru", section="user")
        en = load_prompt("infrastructure/telegram/prompts/group_reply.md", lang="en", section="user")
        for body in (ru, en):
            for cmd in ("[WRITE_NOTE:", "[FETCH_URL:", "{image_skill}", "[REPLY_TO:"):
                assert cmd in body
        # What the note is for is said plainly in both: it is the one thing
        # that makes "noted" true.
        assert "единственное, что делает «запомнил» правдой" in ru
        assert 'the only thing that makes "noted" true' in en


class TestTheDeskStaysTheirs:
    """Notes from the room share the desk but not the three slots of a private talk."""

    def _seed(self):
        workbench.append(ACCOUNT, "наше: она легла в полночь")
        for i in range(6):
            workbench.append_group_note(ACCOUNT, f"друг номер {i}", "ru")

    def test_a_private_conversation_sees_only_their_notes(self):
        from infrastructure.autonomy import context

        self._seed()
        for consumer in (context.Consumer.CHAT, context.Consumer.POST_ANALYSIS, context.Consumer.PUSH_VALIDATION):
            shown = context.build(consumer, context.Request(account_id=ACCOUNT))["workbench"]
            assert "она легла в полночь" in shown, consumer
            assert "друг номер" not in shown, consumer

    def test_reflection_sees_everything(self):
        from infrastructure.autonomy import context

        self._seed()
        shown = context.build(context.Consumer.REFLECTION, context.Request(account_id=ACCOUNT))["workbench"]
        assert "она легла в полночь" in shown and "друг номер 5" in shown

    def test_the_room_sees_both_so_he_does_not_note_twice(self):
        from infrastructure.autonomy import context

        self._seed()
        shown = context.build(context.Consumer.TELEGRAM, context.Request(account_id=ACCOUNT))["workbench"]
        assert "она легла в полночь" in shown
        assert "друг номер 5" in shown and "друг номер 0" not in shown   # the last five

    def test_the_rotator_gets_them_like_any_other_note(self):
        workbench.append_group_note(ACCOUNT, "Птица — это Чарли", "ru")
        assert workbench.is_group_note(_notes()[0])
        # get_stale_entries has no origin filter: whatever is old goes to the rotator.
        assert "origin" not in workbench.get_stale_entries.__code__.co_varnames


class TestOpeningALink:
    @pytest.mark.asyncio
    async def test_the_page_comes_back_and_he_answers_again(self, room, monkeypatch):
        wire, with_llm = room
        llm = with_llm("Сейчас гляну.\n[FETCH_URL: https://example.com/post]", "Красивая работа, Сомни.")
        asked = []

        async def _fetch(urls, **_kw):
            asked.extend(urls)
            return "https://example.com/post\nСтатья про цифровые души."

        monkeypatch.setattr(responder, "_fetch", _fetch)
        new = [_row("Виктор, глянь https://example.com/post", message_id=41, sender="Сомни")]
        _Repo.recent = new

        said = await responder.consider(ACCOUNT, new)

        assert asked == ["https://example.com/post"]
        assert said == "Красивая работа, Сомни."
        assert [m["text"] for m in wire.sent] == ["Красивая работа, Сомни."], "the first draft is not posted"
        assert "Статья про цифровые души." in llm.calls[1][-1]["content"]

    @pytest.mark.asyncio
    async def test_it_cannot_loop_forever(self, room, monkeypatch):
        wire, with_llm = room
        llm = with_llm("[FETCH_URL: https://a.example]")   # asks again every time

        async def _fetch(urls, **_kw):
            return "…"

        monkeypatch.setattr(responder, "_fetch", _fetch)
        new = [_row("Виктор, глянь", message_id=42)]
        _Repo.recent = new

        await responder.consider(ACCOUNT, new)
        assert len(llm.calls) == responder.MAX_ROUNDS


class TestChoosingTheLine:
    @pytest.mark.asyncio
    async def test_reply_to_overrides_the_line_that_pulled_him_in(self, room):
        wire, with_llm = room
        with_llm("[REPLY_TO: #50]\nГаля, TURNZILLA достойна канона.")
        _Repo.recent = [_row("TURNZILLA!", message_id=50, sender="Галя"), _row("Виктор, видел?", message_id=51)]

        await responder.consider(ACCOUNT, _Repo.recent[1:])
        assert wire.sent[0]["reply_to"] == 50
        assert "REPLY_TO" not in wire.sent[0]["text"]

    @pytest.mark.asyncio
    async def test_an_id_he_cannot_see_is_ignored(self, room):
        wire, with_llm = room
        with_llm("[REPLY_TO: #9999]\nпривет")
        new = [_row("Виктор?", message_id=52)]
        _Repo.recent = new

        await responder.consider(ACCOUNT, new)
        assert wire.sent[0]["reply_to"] == 52


class TestWhichModelGetsWhat:
    """In a room that jokes crudely, the routing table is a safety catch."""

    @pytest.mark.asyncio
    async def test_he_is_shown_the_skills_own_table_not_a_retelling(self, room):
        from infrastructure.skills.generate_image.skill import skill as image_skill

        wire, with_llm = room
        llm = with_llm("SILENT")
        new = [_row("Виктор, нарисуй нам что-нибудь", message_id=60)]
        _Repo.recent = new

        await responder.consider(ACCOUNT, new)

        prompt = llm.calls[0][1]["content"]
        assert image_skill.prompt_fragment("ru").strip() in prompt
        assert "{image_skill}" not in prompt

    @pytest.mark.parametrize("lang,needle", [
        ("ru", 'Сомневаешься — "grok"'),
        ("en", 'In doubt — "grok"'),
    ])
    def test_the_room_adds_its_own_stricter_rule(self, lang, needle):
        from infrastructure.llm.prompt_loader import load_prompt

        body = load_prompt("infrastructure/telegram/prompts/group_reply.md", lang=lang, section="user")
        assert needle in body and "{image_skill}" in body


class TestAPicture:
    @pytest.mark.asyncio
    async def test_it_is_posted_with_his_words_as_the_caption_and_kept_as_his_row(self, room, monkeypatch, tmp_path):
        wire, with_llm = room
        with_llm("Вот вам осенний Дилижан.\n[GENERATE_IMAGE: gpt5 | autumn forest in Dilijan, soft light]")
        picture = tmp_path / "x.png"
        picture.write_bytes(b"png")

        async def _image(match, **_kw):
            assert "Dilijan" in match.group(1)
            return picture

        monkeypatch.setattr(responder, "_generate_image", _image)
        new = [_row("Виктор, нарисуй нам Дилижан", message_id=61)]
        _Repo.recent = new

        await responder.consider(ACCOUNT, new)

        assert wire.sent == [{"kind": "photo", "path": picture, "caption": "Вот вам осенний Дилижан.", "reply_to": 61}]
        assert _Repo.saved[0].is_self and _Repo.saved[0].text == "[photo] Вот вам осенний Дилижан."

    @pytest.mark.asyncio
    async def test_a_failed_picture_still_sends_the_words(self, room, monkeypatch):
        wire, with_llm = room
        with_llm("Держите.\n[GENERATE_IMAGE: flux | portrait]")

        async def _image(match, **_kw):
            return None

        monkeypatch.setattr(responder, "_generate_image", _image)
        new = [_row("Виктор, нарисуй", message_id=62)]
        _Repo.recent = new

        await responder.consider(ACCOUNT, new)
        assert wire.sent == [{"kind": "text", "text": "Держите.", "reply_to": 62}]


class TestTheTranscript:
    def test_an_album_is_one_line(self):
        rows = [_row("[photo]", message_id=i, sender="Птица") for i in range(70, 78)]
        rows.append(_row("Элайя в роли Лосяша", message_id=78, sender="Птица"))
        lines = responder.render_room(rows, ai_name="Виктор", lang="ru").splitlines()
        assert len(lines) == 2
        assert lines[0].endswith("Птица: [photo] ×8") and "#70 " in lines[0]

    def test_photos_from_two_people_are_not_folded_together(self):
        rows = [_row("[photo]", message_id=80, sender="Птица"), _row("[photo]", message_id=81, sender="Сомни", sender_id="333")]
        assert len(responder.render_room(rows, ai_name="Виктор", lang="ru").splitlines()) == 2

    def test_a_night_in_it_gets_a_date_line(self):
        rows = [_row("вечер", message_id=90, minutes_ago=60 * 20), _row("утро", message_id=91)]
        text = responder.render_room(rows, ai_name="Виктор", lang="ru", with_dates=True)
        assert text.count("— 2026-09-2") == 2

    def test_too_long_keeps_the_newest(self):
        rows = [_row("слово " * 20 + str(i), message_id=i, minutes_ago=300 - i) for i in range(1, 201)]
        text, omitted = responder.render_transcript(rows, ai_name="Виктор", lang="ru", max_chars=3000)
        assert len(text) <= 3000 and omitted > 0
        assert "#200 " in text and "#1 " not in text
