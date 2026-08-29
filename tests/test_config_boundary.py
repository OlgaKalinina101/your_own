"""The line between the two configuration modules.

Run:
    python -m pytest tests/test_config_boundary.py -v

``settings.py`` is decided at deploy time and read from ``.env``;
``infrastructure/settings_store.py`` is decided by the person using the app and
written through the REST API. Two keys — ``history_pairs`` and
``memory_cutoff_days`` — appear in both, which looks like duplication and is
not: the *value* is the user's, the *bounds* are ours.

That arrangement only works while the two agree. If a default in the store ever
drifts outside the range in ``.env``, the clamp silently rewrites the user's
setting to something they never chose and nothing says a word. These tests hold
the two modules together.
"""
from __future__ import annotations

import json

import pytest

from api.chat import _clamp, _read_inputs
from infrastructure import settings_store
from settings import settings

# key in the store -> the trio in settings.py that bounds it
BOUNDED = {
    "history_pairs": (
        "CHAT_HISTORY_PAIRS_DEFAULT",
        "CHAT_HISTORY_PAIRS_MIN",
        "CHAT_HISTORY_PAIRS_MAX",
    ),
    "memory_cutoff_days": (
        "MEMORY_CUTOFF_DAYS_DEFAULT",
        "MEMORY_CUTOFF_DAYS_MIN",
        "MEMORY_CUTOFF_DAYS_MAX",
    ),
}


async def _inputs(**overrides):
    """Call the real form reader with everything defaulted to absent."""
    kwargs = dict(
        messages="[]", model=None, api_key=None, web_search="false",
        temperature=None, top_p=None, account_id=None, history_pairs=None,
        memory_cutoff_days=None, system_prompt=None, image_urls_json=None,
        image=None, images=None,
    )
    kwargs.update(overrides)
    return await _read_inputs(**kwargs)


class TestTheTwoModulesAgree:
    @pytest.mark.parametrize("key", sorted(BOUNDED))
    def test_the_stored_default_is_inside_the_deployed_range(self, key):
        default_name, min_name, max_name = BOUNDED[key]
        low = getattr(settings, min_name)
        high = getattr(settings, max_name)
        stored_default = settings_store._DEFAULTS[key]

        assert low <= stored_default <= high, (
            f"settings_store default {key}={stored_default} sits outside "
            f"{min_name}..{max_name} ({low}..{high}); the clamp would silently "
            f"replace it with something nobody chose"
        )

    @pytest.mark.parametrize("key", sorted(BOUNDED))
    def test_the_env_default_is_inside_its_own_range(self, key):
        default_name, min_name, max_name = BOUNDED[key]

        assert (
            getattr(settings, min_name)
            <= getattr(settings, default_name)
            <= getattr(settings, max_name)
        )

    @pytest.mark.parametrize("key", sorted(BOUNDED))
    def test_every_bounded_key_is_actually_offered_to_the_user(self, key):
        # A range in .env for something the store does not hold is a bound on
        # nothing — the sign that one side was removed and the other forgotten.
        assert key in settings_store._DEFAULTS


class TestWhatTheClampDoes:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("5", 5),
            ("999", 10),      # above the ceiling
            ("0", 1),         # below the floor
            ("-3", 1),
            (None, 6),        # absent: the default
            ("", 6),
            ("abc", 6),       # not a number: the default, not a crash
            ("3.7", 6),
        ],
    )
    def test_a_value_is_corrected_rather_than_refused(self, raw, expected):
        assert _clamp(raw, 6, 1, 10) == expected


class TestTheRequestOverridesTheStore:
    @pytest.mark.asyncio
    async def test_what_the_client_sends_wins(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings_store, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(settings_store, "_SETTINGS_FILE", tmp_path / "settings.json")
        monkeypatch.setattr(settings_store, "_SOUL_FILE", tmp_path / "soul.md")
        settings_store.save_settings({"history_pairs": 4})

        assert (await _inputs()).history_pairs == 4
        assert (await _inputs(history_pairs="9")).history_pairs == 9

    @pytest.mark.asyncio
    async def test_a_stored_value_out_of_range_is_pulled_back_in(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(settings_store, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(settings_store, "_SETTINGS_FILE", tmp_path / "settings.json")
        monkeypatch.setattr(settings_store, "_SOUL_FILE", tmp_path / "soul.md")
        # Settings are written by the API and could hold anything from an older
        # build or a hand-edited file.
        settings_store.save_settings({"history_pairs": 500})

        got = (await _inputs()).history_pairs

        assert got == settings.CHAT_HISTORY_PAIRS_MAX
        assert got != 500


class TestTheMessagesField:
    @pytest.mark.asyncio
    async def test_unparseable_messages_do_not_take_the_request_down(self):
        result = await _inputs(messages="{not json")

        assert result.messages == []
        assert result.user_text == ""

    @pytest.mark.asyncio
    async def test_the_last_user_message_is_the_one_that_counts(self):
        result = await _inputs(
            messages=json.dumps(
                [
                    {"role": "user", "content": "первый"},
                    {"role": "assistant", "content": "ответ"},
                    {"role": "user", "content": "последний"},
                ]
            )
        )

        assert result.user_text == "последний"
