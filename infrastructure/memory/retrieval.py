from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Iterable, Sequence

from sqlalchemy import Text, cast, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.clock import now_utc
from infrastructure.database.models.message import Message
from infrastructure.database.repositories.message_repo import MessageRepository
from infrastructure.memory.embedder import embed_one
from infrastructure.memory.focus_point import (
    FocusPointPipeline,
    Language,
    detect_language,
    extract_focus_fast,
)

import logging

_logger = logging.getLogger(__name__)

KNN_LIMIT = 200
KW_BOOST_PER = 0.10
KW_BOOST_MAX = 0.25
EXACT_BOOST = 0.15
SUBSET_BOOST = 0.10
MIN_COSINE_SIM = 0.35
MIN_TOTAL_SCORE = 0.40
# Sorting key for a row with no timestamp; older than anything real.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass
class RetrievedPair:
    pair_id: str
    score: float
    cosine: float
    kw_boost: float
    exact_boost: float
    best_sentence: str
    best_role: str
    focus_matched: list[str]
    created_at: datetime | None
    user_text: str
    assistant_text: str

    def to_dict(self, language: Language = "en") -> dict:
        item_language = _pair_language(self, language)
        return {
            "pair_id": self.pair_id,
            "score": self.score,
            "cosine": self.cosine,
            "kw_boost": self.kw_boost,
            "exact_boost": self.exact_boost,
            "best_sentence": self.best_sentence,
            "best_role": self.best_role,
            "focus_matched": self.focus_matched,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "relative_time_label": humanize_timestamp(self.created_at, item_language),
            "user_text": self.user_text,
            "assistant_text": self.assistant_text,
        }


