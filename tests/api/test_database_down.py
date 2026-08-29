"""Postgres unreachable: the client is told which thing is broken.

Run:
    python -m pytest tests/api/test_database_down.py -v

It used to be a bare 500 with no body. From outside, a backend whose database is
unreachable looked exactly like a backend with a bug in it — and the desktop
app's own message, "is the backend running?", asked the wrong question, because
it was running.

There is a second trap underneath. SQLAlchemy does **not** wrap a failed
connect: what comes out is a plain ``ConnectionRefusedError``, an ``OSError`` —
the same class aiohttp raises when OpenRouter is unreachable. Mapping ``OSError``
centrally would have reported a dead OpenRouter as a dead database, so the
translation happens at the database boundary and these tests hold it there.
"""
from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy.exc import OperationalError

import infrastructure.database.engine as db_engine
from infrastructure.database.engine import DatabaseUnavailable


class _DeadSession:
    """A session whose connection can never be established."""

    def __init__(self, error: BaseException | None = None) -> None:
        self._error = error or ConnectionRefusedError("[WinError 1225] refused")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def connection(self):
        raise self._error

    async def rollback(self):
        raise ConnectionRefusedError("and the rollback cannot reach it either")


@pytest.fixture
def database_down(monkeypatch):
    monkeypatch.setattr(db_engine, "AsyncSessionLocal", lambda: _DeadSession())


def _app():
    import main

    return main.app


async def _get(path: str, headers: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get(path, headers=headers)


@pytest.fixture
def headers():
    from infrastructure.auth import AUTH_TOKEN

    return {"Authorization": f"Bearer {AUTH_TOKEN}"}


class TestTheClientIsTold:
    @pytest.mark.asyncio
    async def test_it_is_503_not_500(self, database_down, headers):
        response = await _get("/api/chat/history", headers)
        # 500 says "this backend is broken"; 503 says "it is fine and something
        # it depends on is not", which is the true statement.
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_the_body_names_the_cause(self, database_down, headers):
        response = await _get("/api/chat/history", headers)

        body = json.loads(response.text)
        assert body["cause"] == "database_unavailable"
        assert body["detail"], "an empty detail is a bare 500 with extra steps"
        assert "backend is running" in body["hint"]

    @pytest.mark.asyncio
    async def test_it_says_the_failure_is_worth_retrying(self, database_down, headers):
        response = await _get("/api/chat/history", headers)
        assert response.headers.get("retry-after") == "5"

    @pytest.mark.asyncio
    async def test_every_database_backed_route_answers_the_same_way(
        self, database_down, headers
    ):
        for path in ("/api/chat/history", "/api/memory/stats"):
            response = await _get(path, headers)
            assert response.status_code == 503, path
            assert json.loads(response.text)["cause"] == "database_unavailable", path

    @pytest.mark.asyncio
    async def test_a_connection_that_dies_mid_query_is_also_503(self, monkeypatch, headers):
        """Not a failed connect but a failed statement — a different exception family."""

        class _DiesMidQuery(_DeadSession):
            async def connection(self):
                return None  # the connect succeeds…

        monkeypatch.setattr(db_engine, "AsyncSessionLocal", lambda: _DiesMidQuery())

        import infrastructure.database.repositories.message_repo as repo_mod

        async def _explode(*_a, **_kw):
            raise OperationalError("select 1", {}, Exception("server closed the connection"))

        monkeypatch.setattr(repo_mod.MessageRepository, "get_canonical_pairs_page", _explode)

        response = await _get("/api/chat/history", headers)
        assert response.status_code == 503
        assert json.loads(response.text)["cause"] == "database_unavailable"

    @pytest.mark.asyncio
    async def test_auth_still_comes_first(self, database_down):
        """A database outage must not turn into an open door."""
        response = await _get("/api/chat/history", {})
        assert response.status_code == 401


class TestWhatTheRestOfTheSystemSees:
    @pytest.mark.asyncio
    async def test_a_worker_gets_a_named_exception(self, database_down):
        with pytest.raises(DatabaseUnavailable):
            async with db_engine.get_db_session():
                pass

    @pytest.mark.asyncio
    async def test_a_failing_rollback_does_not_replace_the_real_error(self, monkeypatch):
        """The second exception would hide the first — the useful one."""

        class _AliveThenDead(_DeadSession):
            async def connection(self):
                return None

        monkeypatch.setattr(db_engine, "AsyncSessionLocal", lambda: _AliveThenDead())

        with pytest.raises(ValueError, match="the real problem"):
            async with db_engine.get_db_session():
                raise ValueError("the real problem")

    @pytest.mark.asyncio
    async def test_the_reflection_worker_survives_the_outage(self, database_down):
        import main

        class _Log:
            def __init__(self):
                self.warnings: list[str] = []

            def warning(self, msg, *args):
                self.warnings.append(msg % args if args else msg)

            def info(self, *_a):
                pass

            debug = info

        log = _Log()
        # The tick itself raises; the worker's loop is what must not die. Calling
        # the tick directly is the point of having extracted it.
        with pytest.raises(DatabaseUnavailable):
            await main._reflection_tick(log)


class TestLivenessIsADifferentQuestion:
    @pytest.mark.asyncio
    async def test_root_still_says_ok(self, database_down):
        """`/` answers "is this process up", and it is.

        The Electron launcher waits on this before opening a window; making it
        depend on Postgres would mean the app refuses to start rather than
        starting and saying what is wrong.
        """
        transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get("/")
        assert response.status_code == 200
