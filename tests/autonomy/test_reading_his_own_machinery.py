"""What he can look up about himself: the documents, and his own prompts.

Run:
    python -m pytest tests/autonomy/test_reading_his_own_machinery.py -v

Two different questions, answered two different ways on purpose.

**Documentation** is prose about how he works, and a question like "how does my
memory decide what to surface" is best answered by a model that has read it —
that is what SEARCH_DOCS does. What was missing is that he had no idea what was
on the shelf: his command list said `[SEARCH_DOCS: query]` and nothing about
which documents exist.

**A prompt** is an artefact he wants exactly. Routing that through the research
agent would paraphrase it, cost a call, and risk distortion — a prompt retold is
a different prompt. So it is read directly, in the language he is thinking in.
"""
from __future__ import annotations

import asyncio

import pytest

import infrastructure.autonomy.reflection_engine as engine
from infrastructure.agents.sources import DOC_FILES
from infrastructure.llm.prompt_loader import catalogue, read_verbatim

PROMPT_FILES = (
    "infrastructure/autonomy/prompts/reflection_awakening.md",
    "infrastructure/autonomy/prompts/reflection_continuation.md",
    "infrastructure/autonomy/prompts/reflection_after_action.md",
)


def _prompt_text(rel: str) -> str:
    from infrastructure.paths import PROJECT_ROOT

    return (PROJECT_ROOT / rel).read_text(encoding="utf-8")


def _sections(rel: str) -> list[tuple[str, str]]:
    """(lang, text) for each language section of a prompt file.

    Checked per language, not over the whole file: he only ever reads one of
    them, and a list that is complete in English while the Russian half has
    gone stale is exactly the drift that would pass a whole-file check.
    """
    from infrastructure.llm.prompt_loader import load_prompt

    return [(lang, load_prompt(rel, lang=lang)) for lang in ("ru", "en")]


class TestHeKnowsWhatIsOnTheShelf:
    @pytest.mark.parametrize("rel", PROMPT_FILES)
    def test_the_documents_are_named_where_he_reads_the_command(self, rel):
        for lang, text in _sections(rel):
            for document in DOC_FILES:
                assert document in text, (
                    f"{rel} [{lang}]: {document} is searchable but never named — "
                    f"he has to guess what SEARCH_DOCS covers"
                )

    @pytest.mark.parametrize("rel", PROMPT_FILES)
    def test_both_reading_commands_are_offered(self, rel):
        for lang, text in _sections(rel):
            assert "[LIST_PROMPTS]" in text, f"{rel} [{lang}]"
            assert "SHOW_PROMPT" in text, f"{rel} [{lang}]"

    def test_the_agent_is_given_the_document_names_too(self):
        from infrastructure.agents.sources import _read_docs

        corpus, citations = _read_docs(DOC_FILES)

        for document in DOC_FILES:
            assert document in corpus, "the searcher cannot cite what it cannot name"
        assert len(citations) == len(DOC_FILES)


class TestTheShelfOfPrompts:
    def test_every_prompt_in_the_pipeline_is_addressable(self):
        shelf = catalogue()

        # Nothing hand-maintained: the catalogue is the directory. A prompt
        # added tomorrow is on the shelf without anyone remembering to list it.
        assert len(shelf) >= 21
        for name, path in shelf.items():
            assert path.endswith(".md")
            assert " " not in name, f"{name!r} cannot be typed in a command"

    def test_the_five_skills_do_not_collide_on_one_name(self):
        # Each of them calls its file prompt.md; the folder is what tells them
        # apart. Without that they would answer to one name and four of them
        # would be unreachable.
        shelf = catalogue()

        for skill in ("save_memory", "web_search", "generate_image",
                      "search_dialogue", "schedule_message"):
            assert f"skill_{skill}" in shelf

    def test_a_prompt_comes_back_whole_and_unformatted(self):
        body = read_verbatim("push_validator", "ru")

        assert "{dialogue_history}" in body, (
            "the placeholders are part of what it says — filling them in would "
            "show him a rendered prompt, not the prompt"
        )


class TestReadingOneOfThem:
    def test_it_arrives_in_the_language_he_asked_in(self):
        ru = engine._read_prompt("push_validator", "ru")
        en = engine._read_prompt("push_validator", "en")

        assert "Раньше ты запланировал" in ru
        assert "You previously scheduled" in en
        assert ru != en

    def test_a_name_that_does_not_exist_points_at_the_list(self):
        answer = engine._read_prompt("нет такого", "ru")

        assert "[LIST_PROMPTS]" in answer

    def test_a_long_prompt_is_cut_rather_than_left_to_eat_the_step(self, monkeypatch):
        # Driven by the rule, not by how big a file happens to be today: the
        # awakening prompt is 10 KB across both languages, so either section
        # alone currently fits.
        monkeypatch.setattr(engine, "PROMPT_MAX_CHARS", 400)

        body = engine._read_prompt("reflection_awakening", "ru")

        assert len(body) < 700
        assert "обрезано" in body, "cut in silence, so he cannot tell it is partial"

    def test_a_prompt_that_fits_is_not_touched(self):
        body = engine._read_prompt("key_info_impressive", "ru")

        assert "обрезано" not in body

    def test_the_list_names_every_prompt(self):
        listing = engine._list_prompts("ru")

        for name in catalogue():
            assert name in listing

    @pytest.mark.asyncio
    async def test_the_command_reaches_the_reader(self):
        answer = await engine._handle_command(
            "SHOW_PROMPT", "key_info_dedup", "default", "key", None, "ru"
        )

        assert answer and "key_info_dedup" in answer

    def test_the_parser_recognises_both_commands(self):
        from infrastructure.autonomy.commands import (
            LEAKABLE_COMMANDS,
            REFLECTION_COMMANDS,
        )

        assert "SHOW_PROMPT" in REFLECTION_COMMANDS
        assert "LIST_PROMPTS" in LEAKABLE_COMMANDS
        assert engine._LIST_PROMPTS_RE.search("Посмотрю. [LIST_PROMPTS]")
        assert engine._CMD_RE.search("[SHOW_PROMPT: push_validator]")


def test_a_bare_list_command_cannot_be_swallowed_by_an_unclosed_one():
    # Same guard as SLEEP and VITALS: a reply cut off mid-command must not eat
    # the intact command that follows it.
    text = "[WRITE_NOTE: оборвалось\n[LIST_PROMPTS]"

    assert engine._LIST_PROMPTS_RE.search(text) is not None
    assert [m.group("cmd") for m in engine._CMD_RE.finditer(text)] == []


if __name__ == "__main__":
    asyncio.run(asyncio.sleep(0))
