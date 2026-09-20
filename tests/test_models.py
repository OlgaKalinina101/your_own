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

import pytest

from infrastructure.llm.client import MODEL_INPUTS, VISION_MODELS, accepts, kind_of
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


def _picker() -> list[str]:
    """Every model id offered by the web settings picker.

    It used to carry a ``vision`` flag too, and that flag was a third copy of
    the capability table — one that could only say yes or no to photographs,
    and said "no" for the model that reads PDFs. The picker now asks
    modelInputs.ts at the point of display, so there is nothing here to drift.
    """
    source = WEB_SETTINGS.read_text(encoding="utf-8")
    match = re.search(r"const MODELS = \[(.*?)\] as const;", source, re.DOTALL)
    assert match, "MODELS not found in the settings page"
    return re.findall(r'\{\s*id:\s*"([^"]+)"', match.group(1))


class TestTheModelsOnOffer:
    def test_the_picker_offers_exactly_what_was_chosen(self):
        assert set(_picker()) == CHOSEN

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

    @pytest.mark.parametrize("path", [WEB_CHAT, MOBILE_CHAT], ids=["web", "mobile"])
    def test_neither_chat_controller_keeps_a_copy_of_its_own(self, path):
        """Both used to declare their own `VISION_MODELS` literal, and both had
        already drifted from the backend and from each other. They now re-export
        the one in modelInputs.ts; a literal reappearing here is the drift
        starting over."""
        source = path.read_text(encoding="utf-8")
        assert "const VISION_MODELS = new Set([" not in source
        assert "modelInputs" in source

    def test_the_picker_shows_what_each_model_reads_rather_than_a_flag(self):
        """The settings list used to carry its own vision:true/false. It said
        "false" for GLM, which reads PDFs, so the one model whose limits needed
        explaining was the one described wrong."""
        source = WEB_SETTINGS.read_text(encoding="utf-8")
        assert "vision:" not in source
        assert "describeAccepted" in source

    def test_the_text_only_model_is_not_offered_a_photograph(self):
        # Named on its own because it is the one asymmetry, and the next person
        # adding a model will look for a reason it might be absent.
        assert "~z-ai/glm-latest" not in VISION_MODELS


class TestWhatElseEachModelTakes:
    """Beyond photographs: documents, sound and video.

    Every claim here was established by sending the real thing through
    OpenRouter — see the note over MODEL_INPUTS. The catalogue is wrong in both
    directions, so these are not transcribed from it and must not be "corrected"
    to match it; changing one means running the probe again.
    """

    def test_the_table_covers_exactly_the_models_on_offer(self):
        # A model in the picker but not in the table accepts nothing, and the
        # attachment is dropped with only a log line to show for it.
        assert set(MODEL_INPUTS) == CHOSEN

    def test_vision_is_derived_from_the_table_not_kept_beside_it(self):
        # The two used to be separate constants; one could be edited alone.
        assert VISION_MODELS == {m for m, kinds in MODEL_INPUTS.items() if "image" in kinds}

    def test_gemini_is_the_only_one_that_hears_and_watches(self):
        for kind in ("audio", "video"):
            takes = {m for m in CHOSEN if accepts(m, kind)}
            assert takes == {"~google/gemini-pro-latest"}, kind

    def test_kimi_is_not_offered_video_whatever_the_catalogue_says(self):
        """Its entry advertises video; asked to watch one it answers that it
        cannot. Believing the catalogue here costs a minute of the person's
        time and gives them an answer about nothing."""
        assert not accepts("~moonshotai/kimi-latest", "video")
        assert accepts("~moonshotai/kimi-latest", "image")

    def test_every_one_of_them_reads_a_pdf(self):
        """Including the model that cannot see — OpenRouter parses the PDF
        before any provider is reached, so this is not a vision capability and
        must not be tied to one."""
        assert all(accepts(m, "pdf") for m in CHOSEN)

    def test_every_one_of_them_reads_a_text_file(self):
        # Because it is not sent as an attachment at all; it becomes prose.
        assert all(accepts(m, "text") for m in CHOSEN)

    def test_the_model_that_cannot_see_still_reads(self):
        # It was previously written down as taking nothing, which was wrong and
        # would have had her documents dropped on the one model she reads on.
        assert not accepts("~z-ai/glm-latest", "image")
        assert accepts("~z-ai/glm-latest", "pdf")

    def test_nothing_accepts_a_format_nobody_can_open(self):
        # .docx and archives: refused mid-conversation if sent, so never sent.
        assert not any(accepts(m, "other") for m in CHOSEN)

    def test_a_model_nobody_has_heard_of_is_offered_nothing(self):
        # Not an exception: a slug added to the picker tomorrow has no evidence
        # behind it yet, and text-only is the failure that still answers.
        assert not accepts("some/model-that-does-not-exist", "image")


