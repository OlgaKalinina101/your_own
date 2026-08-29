"""Shared fixtures for tests that go through the real ASGI application.

Everything the chat path touches that is *not* the chat path — the database,
the embedding model, Chroma, the state files — is replaced here. What stays
real: routing, auth, form parsing, prompt assembly, ``LLMClient.stream`` over
a genuine HTTP connection, and the hand-written SSE serialisation. Those are
the parts that break, and until now nothing exercised them end to end.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web


@pytest.fixture(autouse=True)
def _state_off_the_real_thing(tmp_path_factory, monkeypatch):
    """No test may write into the real ``data/``.

    Autouse, because these writes happen two or three layers below whatever the
    test thinks it is doing: an LLM call appends a row to the corpus, a failed
    Chroma lookup records a degradation on the instrument panel, a workbench
    note lands on the desk. This was not theoretical — 64 rows from a test run
    had to be lifted back out of the August segment of the corpus by hand.

    A test that wants its own directory still sets one; this only decides where
    everything else goes.
    """
    from infrastructure.autonomy import identity_memory, threads, vitals, workbench
    from infrastructure.llm import call_log

    root = tmp_path_factory.mktemp("state")
    monkeypatch.setattr(call_log, "DATASET_DIR", root / "dataset")
    for module in (identity_memory, threads, vitals, workbench):
        monkeypatch.setattr(module, "_DATA_DIR", root / "autonomy")


# ── A fake OpenRouter, spoken to over real HTTP ──────────────────────────────


class FakeOpenRouter:
    """A real socket that answers like OpenRouter, programmable per test.

    Faking at the HTTP layer rather than patching ``LLMClient`` is the point:
    the status-code branch inside ``stream()`` is exactly where a live 429 blew
    up, and a mocked client would never have entered it.
    """

    def __init__(self) -> None:
        self.status = 200
        # A queue of statuses, one per request, for testing retries: the first
        # entry answers the first attempt, and so on. Falls back to `status`.
        self.statuses: list[int] = []
        self.chunks: list[str] = ["Привет"]
        # A queue of chunk-lists, one per request, for turns that differ: the
        # agentic loop asks again with the skill's result, and a server that
        # always repeats itself would issue the same command forever.
        self.replies: list[list[str]] = []
        # Abort the connection after this many chunks, to test a stream that
        # dies after the caller has already been shown text.
        self.die_after_chunks: int | None = None
        # "length" means the model hit max_tokens — reflection treats that as a
        # failed waking rather than a short answer.
        self.finish_reason = "stop"
        self.body_on_error = '{"error":{"message":"rate limited"}}'
        self.requests: list[dict] = []
        self._runner: web.AppRunner | None = None
        self.base_url = ""

    def _next_status(self) -> int:
        return self.statuses.pop(0) if self.statuses else self.status

    async def _handler(self, request: web.Request) -> web.StreamResponse:
        payload = await request.json()
        self.requests.append(payload)

        status = self._next_status()
        if status != 200:
            return web.Response(status=status, text=self.body_on_error)

        # Chat streams; reflection, the analyser and the validator do not. Both
        # go through the same client, so the fake has to answer both — and the
        # shape is chosen the way OpenRouter chooses it, by the request.
        if not payload.get("stream"):
            chunks = self.replies.pop(0) if self.replies else self.chunks
            return web.json_response({
                "choices": [{
                    "message": {"role": "assistant", "content": "".join(chunks)},
                    "finish_reason": self.finish_reason,
                }]
            })

        resp = web.StreamResponse(
            status=200, headers={"Content-Type": "text/event-stream"}
        )
        await resp.prepare(request)
        chunks = self.replies.pop(0) if self.replies else self.chunks
        for sent, piece in enumerate(chunks, start=1):
            frame = {"choices": [{"delta": {"content": piece}}]}
            await resp.write(f"data: {json.dumps(frame)}\n\n".encode())
            if self.die_after_chunks is not None and sent >= self.die_after_chunks:
                raise ConnectionResetError("upstream went away mid-answer")
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/api/v1/chat/completions", self._handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.base_url = f"http://127.0.0.1:{port}/api/v1"

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()


@pytest_asyncio.fixture
async def fake_openrouter(monkeypatch):
    import infrastructure.llm.client as llm_client

    server = FakeOpenRouter()
    await server.start()
    monkeypatch.setattr(llm_client, "OPENROUTER_BASE", server.base_url)
    # The retry backoff is real (1.5s, then 3s) and correct in production; here
    # it would add ~22 seconds of sleeping to the suite. Tests that care about
    # retrying assert on `server.requests`, not on the clock.
    monkeypatch.setattr(llm_client, "_retry_delay", lambda *_a, **_kw: 0)
    yield server
    await server.stop()


# ── The application, with its storage taken away ─────────────────────────────


class FakeRepo:
    """Records what would have been written; returns an empty history."""

    saved: list = []

    def __init__(self, db=None) -> None:
        pass

    async def bulk_save(self, rows) -> None:
        FakeRepo.saved.extend(rows)

    async def get_recent_canonical_pairs(self, **_kwargs) -> list[dict]:
        return []

    async def get_last_user_message_at(self, *_a, **_kw):
        return None


@pytest.fixture
def chat_app(tmp_path: Path, monkeypatch):
    """The real ``main.app`` with storage redirected at *tmp_path*.

    Returns ``(app, headers, repo)``.
    """
    import api.chat as chat_mod
    import infrastructure.settings_store as settings_store
    import infrastructure.autonomy.workbench as wb
    import infrastructure.autonomy.threads as threads
    import infrastructure.autonomy.identity_memory as identity
    from infrastructure.auth import AUTH_TOKEN
    from infrastructure.database.engine import get_db
    import main

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings_store, "_DATA_DIR", data_dir)
    monkeypatch.setattr(settings_store, "_SETTINGS_FILE", data_dir / "settings.json")
    monkeypatch.setattr(settings_store, "_SOUL_FILE", data_dir / "soul.md")
    for module in (wb, threads, identity):
        monkeypatch.setattr(module, "_DATA_DIR", tmp_path / "autonomy")

    # The embedding model is 400 MB of torch and is not what these tests are about.
    monkeypatch.setattr(chat_mod, "fill_chunk_embeddings", lambda rows: None)
    monkeypatch.setattr(chat_mod, "build_chunk_rows", lambda **_kw: [])

    def _no_chroma():
        raise RuntimeError("chroma disabled in tests")

    monkeypatch.setattr(chat_mod, "get_chroma_pipeline", _no_chroma)

    FakeRepo.saved = []
    monkeypatch.setattr(chat_mod, "MessageRepository", FakeRepo)

    # _save_partial deliberately opens its own session (the request-scoped one
    # is gone by the time a disconnect is handled), so that one needs faking too.
    import contextlib

    import infrastructure.database.engine as db_engine

    @contextlib.asynccontextmanager
    async def _fake_session():
        yield None

    monkeypatch.setattr(db_engine, "get_db_session", _fake_session)

    main.app.dependency_overrides[get_db] = lambda: None
    yield main.app, {"Authorization": f"Bearer {AUTH_TOKEN}"}, FakeRepo
    main.app.dependency_overrides.clear()


def parse_sse(raw: str) -> list[tuple[str, str]]:
    """Split an SSE body into ``(event_name, data)`` pairs.

    ``event_name`` is ``"message"`` for a bare ``data:`` block and ``"comment"``
    for a keepalive line, so a test can assert on the keepalive too.
    """
    out: list[tuple[str, str]] = []
    for block in raw.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        if block.startswith(":"):
            out.append(("comment", block[1:].strip()))
            continue
        name = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].removeprefix(" "))
        out.append((name, "\n".join(data_lines)))
    return out
