"""The three Telegram settings, on both sides of the API.

Run:
    python -m pytest tests/telegram/test_settings.py -v
"""
from __future__ import annotations

import pathlib
import re

import pytest

from infrastructure.settings_store import _DEFAULTS

ROOT = pathlib.Path(__file__).resolve().parents[2]
WEB_SETTINGS = ROOT / "frontend" / "app" / "dashboard" / "settings" / "page.tsx"
WEB_TYPES = ROOT / "frontend" / "lib" / "types.ts"

KEYS = ("telegram_bot_token", "telegram_chat_id", "telegram_owner_user_id")


class TestTheKeysExistEverywhere:
    @pytest.mark.parametrize("key", KEYS)
    def test_default_is_an_empty_string(self, key):
        """Strings, not ints: a group id is negative and a user id can be
        larger than a JavaScript number keeps exactly."""
        assert _DEFAULTS[key] == ""

    @pytest.mark.parametrize("key", KEYS)
    def test_the_patch_schema_accepts_it(self, key):
        from api.settings_api import SettingsPatch

        assert key in SettingsPatch.model_fields

    @pytest.mark.parametrize("key", KEYS)
    def test_the_desktop_page_reads_and_writes_it(self, key):
        source = WEB_SETTINGS.read_text(encoding="utf-8")
        assert f"data.{key}" in source, f"{key} is never loaded into the page"
        assert re.search(rf"\b{key}\b\s*:", source), f"{key} is never saved from the page"

    @pytest.mark.parametrize("key", KEYS)
    def test_the_type_knows_it(self, key):
        assert f"{key}?: string" in WEB_TYPES.read_text(encoding="utf-8")


class TestTheTokenIsMasked:
    @pytest.mark.asyncio
    async def test_get_settings_hides_the_bot_token(self, tmp_path, monkeypatch):
        from api.settings_api import get_settings
        from infrastructure import settings_store

        monkeypatch.setattr(settings_store, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(settings_store, "_SETTINGS_FILE", tmp_path / "settings.json")
        settings_store.save_settings({"telegram_bot_token": "123456789:AAEabcdefghijklmnop"})

        shown = await get_settings(_token="x")

        assert "AAEabcdefghijklmnop" not in shown["telegram_bot_token"]
        assert shown["telegram_bot_token"].startswith("1234")


class TestTheMigrationIsOnTheChain:
    def test_0011_follows_0010(self):
        source = (ROOT / "alembic" / "versions" / "0011_channel_messages.py").read_text(encoding="utf-8")
        assert 'revision = "0011"' in source
        assert 'down_revision = "0010"' in source

    def test_the_model_is_registered_for_alembic(self):
        import infrastructure.database.models as models

        assert "ChannelMessage" in models.__all__
