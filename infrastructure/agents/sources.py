"""Search backends for :class:`~infrastructure.agents.research.ResearchAgent`.

Every backend is a plain async function ``(query, ctx) -> ProbeResult``
registered in :data:`PROBES`. Adding a source is one function plus one
member of :class:`~infrastructure.agents.research.Source` — no new classes,
no inheritance.

Chroma and the workbench are synchronous libraries; their probes hop to a
thread so a search never blocks the event loop mid-reply.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from infrastructure.agents.research import Citation, ProbeResult, ResearchContext, Source

logger = logging.getLogger("agents.sources")

# A dialogue argument that is a plain date (or date range) is a lookup, not a
# search — there is nothing in it to reformulate.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

CHROMA_ARCHIVE_MAX_DISTANCE = 0.65


async def _to_thread(fn: Callable[[], Any]) -> Any:
    """Run a blocking library call off the event loop."""
    return await asyncio.get_running_loop().run_in_executor(None, fn)


# ── Web ───────────────────────────────────────────────────────────────────────

def _web_tools(engine: str) -> list[dict]:
    """OpenRouter server tools: agentic search plus full-page fetch.

    ``engine`` is set explicitly rather than left to the provider default —
    Google models otherwise fall back to native search, whose result shape
    and domain-filter behaviour differ from the rest.
    """
    return [
        {
            "type": "openrouter:web_search",
            "parameters": {
                "engine": engine,
                "max_results": 5,
                "max_total_results": 20,
            },
        },
        {"type": "openrouter:web_fetch"},
    ]


async def probe_web(query: str, ctx: ResearchContext) -> ProbeResult:
    """One agentic web pass.

    OpenRouter runs the search loop server-side: the model picks its own
    queries, decides how many searches to run, and may open a page in full.
    So a single call here already covers "search, judge, search again" —
    the agent's own retry loop only fires when this comes back empty or
    off-topic.
    """
    from infrastructure.llm.client import LLMClient

    client = LLMClient(api_key=ctx.api_key, model=ctx.model, temperature=0.3)
    messages = [
        {"role": "system", "content": ctx.prompt("web_system")},
        {"role": "user", "content": ctx.prompt("web_user", task=query, now_str=ctx.now_str)},
    ]

    text, raw_citations = await client.complete_with_tools(
        messages=messages,
        tools=_web_tools(ctx.web_engine),
        max_tokens=1200,
    )

    if not text:
        logger.info("[sources.web] empty result query=%s", query[:120])
        return ProbeResult(hits=[], citations=[])

    citations = [Citation(title=c["title"], url=c["url"]) for c in raw_citations]
    return ProbeResult(
        hits=[{"text": text, "meta": {"kind": "web", "query": query}}],
        citations=citations,
        is_brief=True,
    )


# ── Dialogue (PostgreSQL + pgvector) ──────────────────────────────────────────

async def probe_dialogue(query: str, ctx: ResearchContext) -> ProbeResult:
    """Conversation pairs from Postgres — semantic by default, by date on request.

    A ``YYYY-MM-DD`` (or ``YYYY-MM-DD..YYYY-MM-DD``) argument is answered by a
    deterministic page lookup instead of a K-NN search. Callers pass
    ``max_attempts=1`` for that form, since a date cannot be reformulated.
    """
    if ctx.db is None:
        logger.warning("[sources.dialogue] no db session — search skipped")
        return ProbeResult()

    query = query.strip()
    if _DATE_RE.match(query):
        return await _dialogue_by_date(query, ctx)

    from infrastructure.memory.retrieval import humanize_timestamp, retrieve_relevant_pairs

    pairs = await retrieve_relevant_pairs(
        session=ctx.db,
        account_id=ctx.account_id,
        query_text=query,
        top_n=int(ctx.extras.get("top_n", 6)),
        exclude_pair_ids=ctx.extras.get("exclude_pair_ids") or [],
        min_age_days=int(ctx.extras.get("min_age_days", 0)),
    )
    if not pairs:
        logger.info("[sources.dialogue] no pairs query=%s", query[:120])
        return ProbeResult()

    speakers = _speakers(ctx.lang)
    hits: list[dict] = []
    citations: list[Citation] = []
    for pair in pairs:
        time_label = humanize_timestamp(pair.created_at, ctx.lang)
        lines = [f"[{time_label}]"]
        if pair.user_text:
            lines.append(f"  {speakers['user']}: {pair.user_text}")
        if pair.assistant_text:
            lines.append(f"  {speakers['assistant']}: {pair.assistant_text}")
        hits.append({
            "text": "\n".join(lines),
            "meta": {
                "kind": "dialogue",
                "pair_id": pair.pair_id,
                "time": time_label,
                "score": pair.score,
                "user": pair.user_text or "",
                "assistant": pair.assistant_text or "",
            },
        })
        citations.append(Citation(title=time_label, ref=pair.pair_id))

    return ProbeResult(hits=hits, citations=citations)


async def _dialogue_by_date(arg: str, ctx: ResearchContext) -> ProbeResult:
    from infrastructure.database.repositories.message_repo import MessageRepository

    end = arg.split("..")[-1].strip()
    try:
        before = datetime.strptime(end, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
    except ValueError:
        logger.warning("[sources.dialogue] bad date argument: %r", arg)
        return ProbeResult()

    repo = MessageRepository(ctx.db)
    pairs, _, _ = await repo.get_canonical_pairs_page(
        ctx.account_id, limit_pairs=int(ctx.extras.get("top_n", 10)), before=before
    )
    if not pairs:
        return ProbeResult()

    speakers = _speakers(ctx.lang)
    hits: list[dict] = []
    citations: list[Citation] = []
    for pair in pairs:
        created = pair.get("created_at")
        time_label = created.strftime("%Y-%m-%d") if created else arg
        hits.append({
            "text": (
                f"[{time_label}]\n"
                f"  {speakers['user']}: {pair.get('user_text', '')}\n"
                f"  {speakers['assistant']}: {pair.get('assistant_text', '')}"
            ),
            "meta": {
                "kind": "dialogue",
                "pair_id": str(pair.get("pair_id", "")),
                "time": time_label,
                "user": pair.get("user_text", ""),
                "assistant": pair.get("assistant_text", ""),
            },
        })
        citations.append(Citation(title=time_label, ref=str(pair.get("pair_id", ""))))

    return ProbeResult(hits=hits, citations=citations)


def _speakers(lang: str) -> dict[str, str]:
    return {"user": "Они", "assistant": "Я"} if lang == "ru" else {"user": "They", "assistant": "Me"}


# ── Facts (Chroma key_info) ───────────────────────────────────────────────────

async def probe_facts(query: str, ctx: ResearchContext) -> ProbeResult:
    """Long-term facts from Chroma, with the pipeline's own boosts applied."""
    from infrastructure.memory.chroma_pipeline import get_chroma_pipeline

    pipeline = get_chroma_pipeline()
    top_k = int(ctx.extras.get("top_k", 5))
    days_cutoff = int(ctx.extras.get("days_cutoff", 2))

    try:
        facts = await _to_thread(
            lambda: pipeline.query_similar_multi(
                account_id=ctx.account_id,
                message=query,
                top_k=top_k,
                days_cutoff=days_cutoff,
            )
        )
    except Exception as exc:
        logger.warning("[sources.facts] chroma query failed: %s", exc)
        return ProbeResult()

    if not facts:
        logger.info("[sources.facts] nothing found query=%s", query[:120])
        return ProbeResult()

    hits: list[dict] = []
    citations: list[Citation] = []
    for fact in facts:
        meta = fact.get("metadata") or {}
        category = meta.get("category", "?")
        text = (fact.get("text") or "").strip()
        hits.append({
            "text": f"[{category}] {text}",
            "meta": {
                "kind": "fact",
                "id": fact.get("id", ""),
                "category": category,
                "impressive": meta.get("impressive", 0),
                "created_at": meta.get("created_at", ""),
                "score": fact.get("score"),
            },
        })
        citations.append(Citation(title=f"{category}: {text[:60]}", ref=str(fact.get("id", ""))))

    return ProbeResult(hits=hits, citations=citations)


