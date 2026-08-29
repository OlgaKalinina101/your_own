"""Media routes must be reachable by a tag that cannot send a header.

The bug this guards against was silent: closing B2 put `Depends(require_auth)`
on the three media routes, and every `<img src=…>` in both clients started
getting 401 — hidden by an `onError` handler that sets `display: none`. Nothing
logged, nothing raised; images simply stopped appearing.

So the assertions here are deliberately about *the shape of the request a
browser actually makes*: no Authorization header, everything in the URL.
"""
from __future__ import annotations

import time

import httpx
import pytest

from infrastructure import auth


@pytest.fixture
def sig() -> str:
    signature, _ttl = auth.issue_media_signature()
    return signature


class TestSignatureItself:
    def test_a_fresh_signature_verifies(self, sig: str) -> None:
        assert auth._media_signature_valid(sig)

    def test_an_expired_signature_does_not(self) -> None:
        expired, _ = auth.issue_media_signature(ttl_seconds=-1)
        assert not auth._media_signature_valid(expired)

    def test_garbage_does_not_verify(self) -> None:
        for bad in ["", None, "nonsense", "123", "123.", ".abc", "abc.def"]:
            assert not auth._media_signature_valid(bad), bad

    def test_the_signature_is_not_the_token(self, sig: str) -> None:
        """If it ever *is* the token, the whole point is lost."""
        assert auth.current_token() not in sig

    def test_tampering_with_the_expiry_breaks_it(self, sig: str) -> None:
        expires, _, digest = sig.partition(".")
        forged = f"{int(expires) + 3600}.{digest}"
        assert not auth._media_signature_valid(forged)

    def test_rotating_the_token_invalidates_outstanding_signatures(
        self, sig: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Free revocation: the key is the token, so rotation kills old links."""
        assert auth._media_signature_valid(sig)
        monkeypatch.setattr(auth, "AUTH_TOKEN", "a-different-token-entirely")
        assert not auth._media_signature_valid(sig)

    def test_it_expires_within_the_advertised_ttl(self) -> None:
        signature, ttl = auth.issue_media_signature(ttl_seconds=60)
        expires_at = int(signature.partition(".")[0])
        assert 0 < expires_at - int(time.time()) <= ttl


def _app():
    import main

    return main.app


MEDIA_ROUTES = [
    "/api/generated_images/nope.png",
    "/api/user_uploads/nope.png",
    "/api/body/nope.png",
]


class TestTheRequestABrowserActuallyMakes:
    """No Authorization header — because `<img>` cannot send one."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", MEDIA_ROUTES)
    async def test_bare_request_is_still_rejected(self, path: str) -> None:
        transport = httpx.ASGITransport(app=_app(), client=("127.0.0.1", 5555))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get(path)
        assert response.status_code == 401, "B2 must stay closed"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", MEDIA_ROUTES)
    async def test_a_signature_gets_past_the_gate(self, path: str, sig: str) -> None:
        """404, not 401: the file is absent, but auth was accepted.

        This is the assertion that would have caught the regression — the
        clients never send a header here, and before the signature existed
        every one of these was 401.
        """
        transport = httpx.ASGITransport(app=_app(), client=("127.0.0.1", 5555))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get(path, params={"sig": sig})
        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", MEDIA_ROUTES)
    async def test_an_expired_signature_is_rejected(self, path: str) -> None:
        expired, _ = auth.issue_media_signature(ttl_seconds=-1)
        transport = httpx.ASGITransport(app=_app(), client=("127.0.0.1", 5555))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get(path, params={"sig": expired})
        assert response.status_code == 401


class TestTheSignatureOpensNothingElse:
    """A media signature is not a token. Everything else stays header-only."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path", ["/api/settings/raw", "/api/settings/skills", "/api/memory/stats"]
    )
    async def test_sig_does_not_unlock_ordinary_routes(self, path: str, sig: str) -> None:
        transport = httpx.ASGITransport(app=_app(), client=("127.0.0.1", 5555))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get(path, params={"sig": sig})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_the_issuing_endpoint_itself_needs_the_token(self) -> None:
        transport = httpx.ASGITransport(app=_app(), client=("127.0.0.1", 5555))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get("/api/settings/media-signature")
        assert response.status_code == 401
