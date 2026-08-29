"""The contract with OpenRouter: shapes in, timeouts out.

Run:
    python -m pytest tests/test_llm_contract.py -v

Talking to OpenRouter over aiohttp instead of an SDK means every guarantee an
SDK would give has to be written here — and tested here, because nothing else
will notice when one goes missing.
"""
from __future__ import annotations

import asyncio

import pytest
from aiohttp import web

from infrastructure.llm.client import (
    IMAGE_ONLY_PREFIXES,
    modalities_for,
    parse_image_response,
)


class TestImageOnlyModels:
    """The list existed twice and the copies disagreed. This is the disagreement."""

    @pytest.mark.parametrize(
        "model",
        [
            "x-ai/grok-imagine",
            "bytedance-seed/seedream-4",
            "black-forest-labs/flux-2",
            "sourceful/riverflow-v2.5-fast",
            "bytedance/seedream",
        ],
    )
    def test_image_only_models_ask_for_image_alone(self, model):
        # main.py's copy was missing x-ai/ and bytedance-seed/, so body
        # generation asked those for text alongside the image and got neither.
        assert modalities_for(model) == ["image"], model

    @pytest.mark.parametrize("model", ["~anthropic/claude-fable-latest", "~z-ai/glm-latest"])
    def test_text_capable_models_ask_for_both(self, model):
        assert modalities_for(model) == ["image", "text"]

    def test_every_prefix_ends_with_a_slash(self):
        # Without it "x-ai" would also match "x-airline/whatever".
        assert all(p.endswith("/") for p in IMAGE_ONLY_PREFIXES)


class TestImageResponseShapes:
    """Four shapes, because providers answer differently and OpenRouter passes it through."""

    def test_typed_image_url_part(self):
        body = {"choices": [{"message": {"content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}
        ]}}]}
        assert parse_image_response(body) == "data:image/png;base64,AAA"

    def test_typed_image_part_with_raw_data(self):
        body = {"choices": [{"message": {"content": [
            {"type": "image", "data": "BBB"}
        ]}}]}
        assert parse_image_response(body) == "data:image/png;base64,BBB"

    def test_typed_image_part_nested_under_source(self):
        body = {"choices": [{"message": {"content": [
            {"type": "image", "source": {"data": "CCC"}}
        ]}}]}
        assert parse_image_response(body) == "data:image/png;base64,CCC"

    def test_plain_string_content(self):
        body = {"choices": [{"message": {"content": "  https://cdn/img.png  "}}]}
        assert parse_image_response(body) == "https://cdn/img.png"

    def test_message_level_images_array(self):
        body = {"choices": [{"message": {
            "content": None,
            "images": [{"image_url": {"url": "data:image/png;base64,DDD"}}],
        }}]}
        assert parse_image_response(body) == "data:image/png;base64,DDD"

    def test_top_level_data_array_with_bare_base64(self):
        body = {"data": [{"b64_json": "EEE"}]}
        assert parse_image_response(body) == "data:image/png;base64,EEE"

    def test_top_level_data_array_with_url(self):
        body = {"data": [{"url": "https://cdn/x.png"}]}
        assert parse_image_response(body) == "https://cdn/x.png"

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"choices": []},
            {"choices": [{"message": {"content": "just some prose"}}]},
            {"choices": [{"message": {"content": [{"type": "text", "text": "hi"}]}}]},
        ],
    )
    def test_no_image_is_none_not_an_exception(self, body):
        assert parse_image_response(body) is None


class TestStreamTimeout:
    """A stream that goes quiet must end, not hang."""

    @pytest.mark.asyncio
    async def test_a_silent_socket_gives_up(self, monkeypatch):
        import infrastructure.llm.client as llm_client

        async def _headers_then_silence(request):
            response = web.StreamResponse(
                status=200, headers={"Content-Type": "text/event-stream"}
            )
            await response.prepare(request)
            await response.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
            await asyncio.sleep(5)  # …and then nothing (the client gives up at 1s)
            return response

        app = web.Application()
        app.router.add_post("/api/v1/chat/completions", _headers_then_silence)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        monkeypatch.setattr(llm_client, "OPENROUTER_BASE", f"http://127.0.0.1:{port}/api/v1")
        monkeypatch.setattr(llm_client, "STREAM_SOCK_READ_S", 1)

        client = llm_client.LLMClient(api_key="x", model="~anthropic/claude-fable-latest")
        got: list[str] = []

        # aiohttp's default is total=5min with no read deadline, which is the
        # wrong way round: a long answer is fine, a dead socket is not.
        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout(10):
                async for chunk in client.stream(messages=[{"role": "user", "content": "hi"}]):
                    got.append(chunk)

        assert got == ["hi"], "the chunk that did arrive should still have arrived"
        await runner.cleanup()