def humanize_timestamp(created_at_value: datetime | str | None, language: Language = "ru") -> str:
    if not created_at_value:
        return "long ago" if language == "en" else "давно"

    try:
        if isinstance(created_at_value, str):
            created_at = datetime.fromisoformat(created_at_value.replace("Z", "+00:00"))
        else:
            created_at = created_at_value

        # Both sides as instants. Stripping the tzinfo and comparing against a
        # naive datetime.now() measured the age against the *system* clock,
        # which is not the clock anything else in here uses.
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        delta = now_utc() - created_at
        days = max(0, delta.days)

        if language == "en":
            if days == 0:
                return "today"
            if days == 1:
                return "yesterday"
            if days < 7:
                return f"{days} day ago" if days == 1 else f"{days} days ago"
            if days < 30:
                weeks = max(1, days // 7)
                return "1 week ago" if weeks == 1 else f"{weeks} weeks ago"
            months = days // 30
            if months == 1:
                return "1 month ago"
            return "long ago"

        if days == 0:
            return "сегодня"
        if days == 1:
            return "вчера"
        if days < 7:
            if days in (2, 3, 4):
                return f"{days} дня назад"
            return f"{days} дней назад"
        if days < 30:
            weeks = max(1, days // 7)
            if weeks == 1:
                return "неделю назад"
            if weeks in (2, 3, 4):
                return f"{weeks} недели назад"
            return f"{weeks} недель назад"
        months = days // 30
        if months == 1:
            return "месяц назад"
        return "давно"
    except Exception:
        return "long ago" if language == "en" else "давно"


def _memory_copy(language: Language) -> dict[str, str]:
    if language == "ru":
        return {
            "intro": (
                "Это моменты, которые вы уже проживали вместе. "
                "Они здесь как фон — чтобы помнить важное, "
                "бережно чувствовать контекст, "
                "не переспрашивать одно и то же "
                "и замечать то, что повторяется."
            ),
            "user": "Ты",
            "assistant": "Я",
            "empty": "...",
        }
    return {
        "intro": (
            "These are moments you have already lived through together. "
            "They are here as background — to remember what matters, "
            "to feel the context gently, "
            "not to ask the same things twice, "
            "and to notice what keeps coming back."
        ),
        "user": "You",
        "assistant": "Me",
        "empty": "...",
    }


def _pair_language(item: RetrievedPair, fallback: Language) -> Language:
    sample = " ".join(part for part in [item.user_text, item.assistant_text, item.best_sentence] if part).strip()
    if not sample:
        return fallback
    return detect_language(sample)


def _normalise(text_value: str) -> str:
    return re.sub(r"[^\w\s]", " ", text_value.lower()).strip()


def _keyword_boost(query_tokens: set[str], sent_tokens: set[str]) -> float:
    matches = len(query_tokens & sent_tokens)
    return min(KW_BOOST_MAX, matches * KW_BOOST_PER)


def _exact_boost(
    norm_query: str,
    norm_sent: str,
    query_tokens: set[str],
    sent_tokens: set[str],
) -> float:
    boost = 0.0
    if norm_query == norm_sent:
        boost += EXACT_BOOST
    if query_tokens and query_tokens.issubset(sent_tokens):
        boost += SUBSET_BOOST
    return boost


@dataclass
class _Query:
    """One question, in every form the search needs it in."""

    text: str
    lang: Language
    tokens: set[str]        # pipeline lemmas, with synonyms expanded
    fast_tokens: set[str]   # surface forms, as the sentence actually wrote them
    normalised: str
    vector: list[float] | None

    @property
    def index_tokens(self) -> set[str]:
        """Every form worth looking for in ``focus_point``.

        The index is written by ``extract_focus_fast`` over each stored
        sentence, so it holds whatever form that sentence used: measured on the
        live corpus, "говорить" sits in 85 chunks and "говорили" in 13,
        "чувствовать" in 53 and "чувствовала" in 41. The two extractors run on
        the question produce *disjoint* sets — zero overlap on every question
        measured — so using either one alone looks at half the index and calls
        the other half absent.
        """
        return self.tokens | self.fast_tokens


def _read_query(query_text: str) -> _Query:
    lang = detect_language(query_text)
    return _Query(
        text=query_text,
        lang=lang,
        tokens=set(FocusPointPipeline(language=lang, expand_synonyms=True).extract(query_text)),
        fast_tokens=set(extract_focus_fast(query_text)),
        normalised=_normalise(query_text),
        vector=embed_one(query_text),
    )


@dataclass
class _Scored:
    """A chunk that survived scoring, and what its score was made of."""

    total: float
    cosine: float
    kw_boost: float
    exact_boost: float
    msg: Message


async def _candidates_by_vector(
    session: AsyncSession,
    account_id: str,
    query: _Query,
    age_filter: datetime | None,
) -> tuple[Sequence[Message], dict]:
    """Nearest chunks by embedding, with their cosine similarities."""
    vec_str = "[" + ",".join(f"{value:.8f}" for value in query.vector) + "]"
    age_clause = "AND created_at < :age_cutoff" if age_filter is not None else ""
    knn_sql = text(
        f"""
        SELECT message_id,
               1 - (embedding <=> cast(:vec AS vector)) AS cosine_sim
        FROM messages
        WHERE account_id = :acct
          AND embedding IS NOT NULL
          AND message_kind = 'chunk'
          {age_clause}
        ORDER BY embedding <=> cast(:vec AS vector)
        LIMIT :lim
        """
    )
    params: dict = {"vec": vec_str, "acct": account_id, "lim": KNN_LIMIT}
    if age_filter is not None:
        params["age_cutoff"] = age_filter

    knn_rows = (await session.execute(knn_sql, params)).all()
    sim_map = {row.message_id: float(row.cosine_sim) for row in knn_rows}
    if not sim_map:
        return [], {}

    rows = (
        await session.execute(
            select(Message)
            .where(Message.message_id.in_(list(sim_map.keys())))
            .where(Message.message_kind == "chunk")
        )
    ).scalars().all()
    return rows, sim_map


async def _candidates_by_keywords(
    session: AsyncSession,
    account_id: str,
    query: _Query,
    age_filter: datetime | None,
) -> Sequence[Message]:
    """Chunks whose stored focus points overlap the question's.

    Reached only when there is no embedding to search with — the model failed to
    load, or encoding raised. That is a real state on a fresh machine, which is
    why this path exists at all.
    """
    kw_list = list(query.index_tokens)
    if not kw_list:
        return []

    stmt = (
        select(Message)
        .where(Message.account_id == account_id)
        .where(Message.message_kind == "chunk")
        .where(Message.focus_point.op("&&")(cast(kw_list, ARRAY(Text))))
    )
    if age_filter is not None:
        stmt = stmt.where(Message.created_at < age_filter)
    return (await session.execute(stmt.limit(KNN_LIMIT))).scalars().all()


def _rank_by_similarity(
    rows: Sequence[Message],
    sim_map: dict,
    query: _Query,
    exclude: set[str],
) -> list[_Scored]:
    """Cosine carries the score; matching words and exact phrasing add to it."""
    scored: list[_Scored] = []
    for msg in rows:
        if str(msg.pair_id) in exclude:
            continue
        cosine = sim_map.get(msg.message_id, 0.0)
        if cosine < MIN_COSINE_SIM:
            continue
        sent_tokens = set(msg.focus_point or [])
        # The count-based boost looks for every form, because the index holds
        # both. The subset test below deliberately does not: it asks whether the
        # whole question is contained in this chunk, and against the union that
        # would mean every word appearing in *both* its forms at once — a
        # condition nothing satisfies. Measured: widening it there cost two of
        # thirty pairs exactly 0.1, the SUBSET_BOOST they used to earn.
        kw_boost = _keyword_boost(query.index_tokens, sent_tokens)
        exact_boost = _exact_boost(
            query.normalised, _normalise(msg.text), query.fast_tokens, sent_tokens
        )
        total = min(1.0, cosine + kw_boost + exact_boost)
        if total < MIN_TOTAL_SCORE:
            continue
        scored.append(_Scored(total, cosine, kw_boost, exact_boost, msg))
    return scored


def _rank_by_keywords(
    rows: Sequence[Message], query: _Query, exclude: set[str]
) -> list[_Scored]:
    """Rank on word overlap alone, because there is no vector to rank with.

    This branch used to return nothing at all, for two independent reasons. The
    cosine defaulted to ``0.0`` and the next line dropped everything below
    ``MIN_COSINE_SIM`` — every candidate, always. And the candidates were
    selected on one extractor's tokens and scored on the other's, which are
    disjoint: 92 candidates for one question, every one of them 0.0.

    There is no score threshold here on purpose. A first attempt used coverage —
    the share of the question present in the chunk — and measurement disowned
    it: questions carry one or two concepts, so against the union of both
    vocabularies almost every candidate lands at 0.25–0.33, and against the
    concept count almost every candidate passes. A threshold that either admits
    everything or nothing is not a threshold. What is left is honest ordering:
    more of the question matched wins, and among equals the newer memory wins.
    Selection already guarantees at least one match.
    """
    words = query.index_tokens
    if not words:
        return []

    scored: list[_Scored] = []
    for msg in rows:
        if str(msg.pair_id) in exclude:
            continue
        matched = words & set(msg.focus_point or [])
        if not matched:
            continue
        share = len(matched) / len(words)
        scored.append(_Scored(share, 0.0, share, 0.0, msg))

    # Ties are broad — with a two-word question most candidates share a value —
    # so recency decides. It is the only other thing this path knows, and the
    # alternative is whatever order the database happened to return.
    scored.sort(key=lambda item: (item.total, item.msg.created_at or _EPOCH), reverse=True)
    return scored


def _best_per_pair(scored: list[_Scored]) -> list[_Scored]:
    """One entry per exchange: the chunk that matched best speaks for its pair."""
    best: dict[str, _Scored] = {}
    for item in sorted(scored, key=lambda entry: entry.total, reverse=True):
        pair_key = str(item.msg.pair_id)
        if pair_key not in best:
            best[pair_key] = item
    return sorted(best.values(), key=lambda entry: entry.total, reverse=True)


async def retrieve_relevant_pairs(
    session: AsyncSession,
    account_id: str,
    query_text: str,
    top_n: int,
    exclude_pair_ids: Iterable[str] | None = None,
    min_age_days: int = 0,
) -> list[RetrievedPair]:
    """Moments from their history that bear on *query_text*.

    Matching happens on chunks, because a chunk is small enough to be about one
    thing. What comes back is whole exchanges: the chunk only decides *which*
    moment, and he is then shown all of it.
    """
    exclude = {str(pair_id) for pair_id in (exclude_pair_ids or [])}
    query = _read_query(query_text)

    age_filter = None
    if min_age_days > 0:
        from datetime import timedelta
        age_filter = now_utc() - timedelta(days=min_age_days)

    if query.vector is not None:
        rows, sim_map = await _candidates_by_vector(session, account_id, query, age_filter)
        scored = _rank_by_similarity(rows, sim_map, query, exclude)
    else:
        _logger.warning(
            "[retrieval] no embedding for the question — falling back to keyword "
            "overlap; results are coarser than usual"
        )
        rows = await _candidates_by_keywords(session, account_id, query, age_filter)
        scored = _rank_by_keywords(rows, query, exclude)

    top_pairs = _best_per_pair(scored)[:top_n]
    if not top_pairs:
        _logger.info(
            "[retrieval] no pairs passed threshold (cosine>=%.2f total>=%.2f) among %d candidates",
            MIN_COSINE_SIM, MIN_TOTAL_SCORE, len(rows),
        )
        return []

    return await _render(session, account_id, top_pairs, query)


async def _render(
    session: AsyncSession,
    account_id: str,
    top_pairs: list[_Scored],
    query: _Query,
) -> list[RetrievedPair]:
    """Turn the winning chunks back into the exchanges they came from."""
    repo = MessageRepository(session)
    render_rows = await repo.get_pairs_render_data(
        account_id, [entry.msg.pair_id for entry in top_pairs]
    )
    render_map = {str(item["pair_id"]): item for item in render_rows}

    results: list[RetrievedPair] = []
    for entry in top_pairs:
        render = render_map.get(str(entry.msg.pair_id))
        if not render:
            continue
        _logger.info(
            "[retrieval] pair score=%.3f cosine=%.3f kw=%.3f exact=%.3f text=%s",
            entry.total, entry.cosine, entry.kw_boost, entry.exact_boost,
            (entry.msg.text or "")[:80],
        )
        results.append(
            RetrievedPair(
                pair_id=str(entry.msg.pair_id),
                score=round(entry.total, 4),
                cosine=round(entry.cosine, 4),
                kw_boost=round(entry.kw_boost, 4),
                exact_boost=round(entry.exact_boost, 4),
                best_sentence=entry.msg.text,
                best_role=entry.msg.role,
                focus_matched=sorted(set(entry.msg.focus_point or []) & query.index_tokens),
                created_at=render["created_at"],
                user_text=render["user_text"],
                assistant_text=render["assistant_text"],
            )
        )
    return results


def build_memory_block(recalled_pairs: Sequence[RetrievedPair], language: Language = "en") -> str | None:
    if not recalled_pairs:
        return None

    copy = _memory_copy(language)
    lines = [copy["intro"], ""]

    for idx, item in enumerate(recalled_pairs, start=1):
        relative_time = humanize_timestamp(item.created_at, language)
        lines.extend([
            f"[{relative_time}]",
            f"{copy['user']}: {item.user_text or copy['empty']}",
            f"{copy['assistant']}: {item.assistant_text or copy['empty']}",
            "",
        ])
    return "\n".join(lines).strip()
