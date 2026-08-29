"""Vertical tests for POST /api/chat — the SSE contract.

Run:
    python -m pytest tests/api/test_chat_stream.py -v

These are the first tests in the project that go through the assembled
application. They exist because the failures they cover are invisible from
below: a 429 from OpenRouter used to raise ``NameError`` deep inside
``LLMClient.stream``, and the chat endpoint turned that into a normal-looking
empty answer. Every unit test stayed green.
"""
from __future__ import annotations

import json

import httpx
import pytest

from tests.conftest import parse_sse


def _form(text: str = "привет", **extra) -> dict:
    form = {
        "messages": json.dumps([{"role": "user", "content": text}]),
        "model": "~anthropic/claude-fable-latest",
        "api_key": "test-key",
        "account_id": "default",
    }
    form.update(extra)
    return form


async def _post_chat(app, headers, form) -> str:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.post("/api/chat", data=form, headers=headers, timeout=30)
        assert response.status_code == 200
        return response.text


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_the_answer_reaches_the_client(self, chat_app, fake_openrouter):
        app, headers, repo = chat_app
        fake_openrouter.chunks = ["Привет", ", ", "Оля"]

        events = parse_sse(await _post_chat(app, headers, _form()))
        names = [name for name, _ in events]

        assert names[0] == "pair_id"
        assert names[-1] == "message" and events[-1][1] == "[DONE]"
        text = "".join(d for n, d in events if n == "message" and d != "[DONE]")
        assert text == "Привет, Оля"

    @pytest.mark.asyncio
    async def test_the_reply_is_persisted(self, chat_app, fake_openrouter):
        app, headers, repo = chat_app
        fake_openrouter.chunks = ["Готово"]

        await _post_chat(app, headers, _form())

        assistant = [r for r in repo.saved if r.role == "assistant"]
        assert len(assistant) == 1
        assert assistant[0].text == "Готово"

    @pytest.mark.asyncio
    async def test_cyrillic_and_newlines_survive_the_serialiser(
        self, chat_app, fake_openrouter
    ):
        # Multi-line chunks become multiple data: lines in one event; the client
        # rejoins them with \n. A chunk split mid-word must not gain a newline.
        fake_openrouter.chunks = ["строка один\nстрока", " два"]
        app, headers, _ = chat_app

        events = parse_sse(await _post_chat(app, headers, _form()))
        text = "".join(d for n, d in events if n == "message" and d != "[DONE]")
        assert text == "строка один\nстрока два"