WEB_INPUTS = ROOT / "frontend" / "lib" / "modelInputs.ts"
MOBILE_INPUTS = ROOT / "mobile" / "lib" / "modelInputs.ts"


def _ts_model_inputs(path: pathlib.Path) -> dict[str, set[str]]:
    """The MODEL_INPUTS literal out of a TypeScript source."""
    source = path.read_text(encoding="utf-8")
    match = re.search(
        r"export const MODEL_INPUTS: Record<string, InputKind\[\]> = \{(.*?)^\};",
        source, re.DOTALL | re.MULTILINE,
    )
    assert match, f"MODEL_INPUTS not found in {path.name} — has it been renamed?"
    return {
        model: set(re.findall(r'"([^"]+)"', kinds))
        for model, kinds in re.findall(r'"([^"]+)":\s*\[([^\]]*)\]', match.group(1))
    }


class TestTheClientsKnowTheSameTableAsTheBackend:
    """Three copies of it — Python, web, phone — and no import between them.

    The phone and the web build from separate trees, so the duplication is real
    and only a test can hold it together. Drift is silent in both directions:
    a kind missing on the client greys out a button she could have used, a kind
    missing in Python has the file dropped after she watched it upload.
    """

    @pytest.mark.parametrize("path", [WEB_INPUTS, MOBILE_INPUTS], ids=["web", "mobile"])
    def test_the_table_matches_python_exactly(self, path):
        assert _ts_model_inputs(path) == {m: set(k) for m, k in MODEL_INPUTS.items()}

    def test_the_two_client_copies_are_the_same_file(self):
        """Byte-identical but for the one line naming the other copy. Anything
        else means someone edited one of them and not the other."""
        web = WEB_INPUTS.read_text(encoding="utf-8").splitlines()
        mobile = MOBILE_INPUTS.read_text(encoding="utf-8").splitlines()
        differing = [(a, b) for a, b in zip(web, mobile) if a != b]
        assert len(web) == len(mobile)
        assert len(differing) == 1, f"copies differ on {len(differing)} lines"
        assert "duplicated at" in differing[0][0]


class TestWhichKindAMimeTypeIs:
    def test_the_three_that_travel_as_media(self):
        assert kind_of("image/png") == "image"
        assert kind_of("audio/mpeg") == "audio"
        assert kind_of("video/mp4") == "video"

    def test_a_pdf_is_its_own_kind_and_not_a_document_in_general(self):
        """Lumping the two together is what the first version did, and it is
        wrong in both directions: a PDF in a file part every model reads, a
        .txt in the same part four of the five refuse."""
        assert kind_of("application/pdf") == "pdf"
        assert kind_of("text/plain") == "text"

    @pytest.mark.parametrize(
        "mime", ["text/markdown", "text/csv", "application/json", "application/x-yaml"],
    )
    def test_text_that_does_not_say_text(self, mime):
        # Half of what a person attaches as "a text file" has a MIME type that
        # does not begin with text/ — a .json from a picker is the common one.
        assert kind_of(mime) == "text"

    def test_a_format_nobody_can_open_says_so(self):
        assert kind_of("application/vnd.openxmlformats-officedocument.wordprocessingml.document") == "other"
        assert kind_of("application/zip") == "other"

    def test_the_type_is_read_case_insensitively(self):
        # Android's picker has handed us "IMAGE/JPEG" — upper case there meant
        # the photo was classified as a document and sent as a file part.
        assert kind_of("IMAGE/JPEG") == "image"

    def test_a_charset_on_the_end_does_not_hide_the_type(self):
        # "text/plain; charset=utf-8" is what a browser sends, and comparing it
        # whole against "text/plain" makes it an unopenable format.
        assert kind_of("text/plain; charset=utf-8") == "text"
        assert kind_of("application/pdf; qs=0.001") == "pdf"

    def test_a_missing_type_does_not_raise(self):
        assert kind_of("") == "other"
        assert kind_of(None) == "other"


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