# ── Notes (workbench + Chroma archive) ────────────────────────────────────────

async def probe_notes(query: str, ctx: ResearchContext) -> ProbeResult:
    """Rotated notes from the Chroma archive plus the live workbench."""
    hits: list[dict] = []
    citations: list[Citation] = []

    for doc, created_at in await _archive_notes(query, ctx):
        hits.append({
            "text": f"[archive {created_at}] {doc}",
            "meta": {"kind": "note", "origin": "archive", "created_at": created_at},
        })
        citations.append(Citation(title=f"archive {created_at}", ref=created_at))

    current = await _workbench_notes(query, ctx)
    if current:
        hits.append({
            "text": f"[workbench] {current}",
            "meta": {"kind": "note", "origin": "workbench"},
        })
        citations.append(Citation(title="workbench", ref="workbench"))

    if not hits:
        logger.info("[sources.notes] nothing found query=%s", query[:120])
    return ProbeResult(hits=hits, citations=citations)


async def _archive_notes(query: str, ctx: ResearchContext) -> list[tuple[str, str]]:
    from infrastructure.memory.chroma_pipeline import _get_archive_collection
    from infrastructure.memory.embedder import embed_one

    try:
        col = await _to_thread(_get_archive_collection)
        if col is None:
            return []
        embedding = await _to_thread(lambda: embed_one(query))
        if embedding is None:
            return []
        results = await _to_thread(
            lambda: col.query(
                query_embeddings=[embedding],
                n_results=int(ctx.extras.get("top_k", 5)),
                where={"account_id": ctx.account_id},
                include=["documents", "metadatas", "distances"],
            )
        )
    except Exception as exc:
        logger.warning("[sources.notes] archive query failed: %s", exc)
        return []

    if not results or not results.get("ids") or not results["ids"][0]:
        return []

    out: list[tuple[str, str]] = []
    for doc, meta, distance in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        if distance < CHROMA_ARCHIVE_MAX_DISTANCE:
            out.append((doc, (meta or {}).get("created_at", "?")))
    return out


async def _workbench_notes(query: str, ctx: ResearchContext) -> str:
    from infrastructure.autonomy import workbench as wb

    try:
        found = await _to_thread(lambda: wb.search(ctx.account_id, query))
    except Exception as exc:
        logger.warning("[sources.notes] workbench search failed: %s", exc)
        return ""
    if not found or found.startswith("(workbench is empty)") or found.startswith("No notes"):
        return ""
    return found


# ── Registry ──────────────────────────────────────────────────────────────────

PROBES: dict[str, Callable[[str, ResearchContext], Awaitable[ProbeResult]]] = {
    Source.WEB: probe_web,
    Source.DIALOGUE: probe_dialogue,
    Source.FACTS: probe_facts,
    Source.NOTES: probe_notes,
}