class TestOpenRouterFailure:
    """The class of failure that used to be silent."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 500, 502])
    async def test_the_client_is_told(self, chat_app, fake_openrouter, status):
        app, headers, _ = chat_app
        fake_openrouter.status = status

        events = parse_sse(await _post_chat(app, headers, _form()))
        names = [name for name, _ in events]

        # Headers already went out with 200, so the only way to say "this failed"
        # is an in-band event. Without it the reply is indistinguishable from
        # a model that chose to stay quiet.
        assert "error" in names, f"no error event in {names}"
        assert names[-1] == "message" and events[-1][1] == "[DONE]"

    @pytest.mark.asyncio
    async def test_the_error_names_the_pair_so_the_client_can_clean_up(
        self, chat_app, fake_openrouter
    ):
        app, headers, _ = chat_app
        fake_openrouter.status = 429

        events = parse_sse(await _post_chat(app, headers, _form()))
        pair_id = json.loads(next(d for n, d in events if n == "pair_id"))["pair_id"]
        error = json.loads(next(d for n, d in events if n == "error"))
        assert error["pair_id"] == pair_id
        assert error["message"]

    @pytest.mark.asyncio
    async def test_no_half_answer_is_stored_as_a_whole_one(
        self, chat_app, fake_openrouter
    ):
        app, headers, repo = chat_app
        fake_openrouter.status = 429

        await _post_chat(app, headers, _form())

        assert [r for r in repo.saved if r.role == "assistant"] == []


class TestAuth:
    @pytest.mark.asyncio
    async def test_no_token_is_401(self, chat_app, fake_openrouter):
        app, _headers, _ = chat_app
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post("/api/chat", data=_form())
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_a_wrong_token_is_401(self, chat_app, fake_openrouter):
        app, _headers, _ = chat_app
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                "/api/chat", data=_form(), headers={"Authorization": "Bearer wrong"}
            )
        assert response.status_code == 401


class TestClientDisconnect:
    """A hung-up client must not raise, and must not cost the part already said."""

    @staticmethod
    async def _post_and_hang_up(app, form, headers, after_chunks: int = 1) -> bytes:
        """Drive the ASGI app by hand and send http.disconnect mid-stream.

        httpx's ASGITransport reads the response to completion, so it can never
        reproduce a disconnect. Starlette cancels the body generator when
        ``http.disconnect`` arrives — that cancellation is the thing under test.
        """
        import asyncio
        from urllib.parse import urlencode

        body = urlencode(form).encode()
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "path": "/api/chat", "raw_path": b"/api/chat",
            "query_string": b"", "root_path": "", "scheme": "http",
            "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 80),
            "headers": [
                (b"host", b"t"),
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"content-length", str(len(body)).encode()),
                *[(k.lower().encode(), v.encode()) for k, v in headers.items()],
            ],
        }

        body_sent = False
        hang_up = asyncio.Event()
        received: list[bytes] = []

        async def receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await hang_up.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                if chunk:
                    received.append(chunk)
                if len(received) >= after_chunks:
                    hang_up.set()

        await app(scope, receive, send)
        return b"".join(received)

    @pytest.mark.asyncio
    async def test_disconnect_neither_raises_nor_loses_the_partial(
        self, chat_app, fake_openrouter, caplog, monkeypatch
    ):
        import asyncio
        import logging

        app, headers, repo = chat_app
        fake_openrouter.chunks = [f"кусок{i} " for i in range(400)]

        # setup_logger sets propagate=False, so caplog sees nothing by default.
        monkeypatch.setattr(logging.getLogger("chat"), "propagate", True)
        caplog.set_level("INFO", logger="chat")
        await self._post_and_hang_up(app, _form(), headers, after_chunks=3)

        messages = [record.getMessage() for record in caplog.records]
        # `yield` inside `finally` used to turn every disconnect into
        # "async generator ignored GeneratorExit".
        assert not any("ignored GeneratorExit" in m for m in messages), messages
        assert any("client disconnected" in m for m in messages), messages

        # The save is detached on purpose: it has to outlive the request.
        for _ in range(100):
            await asyncio.sleep(0.02)
            if [r for r in repo.saved if r.role == "assistant"]:
                break
        partial = [r for r in repo.saved if r.role == "assistant"]
        assert partial, "the part the user already saw was thrown away"
        assert partial[0].text.startswith("кусок0")


class TestTheEventSerialiser:
    """Thirteen call sites now share one function, so it gets its own tests."""

    def test_the_frame_is_one_event(self):
        from api.chat import _sse

        assert _sse("memory", {"a": 1}) == 'event: memory\ndata: {"a": 1}\n\n'

    def test_a_newline_in_the_payload_cannot_split_the_frame(self):
        """A web brief is multi-line prose; a raw newline would end the event early."""
        from api.chat import _sse

        frame = _sse("web_results", {"brief": "первая строка\nвторая строка"})

        body = frame.split("\n\n")[0]
        assert body.count("\n") == 1, "the data: line was split in two"
        # The newline survives as an escape inside the JSON, not a real one.
        assert "\\n" in frame

    def test_cyrillic_is_readable_rather_than_escaped(self):
        from api.chat import _sse

        frame = _sse("rewrite", {"text": "привет"})
        assert "привет" in frame
        # …and not the \uXXXX form json.dumps produces by default,
        # which is valid but roughly six times the bytes for Russian.
        assert json.dumps({"text": "привет"}) not in frame

    def test_the_client_can_parse_what_it_receives(self):
        from api.chat import _sse
        from tests.conftest import parse_sse

        frame = _sse("web_results", {"brief": "строка\nдругая", "n": 2})
        name, data = parse_sse(frame)[0]

        assert name == "web_results"
        assert json.loads(data) == {"brief": "строка\nдругая", "n": 2}


class TestASkillCommandInTheReply:
    """The agentic loop, through the endpoint. Nothing covered it before.

    The registry and the regexes had tests; what happened when a command
    actually arrived mid-stream — buffering, stripping, the rewrite event, what
    reaches the database — did not.
    """

    @pytest.mark.asyncio
    async def test_a_post_skill_is_stripped_from_what_he_says(
        self, chat_app, fake_openrouter
    ):
        app, headers, repo = chat_app
        fake_openrouter.chunks = [
            "Запомнил. ",
            "[SAVE_MEMORY: Личное | 4 | Она любит Кёсем]",
        ]

        events = parse_sse(await _post_chat(app, headers, _form()))
        names = [name for name, _ in events]
        streamed = "".join(d for n, d in events if n == "message" and d != "[DONE]")

        assert "rewrite" in names, f"no rewrite event: {names}"
        assert "SAVE_MEMORY" not in streamed, "the raw command reached the user"
        # The command was buffered, so what she actually reads is the rewrite
        # payload and what is stored — checking the message frames alone would
        # pass even if the command were left in.
        rewritten = " ".join(d for n, d in events if n == "rewrite")
        assert "SAVE_MEMORY" not in rewritten, "the raw command is what she ends up reading"
        stored = " ".join(r.text for r in repo.saved if r.role == "assistant")
        assert "Запомнил" in stored

    @pytest.mark.asyncio
    async def test_the_command_is_buffered_rather_than_shown_then_erased(
        self, chat_app, fake_openrouter
    ):
        app, headers, _repo = chat_app
        fake_openrouter.chunks = ["До ", "[SAVE_MEMORY: a | 3 | b]", " после"]

        events = parse_sse(await _post_chat(app, headers, _form()))
        before_rewrite = []
        for name, data in events:
            if name == "rewrite":
                break
            if name == "message" and data != "[DONE]":
                before_rewrite.append(data)

        assert "SAVE_MEMORY" not in "".join(before_rewrite)

    @pytest.mark.asyncio
    async def test_a_reply_with_no_command_streams_untouched(
        self, chat_app, fake_openrouter
    ):
        app, headers, _repo = chat_app
        fake_openrouter.chunks = ["Просто ", "ответ"]

        events = parse_sse(await _post_chat(app, headers, _form()))
        names = [name for name, _ in events]
        text = "".join(d for n, d in events if n == "message" and d != "[DONE]")

        assert text == "Просто ответ"
        assert "rewrite" not in names, "an untouched reply should need no rewrite"


class TestTheMemoryPanel:
    """The recalled facts have to reach the client, not just the model."""

    @pytest.mark.asyncio
    async def test_the_memory_event_is_always_sent(self, chat_app, fake_openrouter):
        # Even with nothing recalled: the UI clears its panel from this event,
        # so a missing one leaves the previous turn's facts on screen.
        events = parse_sse(await _post_chat(*chat_app[:2], _form()))
        names = [name for name, _ in events]
        assert "memory" in names, f"no memory event: {names}"

    @pytest.mark.asyncio
    async def test_it_carries_the_facts_that_were_recalled(
        self, chat_app, fake_openrouter, monkeypatch
    ):
        import api.chat as chat_mod

        async def _one_fact(*_a, **_kw):
            return chat_mod._Recall(
                block="<memory>…</memory>",
                fact_ids=["f1"],
                for_ui=[{"id": "f1", "text": "Она любит Кёсем", "category": "Личное",
                         "impressive": 4, "time_label": "вчера"}],
            )

        monkeypatch.setattr(chat_mod, "_recall", _one_fact)

        events = parse_sse(await _post_chat(*chat_app[:2], _form()))
        payload = json.loads(next(d for n, d in events if n == "memory"))

        assert [f["text"] for f in payload["chroma_facts"]] == ["Она любит Кёсем"]


class TestTheAgenticLoop:
    """He acts, reads the result, and answers again — inside one turn.

    Driven with a stand-in skill rather than a real one: the loop is what is
    under test, not what search or image generation happen to do.
    """

    @staticmethod
    def _fake_skill(action_type="agentic", continuation="Вот что нашлось.", markers=()):
        import re as _re

        from infrastructure.skills.base import SkillResult

        class _Skill:
            id = "probe"
            action_type = None
            stream_command_text = True
            persist_in_db = True
            parse_re = _re.compile(r"\[PROBE:\s*([^\]]*)\]")
            open_re_fragment = "PROBE"
            calls: list = []

            def prompt_fragment(self, _lang):
                return "[PROBE: ...]"

            def pre_sse_events(self, _match):
                return [("probe_start", {"ok": True})]

            async def execute(self, match, _ctx):
                self.calls.append(match.group(0))
                return SkillResult(
                    sse_events=[("probe_done", {"n": len(self.calls)})],
                    continuation=continuation,
                    db_markers=list(markers),
                )

        skill = _Skill()
        skill.action_type = action_type
        return skill

    @pytest.fixture
    def with_probe_skill(self, monkeypatch):
        def _install(skill):
            from infrastructure.skills import registry

            monkeypatch.setattr(registry, "get_enabled", lambda *_a, **_kw: [skill])
            monkeypatch.setattr(registry, "get_all", lambda *_a, **_kw: [skill])
            return skill

        return _install

    @pytest.mark.asyncio
    async def test_the_result_comes_back_as_a_second_answer(
        self, chat_app, fake_openrouter, with_probe_skill
    ):
        skill = with_probe_skill(self._fake_skill())
        app, headers, _repo = chat_app
        # First turn issues the command; the second is the continuation and
        # must not issue another, or the loop simply runs to its cap.
        fake_openrouter.replies = [["Сейчас посмотрю. [PROBE: Кёсем]"], ["Нашёл."]]

        events = parse_sse(await _post_chat(app, headers, _form()))
        names = [name for name, _ in events]

        assert skill.calls == ["[PROBE: Кёсем]"], "the command did not execute"
        assert len(fake_openrouter.requests) == 2, "no continuation was requested"
        assert "probe_start" in names and "probe_done" in names

    @pytest.mark.asyncio
    async def test_the_continuation_reaches_the_user(
        self, chat_app, fake_openrouter, with_probe_skill
    ):
        with_probe_skill(self._fake_skill())
        app, headers, _repo = chat_app
        fake_openrouter.replies = [["Смотрю. [PROBE: x]"], ["Вот ответ."]]

        events = parse_sse(await _post_chat(app, headers, _form()))
        streamed = "".join(d for n, d in events if n == "message" and d != "[DONE]")

        assert "Вот ответ." in streamed, "the continuation never reached the client"

    @pytest.mark.asyncio
    async def test_an_inline_skill_does_not_ask_the_model_again(
        self, chat_app, fake_openrouter, with_probe_skill
    ):
        """Image generation is inline: the picture is the answer."""
        with_probe_skill(self._fake_skill(action_type="inline"))
        app, headers, _repo = chat_app
        fake_openrouter.chunks = ["Рисую. [PROBE: кот]"]

        await _post_chat(app, headers, _form())

        assert len(fake_openrouter.requests) == 1, "an inline skill re-prompted the model"

    @pytest.mark.asyncio
    async def test_the_loop_cannot_run_forever(
        self, chat_app, fake_openrouter, with_probe_skill
    ):
        """Every continuation contains another command; the cap is what ends it.

        Bounded by a timeout on purpose: without the cap this does not fail,
        it hangs, and a sentinel that hangs is a sentinel nobody reads.
        """
        import asyncio

        import api.chat as chat_mod

        skill = with_probe_skill(self._fake_skill())
        app, headers, _repo = chat_app
        fake_openrouter.chunks = ["Опять. [PROBE: снова]"]

        async with asyncio.timeout(30):
            await _post_chat(app, headers, _form())

        assert len(skill.calls) == chat_mod.MAX_AGENT_LOOPS, (
            f"ran {len(skill.calls)} rounds, cap is {chat_mod.MAX_AGENT_LOOPS}"
        )

    @pytest.mark.asyncio
    async def test_db_markers_are_stored_but_not_streamed(
        self, chat_app, fake_openrouter, with_probe_skill
    ):
        with_probe_skill(
            self._fake_skill(action_type="inline", markers=["[GENERATED_IMAGE: /x.png]"])
        )
        app, headers, repo = chat_app
        fake_openrouter.chunks = ["Готово. [PROBE: кот]"]

        events = parse_sse(await _post_chat(app, headers, _form()))
        streamed = "".join(d for n, d in events if n == "message" and d != "[DONE]")
        stored = [r.text for r in repo.saved if r.role == "assistant"]

        assert "GENERATED_IMAGE" not in streamed
        assert stored and "GENERATED_IMAGE" in stored[0], (
            "the marker the client needs to re-render the turn was not saved"
        )


class TestWhatKeepsTheConnectionAlive:
    """While a command is buffered nothing goes out, and that silence can run
    the length of the whole reply — long enough for a proxy to decide the
    connection is dead and cut it."""

    @pytest.mark.asyncio
    async def test_a_buffered_stream_still_says_something(
        self, chat_app, fake_openrouter, monkeypatch
    ):
        import api.chat as chat_module

        monkeypatch.setattr(chat_module, "_KEEPALIVE_EVERY_S", 0.0)
        app, headers, _repo = chat_app
        # The command opens buffering; everything after it is held back.
        fake_openrouter.chunks = [
            "Думаю. ",
            "[SAVE_MEMORY: Личное | 4 | что-то]",
            " ещё ", "и ", "ещё",
        ]

        raw = await _post_chat(app, headers, _form())

        assert ": keepalive" in raw, (
            "the stream went silent for the length of the reply and said nothing"
        )


class TestWhatHeSaysAfterACommand:
    @pytest.mark.asyncio
    async def test_words_after_the_last_command_still_reach_her(
        self, chat_app, fake_openrouter, monkeypatch
    ):
        from infrastructure.skills import registry

        skill = TestTheAgenticLoop._fake_skill(action_type="inline")
        monkeypatch.setattr(registry, "get_enabled", lambda *_a, **_kw: [skill])
        monkeypatch.setattr(registry, "get_all", lambda *_a, **_kw: [skill])
        app, headers, repo = chat_app
        fake_openrouter.chunks = ["[PROBE: кот] и вот что я думаю про это"]

        events = parse_sse(await _post_chat(app, headers, _form()))
        seen = " ".join(d for n, d in events if n in ("message", "rewrite"))
        stored = " ".join(r.text for r in repo.saved if r.role == "assistant")

        assert "и вот что я думаю" in seen + stored, (
            "he kept talking after the command and none of it arrived"
        )


class TestTheSavedFactMarker:
    """The marker is how the client renders 'I remembered this'. It has to be in
    the stored text too, or the same turn renders differently tomorrow."""

    @pytest.mark.asyncio
    async def test_it_is_appended_to_what_gets_stored(self, monkeypatch):
        import api.chat as chat_module

        class _Save:
            async def execute_batch(self, _matches, _text, _ctx):
                return [{"category": "Личное", "impressive": 4, "fact": "любит Кёсем"}]

        class _Sched:
            async def execute_batch(self, _matches, _ctx):
                return None

        monkeypatch.setattr("infrastructure.skills.save_memory.skill.skill", _Save())
        monkeypatch.setattr("infrastructure.skills.schedule_message.skill.skill", _Sched())

        text_full, frames = await chat_module._apply_post_skills(
            [], text="Запомнил.", text_full="Запомнил.", ctx=None
        )

        assert "[SAVED_FACT: Личное | 4 | любит Кёсем]" in text_full
        assert any("SAVED_FACT" in f for f in frames), "she was never shown it either"

    @pytest.mark.asyncio
    async def test_a_duplicate_fact_is_neither_shown_nor_stored(self, monkeypatch):
        import api.chat as chat_module

        class _Save:
            async def execute_batch(self, _matches, _text, _ctx):
                return [{"category": "Личное", "impressive": 4,
                         "fact": "уже знал", "dedup": "skipped"}]

        class _Sched:
            async def execute_batch(self, _matches, _ctx):
                return None

        monkeypatch.setattr("infrastructure.skills.save_memory.skill.skill", _Save())
        monkeypatch.setattr("infrastructure.skills.schedule_message.skill.skill", _Sched())

        text_full, frames = await chat_module._apply_post_skills(
            [], text="Ага.", text_full="Ага.", ctx=None
        )

        assert text_full == "Ага."
        assert frames == []
