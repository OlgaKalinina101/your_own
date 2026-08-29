"""The model list, and the three copies of "who can be shown a photograph".

Run:
    python -m pytest tests/test_models.py -v

The vision table lives in four places — the backend, the web chat page, the web
settings picker, and mobile — and before this pass they had already drifted:
the picker marked Gemini as text-only while the backend sent it images, and the
web chat page had no Claude Fable at all, so attaching a photo there was refused
by the client while the backend would have accepted it.

Both directions of that drift are silent. A model missing from the client's set
has its attach button quietly disabled; a model missing from the backend's set
has the picture dropped on the way out and the model answers about text it was
never shown. Neither raises anything.

These tests read the TypeScript literals directly. That is deliberate: what
matters is the constant a human edits, not what some build step makes of it.
"""
from __future__ import annotations

import pathlib
import re

from infrastructure.llm.client import VISION_MODELS
from infrastructure.settings_store import DEFAULT_MODEL, _DEFAULTS

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Moved out of app/chat/page.tsx when the desktop grew a controller, which put
# it at the same address as the phone's copy — the two lines below now differ
# only by platform, which is the point of the whole exercise.
WEB_CHAT = ROOT / "frontend" / "lib" / "useChatController.ts"
WEB_SETTINGS = ROOT / "frontend" / "app" / "dashboard" / "settings" / "page.tsx"
MOBILE_CHAT = ROOT / "mobile" / "lib" / "useChatController.ts"

# The five the owner chose, 2026-08-29. Spelled exactly as OpenRouter spells
# them: four carry a leading tilde and one does not. That is not a rule with an
# exception, it is simply how the catalogue lists them — see the test below.
CHOSEN = {
    "~anthropic/claude-fable-latest",
    "~moonshotai/kimi-latest",
    "~google/gemini-pro-latest",
    "~z-ai/glm-latest",
    "openai/gpt-chat-latest",
}
# ~z-ai/glm-latest takes text only — its input modalities in the catalogue are
# ["text"] alone, unlike the other four.
CHOSEN_WITH_VISION = CHOSEN - {"~z-ai/glm-latest"}


def _js_set(path: pathlib.Path, name: str) -> set[str]:
    """The string literals inside `const <name> = new Set([...])`."""
    source = path.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = new Set\(\[(.*?)\]\)", source, re.DOTALL)
    assert match, f"{name} not found in {path.name} — has it been renamed?"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _picker() -> list[tuple[str, bool]]:
    """(id, vision) for every entry of the web settings model picker."""
    source = WEB_SETTINGS.read_text(encoding="utf-8")
    match = re.search(r"const MODELS = \[(.*?)\] as const;", source, re.DOTALL)
    assert match, "MODELS not found in the settings page"
    return [
        (mid, flag == "true")
        for mid, flag in re.findall(
            r'\{\s*id:\s*"([^"]+)",\s*label:\s*"[^"]*",\s*vision:\s*(true|false)',
            match.group(1),
        )
    ]


class TestTheModelsOnOffer:
    def test_the_picker_offers_exactly_what_was_chosen(self):
        assert {mid for mid, _ in _picker()} == CHOSEN

    def test_the_default_is_one_of_them(self):
        assert DEFAULT_MODEL in CHOSEN

    def test_the_research_agent_uses_one_of_them(self):
        assert _DEFAULTS["research_model"] in CHOSEN

    def test_the_slugs_are_spelled_the_way_the_catalogue_spells_them(self):
        """No normalising, in either direction.

        The first version of this test asserted that every slug starts with a
        tilde — a rule generalised from the four models that happened to be
        chosen first. ``openai/gpt-chat-latest`` has no tilde, and it was
        never a rule: it is just how OpenRouter lists each model. Adding the
        tilde where there is none, or stripping it where there is one, gives an
        identifier the catalogue does not contain, and the provider answers the
        first message with an error.
        """
        assert sorted(CHOSEN) == [
            "openai/gpt-chat-latest",
            "~anthropic/claude-fable-latest",
            "~google/gemini-pro-latest",
            "~moonshotai/kimi-latest",
            "~z-ai/glm-latest",
        ]


class TestEveryCopyOfTheVisionTableAgrees:
    def test_the_backend_knows_which_of_them_can_see(self):
        assert VISION_MODELS == CHOSEN_WITH_VISION

    def test_the_web_chat_page_agrees_with_the_backend(self):
        assert _js_set(WEB_CHAT, "VISION_MODELS") == VISION_MODELS

    def test_mobile_agrees_with_the_backend(self):
        assert _js_set(MOBILE_CHAT, "VISION_MODELS") == VISION_MODELS

    def test_the_picker_flags_agree_with_the_backend(self):
        flagged = {mid for mid, vision in _picker() if vision}
        assert flagged == VISION_MODELS

    def test_the_text_only_model_is_not_offered_a_photograph(self):
        # Named on its own because it is the one asymmetry, and the next person
        # adding a model will look for a reason it might be absent.
        assert "~z-ai/glm-latest" not in VISION_MODELS


# ── Image generation ─────────────────────────────────────────────────────────

IMAGE_MODELS = {
    "sourceful/riverflow-v2.5-fast",
    "sourceful/riverflow-v2.5-pro",
    "openai/gpt-image-2",
    "google/gemini-3-pro-image",
    "black-forest-labs/flux.2-max",
    "x-ai/grok-imagine-image-2.0",
}


def _image_picker() -> set[str]:
    source = WEB_SETTINGS.read_text(encoding="utf-8")
    match = re.search(r"const IMAGE_GEN_MODELS = \[(.*?)\] as const;", source, re.DOTALL)
    assert match, "IMAGE_GEN_MODELS not found in the settings page"
    return set(re.findall(r'id:\s*"([^"]+)"', match.group(1)))


class TestTheImageModels:
    """Separate from the chat models on purpose: they are chosen separately and
    they answer a different endpoint shape."""

    def test_the_skill_offers_only_known_models(self):
        from infrastructure.skills.generate_image.skill import _MODEL_MAP

        assert set(_MODEL_MAP.values()) <= IMAGE_MODELS

    def test_the_fallback_is_one_of_them(self):
        from infrastructure.skills.generate_image.skill import _FALLBACK_MODEL

        assert _FALLBACK_MODEL in IMAGE_MODELS

    def test_the_body_default_is_one_of_them(self):
        assert _DEFAULTS["body_image_model"] in IMAGE_MODELS

    def test_the_picker_offers_only_known_models(self):
        assert _image_picker() <= IMAGE_MODELS

    def test_the_image_only_vendors_are_still_recognised(self):
        """Sourceful, Flux and Grok return an image and no text.

        Asking them for text alongside it gets neither — that was finding A8.
        A rename that moved one of them out from under its vendor prefix would
        be silent, so the rule is checked against the models actually in use.
        """
        from infrastructure.llm.client import modalities_for

        for model in ("sourceful/riverflow-v2.5-fast", "sourceful/riverflow-v2.5-pro",
                      "black-forest-labs/flux.2-max", "x-ai/grok-imagine-image-2.0"):
            assert modalities_for(model) == ["image"], model

    def test_the_two_that_also_speak_are_asked_for_both(self):
        from infrastructure.llm.client import modalities_for

        for model in ("openai/gpt-image-2", "google/gemini-3-pro-image"):
            assert modalities_for(model) == ["image", "text"], model
