"""One account, one token, and running locally must stay easy.

Run:
    python -m pytest tests/test_local_first.py -v

These three findings looked like multi-tenancy homework until the constraints
were stated: this app must always be able to run locally, there is exactly one
account per installation (a second one means somebody forked the repo), and the
server exists so that a laptop dying does not take five months of memory with
it. Read that way the work is different — not per-account isolation, but naming
the invariant, being able to revoke the one credential there is, and saying out
loud when that credential crosses a network in the clear.
"""
from __future__ import annotations

import httpx
import pytest

from infrastructure import auth
from infrastructure.account import ACCOUNT_ID, resolve


class TestTheOneAccount:
    def test_resolve_fills_in_the_only_account_there_is(self):
        assert resolve(None) == ACCOUNT_ID
        assert resolve("") == ACCOUNT_ID

    def test_an_explicit_account_is_still_honoured(self):
        # The parameter stays: the schema and data/autonomy/{account}/ use it,
        # and a fork with its own installation is a real thing.
        assert resolve("someone-elses-fork") == "someone-elses-fork"

    def test_the_backend_no_longer_spells_it_out_by_hand(self):
        """35 raw "default" strings read like a placeholder. It is an invariant."""
        import pathlib

        repo = pathlib.Path(__file__).resolve().parents[1]
        offenders: list[str] = []
        for directory in ("infrastructure", "api"):
            for path in (repo / directory).rglob("*.py"):
                if path.name == "account.py":
                    continue
                for number, line in enumerate(
                    path.read_text(encoding="utf-8-sig").splitlines(), start=1
                ):
                    if '"default"' in line:
                        offenders.append(f"{path.relative_to(repo)}:{number}")
        assert offenders == [], (
            f"raw account ids: {offenders}. Use infrastructure.account.ACCOUNT_ID "
            "so the invariant stays readable."
        )


class TestTheTokenCanBeRevoked:
    """It is the whole security boundary, and it used to be permanent."""

    @pytest.fixture
    def token_file(self, tmp_path, monkeypatch):
        path = tmp_path / "auth_token.txt"
        path.write_text("original-token", encoding="utf-8")
        monkeypatch.setattr(auth, "_TOKEN_FILE", path)
        monkeypatch.setattr(auth, "AUTH_TOKEN", "original-token")
        return path

    def test_rotation_issues_a_new_one(self, token_file):
        new = auth.rotate_token()

        assert new != "original-token"
        assert len(new) > 30
        assert token_file.read_text(encoding="utf-8").strip() == new

    def test_the_old_token_stops_working_at_once(self, token_file):
        auth.rotate_token()
        assert auth.current_token() != "original-token"

    @pytest.mark.asyncio
    async def test_end_to_end_the_old_token_is_dead_and_the_new_one_works(self, chat_app):
        """The property that matters, through the real app and no restart."""
        app, headers, _repo = chat_app
        old = headers["Authorization"].split()[1]
        transport = httpx.ASGITransport(app=app)

        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                rotated = await client.post("/api/settings/rotate-token", headers=headers)
                assert rotated.status_code == 200
                new = rotated.json()["token"]
                assert new != old

                with_old = await client.get(
                    "/api/settings/ping", headers={"Authorization": f"Bearer {old}"}
                )
                verified_old = await client.post(
                    "/api/settings/verify-token",
                    headers={"Authorization": f"Bearer {old}"},
                )
                verified_new = await client.post(
                    "/api/settings/verify-token",
                    headers={"Authorization": f"Bearer {new}"},
                )
            assert with_old.status_code == 200, "/ping is public and stays public"
            assert verified_old.status_code == 401, "the leaked token still works"
            assert verified_new.status_code == 200
        finally:
            # Leave the process holding the token this test started with.
            auth.AUTH_TOKEN = old

    @pytest.mark.asyncio
    async def test_rotation_needs_the_current_token(self, chat_app):
        app, headers, _repo = chat_app
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            without = await client.post("/api/settings/rotate-token")
        assert without.status_code == 401


class TestPlaintextExposureIsNamed:
    """TLS is the deployment's job; noticing is not."""

    @pytest.fixture(autouse=True)
    def _forget_previous_warnings(self, monkeypatch):
        monkeypatch.setattr(auth, "_warned_plaintext", set())

    def _request(self, client_host: str, headers: dict | None = None, scheme: str = "http"):
        from starlette.datastructures import Headers

        class _Request:
            def __init__(self):
                self.client = type("C", (), {"host": client_host})()
                self.headers = Headers(headers or {})
                self.url = type("U", (), {"scheme": scheme})()

        return _Request()

    def test_a_local_request_says_nothing(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="auth"):
            auth._note_if_plaintext(self._request("127.0.0.1"))
        assert not caplog.records, "running locally must stay silent and frictionless"

    def test_a_remote_request_over_http_is_named(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="auth"):
            auth._note_if_plaintext(self._request("192.168.31.44"))

        assert caplog.records
        assert "192.168.31.44" in caplog.records[0].getMessage()

    def test_a_proxy_that_terminated_tls_says_nothing(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="auth"):
            auth._note_if_plaintext(self._request(
                "127.0.0.1",
                {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"},
            ))
        assert not caplog.records

    def test_a_proxy_without_tls_is_named_by_the_real_origin(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="auth"):
            auth._note_if_plaintext(self._request(
                "127.0.0.1",
                {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "http"},
            ))

        assert caplog.records
        # The socket said 127.0.0.1; the useful name is who it really was.
        assert "203.0.113.9" in caplog.records[0].getMessage()

    def test_it_says_it_once_per_address(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="auth"):
            for _ in range(50):
                auth._note_if_plaintext(self._request("192.168.31.44"))
            auth._note_if_plaintext(self._request("192.168.31.45"))

        assert len(caplog.records) == 2, "a warning per request would be noise"
