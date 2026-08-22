"""Tests for the database backends of the research agent.

Covers what each probe turns storage rows into:
  1. Dialogue — semantic pairs, and the date form that bypasses the search.
  2. Facts — Chroma key_info rows with category and id.
  3. Notes — Chroma archive filtered by distance, plus the live workbench.
  4. Registry — every Source has a probe.

Chroma, the workbench and Postgres are all stubbed; nothing here touches a
real store.

Run with:
    cd C:\\Users\\User\\PycharmProjects\\your_own
    python -m pytest tests/agents/test_sources.py -v
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest

from infrastructure.agents import sources
from infrastructure.agents.research import ResearchContext, Source


def make_ctx(**overrides) -> ResearchContext:
    base = dict(
        account_id="default",
        lang="ru",
        api_key="test-key",
        model="test/model",
        web_engine="parallel",
        now_str="2026-08-22 12:00",
        db=object(),
        extras={},
    )
    base.update(overrides)
    return ResearchContext(**base)


# ── Dialogue ──────────────────────────────────────────────────────────────────

@dataclass
class FakePair:
    pair_id: str
    score: float
    created_at: datetime | None
    user_text: str
    assistant_text: str


class TestDialogue:
    @pytest.mark.asyncio
    async def test_pairs_become_hits_and_citations(self, monkeypatch):
        pair = FakePair(
            pair_id="p-1",
            score=0.82,
            created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            user_text="я переехала в Ереван",
            assistant_text="расскажи, как там",
        )

        async def fake_retrieve(**kwargs):
            assert kwargs["query_text"] == "переезд"
            assert kwargs["top_n"] == 6
            assert kwargs["min_age_days"] == 3
            return [pair]

        import infrastructure.memory.retrieval as retrieval
        monkeypatch.setattr(retrieval, "retrieve_relevant_pairs", fake_retrieve)

        ctx = make_ctx(extras={"top_n": 6, "min_age_days": 3})
        result = await sources.probe_dialogue("переезд", ctx)

        assert len(result.hits) == 1
        meta = result.hits[0]["meta"]
        assert meta["kind"] == "dialogue"
        assert meta["pair_id"] == "p-1"
        assert meta["user"] == "я переехала в Ереван"
        assert meta["assistant"] == "расскажи, как там"
        assert "Они:" in result.hits[0]["text"]
        assert "Я:" in result.hits[0]["text"]
        assert result.is_brief is False
        assert [c.ref for c in result.citations] == ["p-1"]

    @pytest.mark.asyncio
    async def test_english_speaker_labels(self, monkeypatch):
        async def fake_retrieve(**kwargs):
            return [FakePair("p-1", 0.9, None, "hi", "hello")]

        import infrastructure.memory.retrieval as retrieval
        monkeypatch.setattr(retrieval, "retrieve_relevant_pairs", fake_retrieve)

        result = await sources.probe_dialogue("greeting", make_ctx(lang="en"))

        assert "They:" in result.hits[0]["text"]
        assert "Me:" in result.hits[0]["text"]

    @pytest.mark.asyncio
    async def test_no_session_returns_empty(self):
        result = await sources.probe_dialogue("anything", make_ctx(db=None))
        assert result.hits == []

    @pytest.mark.asyncio
    async def test_date_argument_uses_the_page_lookup(self, monkeypatch):
        captured = {}

        class FakeRepo:
            def __init__(self, session):
                captured["session"] = session

            async def get_canonical_pairs_page(self, account_id, limit_pairs, before):
                captured["before"] = before
                captured["limit"] = limit_pairs
                return ([{
                    "pair_id": "p-9",
                    "created_at": datetime(2026, 3, 17, tzinfo=timezone.utc),
                    "user_text": "тот день",
                    "assistant_text": "помню",
                }], None, False)

        import infrastructure.database.repositories.message_repo as repo_mod
        monkeypatch.setattr(repo_mod, "MessageRepository", FakeRepo)

        result = await sources.probe_dialogue("2026-03-17", make_ctx())

        assert captured["before"].date().isoformat() == "2026-03-17"
        assert len(result.hits) == 1
        assert result.hits[0]["meta"]["pair_id"] == "p-9"
        assert "2026-03-17" in result.hits[0]["text"]

    @pytest.mark.asyncio
    async def test_date_range_uses_the_end_of_the_range(self, monkeypatch):
        captured = {}

        class FakeRepo:
            def __init__(self, session):
                pass

            async def get_canonical_pairs_page(self, account_id, limit_pairs, before):
                captured["before"] = before
                return ([], None, False)

        import infrastructure.database.repositories.message_repo as repo_mod
        monkeypatch.setattr(repo_mod, "MessageRepository", FakeRepo)

        await sources.probe_dialogue("2026-03-01..2026-03-17", make_ctx())

        assert captured["before"].date().isoformat() == "2026-03-17"

    @pytest.mark.asyncio
    async def test_malformed_date_returns_empty(self, monkeypatch):
        result = await sources.probe_dialogue("2026-13-99", make_ctx())
        assert result.hits == []


# ── Facts ─────────────────────────────────────────────────────────────────────

class FakePipeline:
    def __init__(self, facts):
        self.facts = facts
        self.calls: list[dict] = []

    def query_similar_multi(self, *, account_id, message, top_k, days_cutoff):
        self.calls.append({
            "account_id": account_id, "message": message,
            "top_k": top_k, "days_cutoff": days_cutoff,
        })
        return self.facts


class TestFacts:
    @pytest.mark.asyncio
    async def test_facts_become_hits(self, monkeypatch):
        pipeline = FakePipeline([
            {"id": "f-1", "text": "любит море",
             "metadata": {"category": "предпочтения", "impressive": 3}, "score": 0.21},
        ])
        import infrastructure.memory.chroma_pipeline as cp
        monkeypatch.setattr(cp, "get_chroma_pipeline", lambda: pipeline)

        result = await sources.probe_facts("что она любит", make_ctx())

        assert result.hits[0]["text"] == "[предпочтения] любит море"
        assert result.hits[0]["meta"]["id"] == "f-1"
        assert result.hits[0]["meta"]["impressive"] == 3
        assert result.citations[0].ref == "f-1"
        assert pipeline.calls[0]["days_cutoff"] == 2  # the pipeline's own default

    @pytest.mark.asyncio
    async def test_extras_override_the_defaults(self, monkeypatch):
        pipeline = FakePipeline([])
        import infrastructure.memory.chroma_pipeline as cp
        monkeypatch.setattr(cp, "get_chroma_pipeline", lambda: pipeline)

        await sources.probe_facts("q", make_ctx(extras={"top_k": 9, "days_cutoff": 0}))

        assert pipeline.calls[0]["top_k"] == 9
        assert pipeline.calls[0]["days_cutoff"] == 0

    @pytest.mark.asyncio
    async def test_chroma_failure_is_contained(self, monkeypatch):
        class Boom:
            def query_similar_multi(self, **kwargs):
                raise RuntimeError("chroma down")

        import infrastructure.memory.chroma_pipeline as cp
        monkeypatch.setattr(cp, "get_chroma_pipeline", lambda: Boom())

        result = await sources.probe_facts("q", make_ctx())

        assert result.hits == []


# ── Notes ─────────────────────────────────────────────────────────────────────

class TestNotes:
    @pytest.mark.asyncio
    async def test_archive_and_workbench_are_merged(self, monkeypatch):
        class FakeCollection:
            def query(self, **kwargs):
                return {
                    "ids": [["a-1", "a-2"]],
                    "documents": [["близкая заметка", "далёкая заметка"]],
                    "metadatas": [[{"created_at": "2026-08-01"}, {"created_at": "2026-07-01"}]],
                    "distances": [[0.2, 0.9]],
                }

        import infrastructure.memory.chroma_pipeline as cp
        import infrastructure.memory.embedder as embedder
        import infrastructure.autonomy.workbench as wb
        monkeypatch.setattr(cp, "_get_archive_collection", lambda: FakeCollection())
        monkeypatch.setattr(embedder, "embed_one", lambda text: [0.1, 0.2])
        monkeypatch.setattr(wb, "search", lambda account_id, query: "### 2026-08-20\nсвежая мысль")

        result = await sources.probe_notes("мысль", make_ctx())

        texts = [h["text"] for h in result.hits]
        assert any("близкая заметка" in t for t in texts)
        # 0.9 is past CHROMA_ARCHIVE_MAX_DISTANCE — dropped
        assert not any("далёкая заметка" in t for t in texts)
        assert any("свежая мысль" in t for t in texts)
        origins = {h["meta"]["origin"] for h in result.hits}
        assert origins == {"archive", "workbench"}

    @pytest.mark.asyncio
    async def test_empty_workbench_is_not_a_hit(self, monkeypatch):
        import infrastructure.memory.chroma_pipeline as cp
        import infrastructure.autonomy.workbench as wb
        monkeypatch.setattr(cp, "_get_archive_collection", lambda: None)
        monkeypatch.setattr(wb, "search", lambda account_id, query: "(workbench is empty)")

        result = await sources.probe_notes("q", make_ctx())

        assert result.hits == []

    @pytest.mark.asyncio
    async def test_no_match_message_is_not_a_hit(self, monkeypatch):
        import infrastructure.memory.chroma_pipeline as cp
        import infrastructure.autonomy.workbench as wb
        monkeypatch.setattr(cp, "_get_archive_collection", lambda: None)
        monkeypatch.setattr(wb, "search", lambda account_id, query: "No notes matching 'q'.")

        result = await sources.probe_notes("q", make_ctx())

        assert result.hits == []


# ── Registry ──────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_every_source_has_a_probe(self):
        declared = {
            value for name, value in vars(Source).items()
            if not name.startswith("_") and isinstance(value, str)
        }
        assert declared == set(sources.PROBES)
