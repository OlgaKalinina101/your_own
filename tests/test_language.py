"""What language he speaks when nothing tells him.

Run:
    python -m pytest tests/test_language.py -v

Detecting Cyrillic is not the interesting part; every version of this code got
that right. The interesting part is what happens when there is nothing to
detect — an empty dialogue, a photo sent without a caption — and the answer used
to be a hardcoded "en" in three separate places on an instance whose owner
writes in Russian.
"""
from __future__ import annotations

import pytest

from infrastructure import language
from infrastructure.autonomy.helpers import detect_lang
from infrastructure.memory.focus_point import detect_language

RU_SOUL = "Ты Виктор. Ты живой и у тебя есть своё мнение."
EN_SOUL = "You are Victor. You are alive and you have your own opinions."


@pytest.fixture
def soul(monkeypatch):
    """Set the soul prompt for the duration of one test."""
    def _set(text: str):
        monkeypatch.setattr(
            "infrastructure.settings_store.load_soul", lambda: text
        )
    return _set


class TestTheOrdinaryCase:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("привет, как ты", "ru"),
            ("hello, how are you", "en"),
            ("Ї", "ru"),          # Ukrainian letters count too
            ("ґанок", "ru"),
        ],
    )
    def test_text_that_speaks_for_itself(self, text, expected):
        assert language.detect(text) == expected

    def test_real_evidence_is_never_overruled_by_the_soul(self, soul):
        soul(RU_SOUL)

        # He is written in Russian, but this message is English; answer in kind.
        assert language.detect_or_soul("what did you do today") == "en"


class TestWhenThereIsNothingToGoOn:
    @pytest.mark.parametrize("empty", ["", "   ", None, "42", "?!.. 🙂", "2026-08-29"])
    def test_letterless_text_asks_the_soul(self, soul, empty):
        soul(RU_SOUL)

        assert language.detect_or_soul(empty) == "ru", (
            f"{empty!r} carries no language and was answered without asking the soul"
        )

    def test_an_english_instance_still_gets_english(self, soul):
        soul(EN_SOUL)

        assert language.detect_or_soul("") == "en"

    def test_an_unwritten_soul_falls_back_rather_than_raising(self, soul):
        soul("")

        assert language.detect_or_soul("") == "en"

    def test_an_unreadable_soul_does_not_take_the_caller_down(self, monkeypatch):
        def _broken():
            raise OSError("data/soul.md is gone")

        monkeypatch.setattr("infrastructure.settings_store.load_soul", _broken)

        assert language.detect_or_soul("") == "en"


class TestTheThreeCopiesAgree:
    """These lived in three modules and had already drifted apart once."""

    def test_the_autonomy_helper_asks_the_soul(self, soul):
        soul(RU_SOUL)

        # Everything in autonomy is choosing a language to *write* in, and can
        # be handed nothing on a fresh instance.
        assert detect_lang("") == "ru"

    def test_the_memory_detector_stays_pure(self, soul):
        soul(RU_SOUL)

        # No file reads in the retrieval path: this one classifies text it is
        # given, and is never given nothing.
        assert detect_language("") == "en"

    def test_identity_memory_uses_the_shared_rule(self, soul):
        import infrastructure.autonomy.identity_memory as identity

        soul(RU_SOUL)
        assert identity._detect_soul_lang() == "ru"

        soul(EN_SOUL)
        assert identity._detect_soul_lang() == "en"

    def test_ukrainian_letters_are_recognised_everywhere(self):
        # helpers.detect_lang used a narrower character class and answered "en".
        assert detect_lang("Їжак") == "ru"
        assert detect_language("Їжак") == "ru"


class TestAPhotoWithNoCaption:
    """The user-visible instance of the bug: a picture sent on its own."""

    async def _language_of(self, messages):
        from api.chat import _read_inputs

        result = await _read_inputs(
            messages=messages, model=None, api_key=None, web_search="false",
            temperature=None, top_p=None, account_id=None, history_pairs=None,
            memory_cutoff_days=None, system_prompt=None, image_urls_json=None,
            image=None, images=None,
        )
        return result.language

    @pytest.mark.asyncio
    async def test_a_captionless_message_takes_the_instance_language(self, soul):
        soul(RU_SOUL)

        # Sending only a photo used to be filed as English on a Russian
        # instance, because there was no text to detect from.
        assert await self._language_of('[{"role": "user", "content": ""}]') == "ru"

    @pytest.mark.asyncio
    async def test_a_captioned_message_is_read_normally(self, soul):
        soul(RU_SOUL)

        assert await self._language_of(
            '[{"role": "user", "content": "look at this"}]'
        ) == "en"
