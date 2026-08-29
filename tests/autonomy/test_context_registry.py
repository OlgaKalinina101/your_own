"""Every consumer's set of state sections, against one list.

Run:
    python -m pytest tests/autonomy/test_context_registry.py -v

The precedent this exists for: a section was added to the identity file and
reached two of the four prompts. Nothing failed, nothing logged, and the gap was
found by reading. A section is now a row in the registry, and the expected
matrix below is the second copy — so adding a row without deciding who gets it
fails here rather than in production three weeks later.

EXPECTED is written out by hand on purpose. A test that derives its expectation
from the code under test agrees with any change, including a wrong one.
"""
from __future__ import annotations

import pytest

from infrastructure.autonomy import context
from infrastructure.autonomy.context import Consumer
from infrastructure.llm.prompt_loader import load_prompt

# consumer -> exactly the sections it may receive
EXPECTED: dict[Consumer, set[str]] = {
    Consumer.CHAT: {
        "canon", "workbench", "open_threads", "current_time", "timezone_label",
    },
    Consumer.REFLECTION: {
        "identity", "workbench", "open_threads", "vitals",
        "current_time", "timezone_label",
    },
    Consumer.POST_ANALYSIS: {
        "identity", "workbench", "open_threads", "current_time", "timezone_label",
    },
    Consumer.PUSH_VALIDATION: {
        "workbench", "open_threads", "current_time", "timezone_label",
    },
}

# Which .md each consumer fills, and which subsection of it. Chat assembles its
# system prompt in code and so has no single template. The awakening prompt is
# one whole block rather than system/user halves, hence the None.
TEMPLATES = {
    Consumer.REFLECTION: ("infrastructure/autonomy/prompts/reflection_awakening.md", None),
    Consumer.POST_ANALYSIS: ("infrastructure/autonomy/prompts/post_analyzer.md", "user"),
    Consumer.PUSH_VALIDATION: ("infrastructure/autonomy/prompts/push_validator.md", "user"),
}


class TestTheMatrixIsWhatWeSaidItIs:
    @pytest.mark.parametrize("consumer", list(Consumer))
    def test_each_consumer_gets_exactly_its_sections(self, consumer):
        assert context.section_names(consumer) == EXPECTED[consumer]

    def test_every_registered_section_reaches_someone(self):
        # A row nobody consumes is a section that is being rendered for nothing.
        orphans = [s.name for s in context.SECTIONS if not s.consumers]
        assert orphans == [], f"sections with no consumer: {orphans}"

    def test_a_new_section_has_to_declare_its_consumers(self):
        registered = {s.name for s in context.SECTIONS}
        declared = set().union(*EXPECTED.values())
        assert registered == declared, (
            "the registry and the expected matrix disagree — if a section was "
            f"added or removed, update EXPECTED too. diff: {registered ^ declared}"
        )


class TestThePromptsHaveTheSlots:
    """A section a consumer is entitled to must have somewhere to land."""

    @pytest.mark.parametrize("consumer", list(TEMPLATES))
    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_template_has_a_placeholder_for_each_section(self, consumer, lang):
        path, subsection = TEMPLATES[consumer]
        template = load_prompt(path, lang=lang, section=subsection)
        missing = [
            name for name in EXPECTED[consumer]
            if "{" + name + "}" not in template
        ]
        assert missing == [], f"{path} ({lang}) has no slot for: {missing}"

    @pytest.mark.parametrize("consumer", list(TEMPLATES))
    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_ru_and_en_ask_for_the_same_sections(self, consumer, lang):
        other = "en" if lang == "ru" else "ru"
        path, subsection = TEMPLATES[consumer]
        here = load_prompt(path, lang=lang, section=subsection)
        there = load_prompt(path, lang=other, section=subsection)
        for name in EXPECTED[consumer]:
            assert ("{" + name + "}" in here) == ("{" + name + "}" in there), (
                f"section {name!r} is in the {lang} half but not the {other} one"
            )


class TestThePushValidatorRegression:
    """The one that was actually broken, stated as itself."""

    def test_it_sees_the_open_threads(self):
        # It decides whether to interrupt her. Not knowing what is still open
        # between them is exactly the wrong thing to be missing.
        assert "open_threads" in context.section_names(Consumer.PUSH_VALIDATION)

    def test_it_knows_what_time_it_is_for_her(self):
        assert "timezone_label" in context.section_names(Consumer.PUSH_VALIDATION)
        assert "current_time" in context.section_names(Consumer.PUSH_VALIDATION)


class TestRendering:
    @pytest.fixture
    def account(self, tmp_path, monkeypatch):
        import infrastructure.autonomy.identity_memory as identity
        import infrastructure.autonomy.threads as threads
        import infrastructure.autonomy.vitals as vitals
        import infrastructure.autonomy.workbench as workbench

        for module in (identity, threads, vitals, workbench):
            monkeypatch.setattr(module, "_DATA_DIR", tmp_path)
        return "default"

    @pytest.mark.parametrize("consumer", list(Consumer))
    def test_build_returns_every_entitled_section_and_nothing_else(self, consumer, account):
        built = context.build(consumer, context.Request(account_id=account, lang="ru"))
        assert set(built) == EXPECTED[consumer]

    def test_chat_drops_an_empty_block_and_the_others_name_it(self, account):
        chat = context.build(Consumer.CHAT, context.Request(account_id=account, lang="ru"))
        analysis = context.build(
            Consumer.POST_ANALYSIS, context.Request(account_id=account, lang="ru")
        )
        # Chat runs on every message; an empty board repeated a thousand times
        # is pure cost. Everywhere else, empty is worth saying.
        assert chat["open_threads"] == ""
        assert analysis["open_threads"] == "(пусто)"

    def test_reflection_reads_the_whole_desk_and_chat_the_last_few(self, account):
        import infrastructure.autonomy.workbench as workbench

        for i in range(6):
            workbench.append(account, f"заметка номер {i}")

        chat = context.build(Consumer.CHAT, context.Request(account_id=account))
        reflection = context.build(Consumer.REFLECTION, context.Request(account_id=account))

        assert chat["workbench"].count("<entry") == context.WORKBENCH_RECENT_ENTRIES
        assert "заметка номер 0" in reflection["workbench"]
        assert "заметка номер 0" not in chat["workbench"]

    def test_a_broken_section_costs_only_itself(self, account, monkeypatch, caplog):
        import logging

        def _explode(_request, _consumer):
            raise RuntimeError("disk gone")

        broken = context.Section(
            name="workbench",
            consumers=frozenset(Consumer),
            render=_explode,
        )
        monkeypatch.setattr(
            context, "SECTIONS",
            tuple(broken if s.name == "workbench" else s for s in context.SECTIONS),
        )

        with caplog.at_level(logging.WARNING):
            built = context.build(Consumer.CHAT, context.Request(account_id=account))

        assert built["workbench"] == ""
        assert built["current_time"], "one broken block took the rest with it"
        assert any("disk gone" in r.getMessage() for r in caplog.records)
