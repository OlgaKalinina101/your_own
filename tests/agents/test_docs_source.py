"""Tests for the docs backend — reading the project's own documentation.

Same shape as the web probe: the searcher model gets the whole corpus and
answers in prose, so a single good pass needs no summarising step. The docs
are English and the questions arrive in Russian, which is why this is a model
call rather than a keyword match.

Pinned here:
  1. The corpus is assembled with file names and modification dates — docs
     drift behind code, and a visible age beats an assumed accuracy.
  2. Missing or unreadable files do not take the probe down.
  3. A truncated answer is trimmed to its last finished sentence: the result
     is used verbatim, so a dangling clause would reach him as-is.
  4. The command is wired everywhere a command has to be wired.

No network: the LLM client is stubbed.

Run with:
    cd C:\\Users\\User\\PycharmProjects\\your_own
    python -m pytest tests/agents/test_docs_source.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest

from infrastructure.paths import PROJECT_ROOT

from infrastructure.agents import sources
from infrastructure.agents.research import ResearchContext, Source


def make_ctx(**overrides) -> ResearchContext:
    base = dict(
        account_id="default", lang="ru", api_key="test-key", model="test/model",
        web_engine="parallel", now_str="2026-08-29 12:00", db=None, extras={},
    )
    base.update(overrides)
    return ResearchContext(**base)


@pytest.fixture
def fake_llm(monkeypatch):
    """Stub the client and record what the probe sent it."""
    sent: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            sent["client"] = kwargs

        async def complete(self, messages, max_tokens, temperature, return_meta):
            sent["messages"] = messages
            sent["max_tokens"] = max_tokens
            return sent.get("reply", "Ответ по документации."), sent.get("finish", "end_turn")

    import infrastructure.llm.client as client_mod
    monkeypatch.setattr(client_mod, "LLMClient", FakeClient)
    return sent


# ── Corpus assembly ───────────────────────────────────────────────────────────

class TestCorpus:
    def test_every_declared_file_exists(self):
        """A renamed doc must fail here, not silently shrink his knowledge."""
        for rel_path in sources.DOC_FILES:
            assert (PROJECT_ROOT / rel_path).is_file(), rel_path

    def test_files_are_named_and_dated(self):
        corpus, citations = sources._read_docs(sources.DOC_FILES)

        for rel_path in sources.DOC_FILES:
            assert rel_path in corpus
        assert len(citations) == len(sources.DOC_FILES)
        for citation in citations:
            assert "обновлён" in citation.title
            assert citation.ref in sources.DOC_FILES

    def test_a_missing_file_is_skipped(self):
        corpus, citations = sources._read_docs(("README.md", "docs/nope.md"))

        assert "README.md" in corpus
        assert len(citations) == 1

    def test_all_files_missing_gives_nothing(self):
        corpus, citations = sources._read_docs(("nope.md",))
        assert corpus == ""
        assert citations == []

    def test_the_corpus_is_capped(self, monkeypatch):
        monkeypatch.setattr(sources, "DOCS_MAX_CHARS", 500)
        corpus, _ = sources._read_docs(sources.DOC_FILES)
        # The cap applies to document bodies; headers add a little on top.
        assert len(corpus) < 1500


# ── The probe ─────────────────────────────────────────────────────────────────

class TestProbeDocs:
    @pytest.mark.asyncio
    async def test_it_returns_prose_ready_to_use(self, fake_llm):
        result = await sources.probe_docs("как работает ротация", make_ctx())

        assert result.is_brief is True          # no second call to restate it
        assert result.hits[0]["text"] == "Ответ по документации."
        assert result.hits[0]["meta"]["kind"] == "doc"
        assert [c.ref for c in result.citations] == list(sources.DOC_FILES)

    @pytest.mark.asyncio
    async def test_the_question_and_the_docs_both_reach_the_model(self, fake_llm):
        await sources.probe_docs("через сколько часов ротация", make_ctx())

        user = fake_llm["messages"][1]["content"]
        assert "через сколько часов ротация" in user
        assert "README.md" in user
        assert fake_llm["messages"][0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_the_budget_leaves_room_for_reasoning(self, fake_llm):
        """Measured: at 2000 the answer came back after 260 characters."""
        await sources.probe_docs("q", make_ctx())
        assert fake_llm["max_tokens"] >= 8000

    @pytest.mark.asyncio
    async def test_an_empty_answer_is_no_result(self, fake_llm):
        fake_llm["reply"] = ""
        result = await sources.probe_docs("q", make_ctx())
        assert result.hits == []

    @pytest.mark.asyncio
    async def test_a_truncated_answer_is_trimmed(self, fake_llm):
        fake_llm["reply"] = "Ротация идёт раз в 48 часов. Дальше обрыв на полус"
        fake_llm["finish"] = "length"

        result = await sources.probe_docs("q", make_ctx())

        assert result.hits[0]["text"] == "Ротация идёт раз в 48 часов."

    @pytest.mark.asyncio
    async def test_a_truncated_answer_with_no_sentence_is_kept_whole(self, fake_llm):
        """Nothing to trim back to, so the fragment stands rather than vanishing."""
        fake_llm["reply"] = "оборвалось сразу же"
        fake_llm["finish"] = "length"

        result = await sources.probe_docs("q", make_ctx())

        assert result.hits[0]["text"] == "оборвалось сразу же"

    @pytest.mark.asyncio
    async def test_no_docs_means_no_call(self, fake_llm):
        result = await sources.probe_docs("q", make_ctx(extras={"files": ["nope.md"]}))

        assert result.hits == []
        assert "messages" not in fake_llm   # the model was never asked


# ── Wiring ────────────────────────────────────────────────────────────────────

class TestWiring:
    def test_the_source_is_registered(self):
        assert Source.DOCS in sources.PROBES
        assert sources.PROBES[Source.DOCS] is sources.probe_docs

    def test_the_command_maps_to_the_source(self):
        from infrastructure.autonomy.reflection_engine import _SEARCH_CMDS, _SEARCH_SOURCES

        assert _SEARCH_SOURCES["SEARCH_DOCS"] == Source.DOCS
        assert "SEARCH_DOCS" in _SEARCH_CMDS

    def test_the_command_parses(self):
        from infrastructure.autonomy.reflection_engine import _CMD_RE

        hits = [(m.group("cmd").upper(), m.group("arg")) for m in
                _CMD_RE.finditer("[SEARCH_DOCS: как работает ротация заметок]")]
        assert hits == [("SEARCH_DOCS", "как работает ротация заметок")]

    def test_the_sanitiser_knows_it(self):
        from infrastructure.autonomy import workbench as wb

        assert "SEARCH_DOCS" in wb.LEAKABLE_COMMANDS
        assert wb._sanitize_note("[SEARCH_DOCS: оборвалось на полу") == ""

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_prompts_exist(self, lang):
        from infrastructure.llm.prompt_loader import load_prompt

        path = "infrastructure/agents/prompts/research_agent.md"
        assert load_prompt(path, lang=lang, section="docs_system").strip()
        user = load_prompt(path, lang=lang, section="docs_user")
        assert "{task}" in user and "{docs}" in user

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_docs_prompt_warns_about_drift(self, lang):
        """He must not read documentation as ground truth about the code."""
        from infrastructure.llm.prompt_loader import load_prompt

        text = load_prompt(
            "infrastructure/agents/prompts/research_agent.md",
            lang=lang, section="docs_system",
        ).lower()
        assert ("отстава" in text) or ("drift" in text)

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_judge_accepts_not_covered_as_an_answer(self, lang):
        """Otherwise a question the docs cannot answer costs three full passes."""
        from infrastructure.llm.prompt_loader import load_prompt

        text = load_prompt(
            "infrastructure/agents/prompts/research_agent.md",
            lang=lang, section="judge_system",
        )
        assert "ENOUGH" in text
        assert ("нет" in text.lower()) or ("not there" in text.lower())

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_reflection_advertises_the_command(self, lang):
        from infrastructure.llm.prompt_loader import get_prompt

        prompt = get_prompt(
            "infrastructure/autonomy/prompts/reflection_awakening.md",
            lang=lang,
            ai_name="Victor", identity="", workbench="",
            open_threads="", recent_dialogue="", current_time="2026-08-29 12:00",
            hours_since_last="3.0 h", pending_tasks_block="", vitals="",
            cooldown_h=4, interval_h=12, timezone_label="Asia/Yerevan",
        )
        assert "SEARCH_DOCS" in prompt
