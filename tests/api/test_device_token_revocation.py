"""A phone that walks away must be able to say so.

`clearAuth()` on the phone used to wipe the address and the token and leave the
device token behind on the server, which then went on pushing to a device that
no longer talked to it — silently, because a push nobody receives raises
nothing anywhere.

The fix is one line on the phone, and it rests entirely on a property of
`put_settings` that nothing pinned: the patch filter drops `None`, **not** the
empty string. So `{"pushy_device_token": ""}` is a revocation and not an
omission. Tighten that filter to `if v` some day — a perfectly reasonable-looking
cleanup — and revocation goes back to being a no-op with no failure to notice.

These tests exist so that cleanup fails here instead.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch) -> Path:
    """Point the settings store at a scratch file for the duration."""
    from infrastructure import settings_store

    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(settings_store, "_SETTINGS_FILE", path)
    return path


@pytest.fixture
def headers() -> dict:
    from infrastructure.auth import AUTH_TOKEN

    return {"Authorization": f"Bearer {AUTH_TOKEN}"}


async def _put(body: dict, headers: dict) -> httpx.Response:
    import main

    transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.put("/api/settings", json=body, headers=headers)


class TestRevocation:
    @pytest.mark.asyncio
    async def test_an_empty_device_token_clears_the_stored_one(
        self, settings_file, headers
    ) -> None:
        from infrastructure.settings_store import load_settings, save_settings

        save_settings({"pushy_device_token": "device-abc"})
        assert load_settings()["pushy_device_token"] == "device-abc"

        response = await _put({"pushy_device_token": ""}, headers)

        assert response.status_code == 200
        assert load_settings()["pushy_device_token"] == ""

    @pytest.mark.asyncio
    async def test_omitting_the_field_leaves_it_alone(
        self, settings_file, headers
    ) -> None:
        """The distinction the filter exists for.

        Every save from the settings screen sends a partial patch; if a missing
        field meant "clear it", saving the model name would unregister the phone.
        """
        from infrastructure.settings_store import load_settings, save_settings

        save_settings({"pushy_device_token": "device-abc"})

        response = await _put({"ai_name": "Виктор"}, headers)

        assert response.status_code == 200
        stored = load_settings()
        assert stored["pushy_device_token"] == "device-abc"
        assert stored["ai_name"] == "Виктор"

    def test_a_revoked_token_means_no_push_client(self, settings_file) -> None:
        """Revocation has to reach the thing that actually sends.

        `get_client()` is the only place a push is built, and it is what turns a
        cleared token into "no pushes" rather than "pushes to nowhere".
        """
        from infrastructure.pushy.client import get_client
        from infrastructure.settings_store import save_settings

        save_settings({"pushy_api_key": "key", "pushy_device_token": "device-abc"})
        assert get_client() is not None

        save_settings({"pushy_device_token": ""})
        assert get_client() is None