class TestEmbeddingStamp:
    """Changing the embedding model silently turns every stored vector into noise."""

    def test_a_fresh_store_is_stamped(self, tmp_path):
        from infrastructure.memory.chroma_pipeline import _EMBEDDING_STAMP, _verify_embedding_space
        from infrastructure.memory.embedder import MODEL_NAME

        _verify_embedding_space(tmp_path)
        assert (tmp_path / _EMBEDDING_STAMP).read_text(encoding="utf-8").strip() == MODEL_NAME

    def test_a_matching_stamp_says_nothing(self, tmp_path, caplog):
        import logging

        from infrastructure.memory.chroma_pipeline import _verify_embedding_space

        _verify_embedding_space(tmp_path)
        with caplog.at_level(logging.ERROR, logger="chroma"):
            _verify_embedding_space(tmp_path)
        assert not caplog.records

    def test_a_changed_model_is_an_error(self, tmp_path, caplog):
        import logging

        from infrastructure.memory.chroma_pipeline import _EMBEDDING_STAMP, _verify_embedding_space

        (tmp_path / _EMBEDDING_STAMP).write_text("some-other-model", encoding="utf-8")
        with caplog.at_level(logging.ERROR):
            _verify_embedding_space(tmp_path)

        assert caplog.records, "a silent re-index is the whole failure"
        assert "some-other-model" in caplog.records[0].getMessage()


class TestRetries:
    """The streaming path had none, and it is the one every chat message uses.

    A 429 at the wrong moment cost the whole reply, while a reflection step in
    the same second got three attempts — same client, same server, opposite
    treatment, for no reason anyone had decided.
    """

    @pytest.mark.asyncio
    async def test_a_retryable_status_is_retried(self, chat_app, fake_openrouter):
        import infrastructure.llm.client as llm_client

        fake_openrouter.status = 429
        client = llm_client.LLMClient(api_key="x", model="~anthropic/claude-fable-latest")

        with pytest.raises(llm_client.OpenRouterError):
            async for _ in client.stream(messages=[{"role": "user", "content": "hi"}]):
                pass

        assert len(fake_openrouter.requests) == llm_client.MAX_ATTEMPTS

    @pytest.mark.asyncio
    async def test_a_bad_key_is_not_retried(self, chat_app, fake_openrouter):
        """401 will not become 200 in 1.5 seconds; retrying only wastes his turn."""
        import infrastructure.llm.client as llm_client

        fake_openrouter.status = 401
        client = llm_client.LLMClient(api_key="x", model="~anthropic/claude-fable-latest")

        with pytest.raises(llm_client.OpenRouterError) as caught:
            async for _ in client.stream(messages=[{"role": "user", "content": "hi"}]):
                pass

        assert caught.value.retryable is False
        assert len(fake_openrouter.requests) == 1

    @pytest.mark.asyncio
    async def test_a_retry_recovers_the_reply(self, chat_app, fake_openrouter):
        """The point of the whole thing: the answer arrives anyway."""
        import infrastructure.llm.client as llm_client

        fake_openrouter.statuses = [429, 200]
        fake_openrouter.chunks = ["Привет"]
        client = llm_client.LLMClient(api_key="x", model="~anthropic/claude-fable-latest")

        got = [c async for c in client.stream(messages=[{"role": "user", "content": "hi"}])]

        assert got == ["Привет"]
        assert len(fake_openrouter.requests) == 2

    @pytest.mark.asyncio
    async def test_nothing_is_retried_once_the_user_has_seen_text(
        self, chat_app, fake_openrouter
    ):
        """A second attempt would append a second answer to half of the first."""
        import infrastructure.llm.client as llm_client

        fake_openrouter.chunks = ["nach", "und", "nach"]
        fake_openrouter.die_after_chunks = 1
        client = llm_client.LLMClient(api_key="x", model="~anthropic/claude-fable-latest")

        got: list[str] = []
        with pytest.raises(Exception):
            async for chunk in client.stream(messages=[{"role": "user", "content": "hi"}]):
                got.append(chunk)

        assert got == ["nach"]
        assert len(fake_openrouter.requests) == 1, "it retried after showing text"


class TestThereIsOnlyOneWayOut:
    """One transport, so a policy decided once is a policy that applies.

    There were five hand-rolled paths: stream, complete, complete_with_tools,
    generate_image, and the body-expression pipeline in main.py. Each had its
    own session, its own timeout and its own idea of a failure — and retries in
    three of them, missing from the one every chat message uses.
    """

    import pathlib as _pathlib

    REPO = _pathlib.Path(__file__).resolve().parents[1]
    CLIENT = REPO / "infrastructure" / "llm" / "client.py"

    def _production_files(self):
        for directory in ("infrastructure", "api"):
            yield from (self.REPO / directory).rglob("*.py")
        yield self.REPO / "main.py"

    def test_nobody_else_posts_to_openrouter(self):
        offenders = [
            str(path.relative_to(self.REPO))
            for path in self._production_files()
            if path != self.CLIENT
            and "openrouter.ai/api" in path.read_text(encoding="utf-8-sig")
        ]
        assert offenders == [], (
            f"a sixth hand-rolled path to OpenRouter: {offenders}. "
            "Route it through LLMClient so it inherits the retry policy."
        )

    def test_the_client_opens_exactly_one_session(self):
        """Every path goes through `_open`, which is where the policy lives."""
        source = self.CLIENT.read_text(encoding="utf-8-sig")
        assert source.count("aiohttp.ClientSession(") == 1
        assert source.count("session.post(") == 1

    def test_the_retry_policy_is_stated_once(self):
        import infrastructure.llm.client as llm_client

        assert llm_client.MAX_ATTEMPTS == 3
        # 401/403/404 must stay out: a bad key does not fix itself in 1.5s.
        assert not (llm_client.RETRYABLE_STATUSES & {400, 401, 403, 404})
        assert {429, 500, 502, 503} <= llm_client.RETRYABLE_STATUSES
