from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Iterable, Sequence

from sqlalchemy import Text, cast, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database.models.message import Message
from infrastructure.database.repositories.message_repo import MessageRepository
from infrastructure.memory.embedder import embed_one
from infrastructure.memory.focus_point import (
    FocusPointPipeline,
    Language,
    detect_language,
    extract_focus_fast,
)

KNN_LIMIT = 200
KW_BOOST_PER = 0.10
KW_BOOST_MAX = 0.25
EXACT_BOOST = 0.15
SUBSET_BOOST = 0.10


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

        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        delta = now - created_at
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


async def retrieve_relevant_pairs(
    session: AsyncSession,
    account_id: str,
    query_text: str,
    top_n: int,
    exclude_pair_ids: Iterable[str] | None = None,
    min_age_days: int = 0,
) -> list[RetrievedPair]:
    exclude_set = {str(pair_id) for pair_id in (exclude_pair_ids or [])}
    repo = MessageRepository(session)

    lang = detect_language(query_text)
    query_tokens = set(FocusPointPipeline(language=lang, expand_synonyms=True).extract(query_text))
    fast_tokens = set(extract_focus_fast(query_text))
    norm_query = _normalise(query_text)
    query_vec = embed_one(query_text)

    age_clause = ""
    age_filter = None
    if min_age_days > 0:
        from datetime import timedelta
        age_clause = "AND created_at < :age_cutoff"
        age_filter = datetime.now(timezone.utc) - timedelta(days=min_age_days)

    candidate_rows: Sequence[Message]
    sim_map: dict = {}

    if query_vec is not None:
        vec_str = "[" + ",".join(f"{value:.8f}" for value in query_vec) + "]"
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
        knn_params: dict = {"vec": vec_str, "acct": account_id, "lim": KNN_LIMIT}
        if age_filter is not None:
            knn_params["age_cutoff"] = age_filter
        knn_rows = (
            await session.execute(knn_sql, knn_params)
        ).all()
        sim_map = {row.message_id: float(row.cosine_sim) for row in knn_rows}
        candidate_ids = list(sim_map.keys())
        if candidate_ids:
            candidate_rows = (
                await session.execute(
                    select(Message)
                    .where(Message.message_id.in_(candidate_ids))
                    .where(Message.message_kind == "chunk")
                )
            ).scalars().all()
        else:
            candidate_rows = []
    else:
        kw_list = list(query_tokens) or list(fast_tokens)
        if not kw_list:
            return []
        kw_array = cast(kw_list, ARRAY(Text))
        stmt = (
            select(Message)
            .where(Message.account_id == account_id)
            .where(Message.message_kind == "chunk")
            .where(Message.focus_point.op("&&")(kw_array))
        )
        if age_filter is not None:
            stmt = stmt.where(Message.created_at < age_filter)
        candidate_rows = (
            await session.execute(stmt.limit(KNN_LIMIT))
        ).scalars().all()

    scored: list[tuple[float, float, float, float, Message]] = []
    for msg in candidate_rows:
        if str(msg.pair_id) in exclude_set:
            continue
        cosine = sim_map.get(msg.message_id, 0.0)
        sent_tokens = set(msg.focus_point or [])
        kw_boost = _keyword_boost(fast_tokens, sent_tokens)
        exact_boost = _exact_boost(norm_query, _normalise(msg.text), fast_tokens, sent_tokens)
        total = min(1.0, cosine + kw_boost + exact_boost)
        scored.append((total, cosine, kw_boost, exact_boost, msg))

    scored.sort(key=lambda item: item[0], reverse=True)

    best_per_pair: dict[str, tuple[float, float, float, float, Message]] = {}
    for total, cosine, kw_boost, exact_boost, msg in scored:
        pair_key = str(msg.pair_id)
        existing = best_per_pair.get(pair_key)
        if existing is None or total > existing[0]:
            best_per_pair[pair_key] = (total, cosine, kw_boost, exact_boost, msg)

    top_pairs = sorted(best_per_pair.values(), key=lambda item: item[0], reverse=True)[:top_n]
    if not top_pairs:
        return []

    render_rows = await repo.get_pairs_render_data(
        account_id,
        [entry[4].pair_id for entry in top_pairs],
    )
    render_map = {str(item["pair_id"]): item for item in render_rows}

    results: list[RetrievedPair] = []
    for total, cosine, kw_boost, exact_boost, best_msg in top_pairs:
        render = render_map.get(str(best_msg.pair_id))
        if not render:
            continue
        results.append(
            RetrievedPair(
                pair_id=str(best_msg.pair_id),
                score=round(total, 4),
                cosine=round(cosine, 4),
                kw_boost=round(kw_boost, 4),
                exact_boost=round(exact_boost, 4),
                best_sentence=best_msg.text,
                best_role=best_msg.role,
                focus_matched=sorted(set(best_msg.focus_point or []) & fast_tokens),
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
