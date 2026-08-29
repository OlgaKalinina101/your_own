"""How a question turns into moments from their history.

Run:
    python -m pytest tests/memory/test_retrieval.py -v

``retrieve_relevant_pairs`` was 137 lines and had no tests at all. Splitting it
made the parts testable without a database, which is the point of having split
it: the ranking rules are ordinary functions over rows now.

The reason this needed doing is in ``_rank_by_keywords``. That branch runs when
there is no embedding — the model failed to load, which is exactly what happens
on a fresh machine — and it returned nothing at all, ever, for two independent
reasons. Measured on the live corpus: 92 candidates for one question, every one
of them scored 0.0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

import infrastructure.memory.retrieval as R

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


@dataclass
class _Chunk:
    """What the ranking functions actually touch on a row."""

    text: str
    focus_point: list[str] = field(default_factory=list)
    pair_id: str = "p1"
    message_id: int = 1
    role: str = "user"
    created_at: datetime = NOW


def _query(text="фотографии с прогулки", *, tokens=None, fast=None, vector=None):
    return R._Query(
        text=text,
        lang="ru",
        tokens=set(tokens if tokens is not None else ["фотография", "прогулка"]),
        fast_tokens=set(fast if fast is not None else ["фотографии", "прогулки"]),
        normalised=R._normalise(text),
        vector=vector,
    )


class TestRankingBySimilarity:
    def test_a_close_chunk_is_kept_and_a_distant_one_is_not(self):
        near = _Chunk("про фотографии", message_id=1, pair_id="a")
        far = _Chunk("про погоду", message_id=2, pair_id="b")

        scored = R._rank_by_similarity(
            [near, far], {1: 0.80, 2: 0.10}, _query(), exclude=set()
        )

        assert [s.msg.pair_id for s in scored] == ["a"]

    def test_matching_words_lift_the_score_above_similarity_alone(self):
        chunk = _Chunk("фотографии с прогулки", focus_point=["фотографии", "прогулки"])

        scored = R._rank_by_similarity([chunk], {1: 0.60}, _query(), exclude=set())

        assert scored[0].cosine == pytest.approx(0.60)
        assert scored[0].kw_boost > 0
        assert scored[0].total > scored[0].cosine

    def test_a_pair_he_is_already_looking_at_is_left_out(self):
        chunk = _Chunk("про фотографии", pair_id="already-here")

        scored = R._rank_by_similarity(
            [chunk], {1: 0.90}, _query(), exclude={"already-here"}
        )

        assert scored == []

    def test_a_weak_total_is_dropped_even_with_an_acceptable_cosine(self):
        # Above MIN_COSINE_SIM, below MIN_TOTAL_SCORE, no words in common.
        chunk = _Chunk("что-то другое", focus_point=["другое"])

        assert R._rank_by_similarity([chunk], {1: 0.37}, _query(), set()) == []


class TestRankingWithoutAVector:
    """The branch that used to return nothing at all."""

    def test_a_chunk_that_shares_words_with_the_question_comes_back(self):
        chunk = _Chunk("гуляли и фотографировали", focus_point=["фотография", "прогулка"])

        assert len(R._rank_by_keywords([chunk], _query(), exclude=set())) == 1

    def test_a_chunk_with_nothing_in_common_is_not_invented(self):
        chunk = _Chunk("совсем про другое", focus_point=["погода"])

        assert R._rank_by_keywords([chunk], _query(), exclude=set()) == []

    def test_more_of_the_question_matched_ranks_higher(self):
        one = _Chunk("о прогулке", focus_point=["прогулка"], pair_id="one", message_id=1)
        both = _Chunk("фото с прогулки", focus_point=["прогулка", "фотография"],
                      pair_id="both", message_id=2)

        scored = R._rank_by_keywords([one, both], _query(), exclude=set())

        assert [s.msg.pair_id for s in scored] == ["both", "one"]

    def test_the_newer_memory_wins_a_tie(self):
        old = _Chunk("тогда", focus_point=["прогулка"], pair_id="old",
                     message_id=1, created_at=NOW - timedelta(days=30))
        new = _Chunk("недавно", focus_point=["прогулка"], pair_id="new",
                     message_id=2, created_at=NOW)

        scored = R._rank_by_keywords([old, new], _query(), exclude=set())

        assert [s.msg.pair_id for s in scored] == ["new", "old"]

    def test_a_question_with_no_tokens_asks_for_nothing(self):
        chunk = _Chunk("что угодно", focus_point=["прогулка"])

        assert R._rank_by_keywords([chunk], _query(tokens=[]), exclude=set()) == []

    def test_an_excluded_pair_stays_out_here_too(self):
        chunk = _Chunk("о прогулке", focus_point=["прогулка"], pair_id="seen")

        assert R._rank_by_keywords([chunk], _query(), exclude={"seen"}) == []


class TestOneEntryPerExchange:
    def test_the_best_chunk_speaks_for_its_pair(self):
        weak = R._Scored(0.5, 0.5, 0, 0, _Chunk("слабое", pair_id="a", message_id=1))
        strong = R._Scored(0.9, 0.9, 0, 0, _Chunk("сильное", pair_id="a", message_id=2))
        other = R._Scored(0.7, 0.7, 0, 0, _Chunk("другое", pair_id="b", message_id=3))

        best = R._best_per_pair([weak, strong, other])

        assert [(b.msg.pair_id, b.msg.text) for b in best] == [
            ("a", "сильное"),
            ("b", "другое"),
        ]

    def test_pairs_come_back_strongest_first(self):
        items = [
            R._Scored(0.4, 0.4, 0, 0, _Chunk("c", pair_id="c", message_id=3)),
            R._Scored(0.9, 0.9, 0, 0, _Chunk("a", pair_id="a", message_id=1)),
            R._Scored(0.6, 0.6, 0, 0, _Chunk("b", pair_id="b", message_id=2)),
        ]

        assert [b.msg.pair_id for b in R._best_per_pair(items)] == ["a", "b", "c"]


class TestWhichSearchIsUsed:
    @pytest.mark.asyncio
    async def test_an_embedding_means_the_vector_search(self, monkeypatch):
        called = []

        async def _vector(*_a, **_kw):
            called.append("vector")
            return [], {}

        async def _keywords(*_a, **_kw):
            called.append("keywords")
            return []

        monkeypatch.setattr(R, "embed_one", lambda _t: [0.1] * 384)
        monkeypatch.setattr(R, "_candidates_by_vector", _vector)
        monkeypatch.setattr(R, "_candidates_by_keywords", _keywords)

        await R.retrieve_relevant_pairs(None, "default", "вопрос", top_n=3)

        assert called == ["vector"]

    @pytest.mark.asyncio
    async def test_no_embedding_falls_back_instead_of_giving_up(self, monkeypatch, caplog):
        called = []

        async def _vector(*_a, **_kw):
            called.append("vector")
            return [], {}

        async def _keywords(*_a, **_kw):
            called.append("keywords")
            return []

        monkeypatch.setattr(R, "embed_one", lambda _t: None)
        monkeypatch.setattr(R, "_candidates_by_vector", _vector)
        monkeypatch.setattr(R, "_candidates_by_keywords", _keywords)

        with caplog.at_level("WARNING"):
            await R.retrieve_relevant_pairs(None, "default", "вопрос", top_n=3)

        assert called == ["keywords"]
        assert "falling back" in caplog.text, (
            "memory ran degraded and said nothing about it"
        )


class TestBothSpellingsAreLookedFor:
    """The index holds whichever form the stored sentence used.

    ``focus_point`` is written by ``extract_focus_fast`` over each sentence, so
    a chunk that said "чувствовала" is indexed under that and one that said
    "чувствовать" under the lemma — measured on the live corpus, 41 chunks and
    53 chunks. The two extractors run on the *question* produce disjoint sets,
    so consulting either alone reads half the index and calls the rest absent.
    """

    def test_a_chunk_indexed_under_the_lemma_is_found(self):
        chunk = _Chunk("о прогулке", focus_point=["прогулка"])    # pipeline form

        assert R._rank_by_keywords([chunk], _query(), exclude=set())

    def test_a_chunk_indexed_under_the_surface_form_is_found(self):
        chunk = _Chunk("о прогулках", focus_point=["прогулки"])   # fast form

        assert R._rank_by_keywords([chunk], _query(), exclude=set())

    def test_the_boost_counts_the_lemma_form_too(self):
        # Before the fix the boost consulted only the surface forms, so a chunk
        # stored under lemmas earned nothing from words it plainly shared.
        chunk = _Chunk("фотографии с прогулки", focus_point=["фотография", "прогулка"])

        scored = R._rank_by_similarity([chunk], {1: 0.60}, _query(), exclude=set())

        assert scored[0].kw_boost > 0

    def test_the_subset_bonus_is_left_on_the_narrower_vocabulary(self):
        # Widening it there asks for every word in both forms at once, which
        # nothing satisfies: measured, it cost two of thirty pairs exactly 0.1.
        chunk = _Chunk("фотографии прогулки", focus_point=["фотографии", "прогулки"])

        scored = R._rank_by_similarity([chunk], {1: 0.60}, _query(), exclude=set())

        assert scored[0].exact_boost >= R.SUBSET_BOOST

    @pytest.mark.asyncio
    async def test_the_search_itself_asks_for_both_forms(self):
        """The escape a mutation found: nothing checked what the query asks for."""
        class _Recording:
            def __init__(self):
                self.tokens = None

            async def execute(self, stmt, *_a, **_kw):
                for value in stmt.compile().params.values():
                    if isinstance(value, list):
                        self.tokens = set(value)

                class _Empty:
                    def scalars(self):
                        return self

                    def all(self):
                        return []

                return _Empty()

        session = _Recording()
        await R._candidates_by_keywords(session, "default", _query(), None)

        assert session.tokens == {"фотография", "прогулка", "фотографии", "прогулки"}, (
            "the search read half the index and called the other half absent"
        )
