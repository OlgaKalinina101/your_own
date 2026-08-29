"""Every route is behind the token unless it is on the list below.

Run:
    python -m pytest tests/api/test_auth_coverage.py -v

The list is the point. An endpoint becomes public by being added here, in a
commit someone reads — not by being written with ``dependencies=[]`` and
noticed months later. That is how ``GET /api/settings/local-token`` came to
hand the master token to any caller whose socket looked local, which behind
this app's own Next.js rewrite meant anyone with the tunnel URL.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.routing import APIRoute
from starlette.routing import Mount

# Public on purpose:
#   /                          liveness for the Electron launcher
#   /api/settings/ping         reachability probe before a token exists
#   /api/startup/status        model-preload progress, shown on the splash.
#                              Note: it holds the connection open until preload
#                              reports done, so it is public *and* long-lived.
PUBLIC_ROUTES = {"/", "/api/settings/ping", "/api/startup/status"}

# FastAPI's own docs surface. Not our routes; disable in production separately.
DOCS_ROUTES = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


# Both count as "behind the token". The second accepts a short-lived signature
# in the query string *in addition to* the header, because `<img src=…>` cannot
# send a header — see the block above `issue_media_signature` in
# infrastructure/auth.py. It is not a way to be public: the signature is derived
# from the token and expires, and `TestMediaSignatureIsNotAWayOut` below pins
# that it opens nothing but the three media routes.
AUTH_DEPENDENCIES = {"require_auth", "require_auth_or_media_signature"}

# The only routes allowed to use the signature variant. Anything else appearing
# here is a hole: a signature is proof someone held the token recently, which is
# the right bar for fetching an image and the wrong bar for changing settings.
MEDIA_ROUTES = {
    "/api/generated_images/{filename:path}",
    "/api/user_uploads/{filename:path}",
    "/api/body/{filename:path}",
}


def _dependency_names(dependant) -> set[str]:
    names = {
        getattr(d.call, "__name__", "")
        for d in dependant.dependencies
        if d.call is not None
    }
    for d in dependant.dependencies:
        names |= _dependency_names(d)
    return names


def _requires_auth(dependant) -> bool:
    return bool(_dependency_names(dependant) & AUTH_DEPENDENCIES)


def _app():
    import main

    return main.app


class TestAuthCoverage:
    def test_every_route_needs_a_token(self):
        unprotected = [
            route.path
            for route in _app().routes
            if isinstance(route, APIRoute)
            and route.path not in PUBLIC_ROUTES | DOCS_ROUTES
            and not _requires_auth(route.dependant)
        ]
        assert unprotected == [], f"routes reachable without a token: {unprotected}"

    def test_the_public_list_has_not_quietly_grown(self):
        actual = {
            route.path
            for route in _app().routes
            if isinstance(route, APIRoute)
            and route.path not in DOCS_ROUTES
            and not _requires_auth(route.dependant)
        }
        assert actual == PUBLIC_ROUTES, (
            "the set of public endpoints changed; if that is intended, edit "
            f"PUBLIC_ROUTES in this file. Now public: {sorted(actual)}"
        )

    def test_there_are_no_static_mounts(self):
        # A Mount carries no dependency, so anything served that way is readable
        # by whoever reaches the port. /api/body served six fixed filenames
        # (anchor.png, listener.png, …) and was therefore fully enumerable.
        # They are ordinary authed routes now; a Mount reappearing is a hole.
        mounts = [route.path for route in _app().routes if isinstance(route, Mount)]
        assert mounts == [], f"unauthenticated mounts: {mounts}"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/generated_images/{f}",
            "/api/user_uploads/{f}",
            "/api/body/{f}",
        ],
    )
    def test_the_served_files_are_behind_the_token(self, path):
        routes = {
            route.path: route
            for route in _app().routes
            if isinstance(route, APIRoute)
        }
        route = routes[path.replace("{f}", "{filename:path}")]
        assert _requires_auth(route.dependant)


class TestMediaSignatureIsNotAWayOut:
    """The signature variant must stay confined to the three media routes.

    It exists for one reason — `<img>` cannot send a header — and widening it
    to an ordinary endpoint would turn a URL that lands in access logs and
    browser history into a way to read or change settings.
    """

    def test_only_media_routes_accept_a_signature(self):
        using_signature = {
            route.path
            for route in _app().routes
            if isinstance(route, APIRoute)
            and "require_auth_or_media_signature" in _dependency_names(route.dependant)
        }
        assert using_signature == MEDIA_ROUTES, (
            "the signature dependency spread beyond media: "
            f"{sorted(using_signature ^ MEDIA_ROUTES)}"
        )


class TestNoTokenHandout:
    """No unauthenticated GET may return the auth token, whatever the client looks like."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path", ["/api/settings/local-token", "/api/settings/ping", "/"]
    )
    async def test_public_responses_never_contain_the_token(self, path):
        from infrastructure.auth import AUTH_TOKEN

        # client=127.0.0.1 on purpose: that is exactly what a reverse proxy
        # looks like, and what the removed endpoint trusted.
        transport = httpx.ASGITransport(app=_app(), client=("127.0.0.1", 5555))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get(path)

        # 404 is the right answer for the endpoint that used to exist.
        assert AUTH_TOKEN not in response.text, f"{path} handed out the auth token"

    @pytest.mark.asyncio
    async def test_the_local_token_endpoint_stays_gone(self):
        transport = httpx.ASGITransport(app=_app(), client=("127.0.0.1", 5555))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get("/api/settings/local-token")
        assert response.status_code == 404


class TestServedFileContainment:
    """The traversal guard is ours now, not Starlette's, so it gets a test."""

    @pytest.fixture
    def served(self, tmp_path, monkeypatch):
        import main

        root = tmp_path / "uploads"
        root.mkdir()
        (root / "ok.png").write_bytes(b"PNG")
        (tmp_path / "OUTSIDE.txt").write_text("the auth token lives near here")
        monkeypatch.setitem(main._SERVED_DIRS, "user_uploads", root)
        return main

    def test_a_file_inside_the_root_is_served(self, served):
        assert served._served_file("user_uploads", "ok.png").name == "ok.png"

    @pytest.mark.parametrize(
        "attempt",
        [
            "../OUTSIDE.txt",
            "..\\OUTSIDE.txt",  # the Windows separator form
            "a/../../OUTSIDE.txt",
            "./../OUTSIDE.txt",
            "missing.png",
        ],
    )
    def test_anything_outside_the_root_is_404(self, served, attempt):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as caught:
            served._served_file("user_uploads", attempt)
        assert caught.value.status_code == 404
