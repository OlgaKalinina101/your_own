"""
POST /api/chat

Streams the LLM response as SSE.

Unlike the original proxy implementation, this endpoint now:
  - saves live chat messages to PostgreSQL
  - loads recent canonical chat history from DB
  - retrieves semantically relevant Chroma facts as the memory block
  - assembles the final prompt server-side
  - parses [SAVE_MEMORY: ...] AI skill commands at end of response
  - supports [GENERATE_IMAGE: model | prompt] image generation skill
"""

from __future__ import annotations

from infrastructure.account import ACCOUNT_ID, resolve
import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.events import publish_pairs_changed
from infrastructure.database.engine import get_db
from infrastructure.database.repositories.message_repo import MessageRepository
from infrastructure.llm.client import LLMClient
from infrastructure.logging.logger import setup_logger
from infrastructure.clock import now_local
from infrastructure.memory.live_store import (
    build_canonical_row,
    build_chunk_rows,
    fill_chunk_embeddings,
)
from infrastructure.language import detect_or_soul
from infrastructure.memory.retrieval import humanize_timestamp
from infrastructure.memory.chroma_pipeline import get_chroma_pipeline
from infrastructure.auth import require_auth
from infrastructure.autonomy import context
from infrastructure.paths import GENERATED_IMAGES_DIR, LOGS_DIR, USER_UPLOADS_DIR
from infrastructure.skills import registry as skill_registry
from infrastructure.skills.base import SkillContext
from settings import settings

logger = setup_logger("chat")
MAX_CHAT_IMAGES = 8

for _directory in (LOGS_DIR, GENERATED_IMAGES_DIR, USER_UPLOADS_DIR):
    _directory.mkdir(parents=True, exist_ok=True)

# The chat trace, unlike the call corpus, really is a log: it exists to explain
# the last few requests and nothing reads it a month later. So it rotates —
# 10 MB x 3, which was roughly its size after five months of unbounded growth.
_DBG_PATH = LOGS_DIR / "chat_debug.log"
_dbg_logger = logging.getLogger("chat.trace")
if not _dbg_logger.handlers:
    _dbg_handler = RotatingFileHandler(
        _DBG_PATH, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    _dbg_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _dbg_logger.addHandler(_dbg_handler)
    _dbg_logger.setLevel(logging.DEBUG)
    _dbg_logger.propagate = False


def _dbg(msg: str) -> None:
    try:
        _dbg_logger.debug(msg)
    except Exception:
        pass

_dbg("MODULE_LOADED")

router = APIRouter(prefix="/api", tags=["chat"], dependencies=[Depends(require_auth)])


def _save_upload(payload: bytes, content_type: str) -> str:
    """Save raw image bytes to user_uploads/ and return the relative URL."""
    ext = "jpg"
    ct = content_type.lower()
    if "png" in ct:
        ext = "png"
    elif "webp" in ct:
        ext = "webp"
    elif "gif" in ct:
        ext = "gif"
    filename = f"{uuid.uuid4().hex}.{ext}"
    (USER_UPLOADS_DIR / filename).write_bytes(payload)
    return f"/api/user_uploads/{filename}"


@router.post("/upload")
async def upload_image(
    image: UploadFile = File(...),
):
    """Upload a single image and return its server URL."""
    payload = await image.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Empty file")
    url = _save_upload(payload, image.content_type or "image/jpeg")
    return {"url": url}


async def _post_analyze_background(
    account_id: str,
    recent_pairs: list[dict],
    current_user_text: str,
    current_assistant_text: str,
    api_key: str,
) -> None:
    try:
        from infrastructure.autonomy.post_analyzer import run_post_analysis
        await run_post_analysis(
            account_id=account_id,
            recent_pairs=recent_pairs,
            current_user_text=current_user_text,
            current_assistant_text=current_assistant_text,
            api_key=api_key,
        )
    except Exception as exc:
        logger.warning("[chat] post-analysis error: %s", exc)


def _note_degradation(account_id: str, name: str, detail: str) -> None:
    """Record on his own instrument panel that a reply went out incomplete.

    Never raises: a broken panel must not also break the reply it is describing.
    """
    try:
        from infrastructure.autonomy.vitals import Vitals

        Vitals(account_id).record_degradation(name, detail[:200])
    except Exception as exc:
        logger.error("[chat] could not record the degradation %r: %s", name, exc)


def _preview(text: str, limit: int = 180) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _build_chroma_block(facts: list[dict], language: str) -> str:
    """
    Format Chroma facts into a memory block injected as an assistant message
    before the current user turn. Written as the AI's inner recollections.
    """
    lines: list[str] = []
    for fact in facts:
        meta = fact.get("metadata") or {}
        created_at_str = meta.get("created_at")
        created_at_dt: Optional[datetime] = None
        if created_at_str:
            try:
                from datetime import timezone as _tz
                created_at_dt = datetime.fromisoformat(created_at_str)
                if created_at_dt.tzinfo is None:
                    created_at_dt = created_at_dt.replace(tzinfo=_tz.utc)
            except Exception:
                pass

        time_label = humanize_timestamp(created_at_dt, language)  # type: ignore[arg-type]
        text = fact.get("text", "").strip()
        lines.append(f"— ({time_label}) {text}")

    body = "\n".join(lines).strip()
    return f"<memory>\n{body}\n</memory>"


@dataclass
class _Recall:
    """What the long-term memory had to say about this message."""

    block: Optional[str] = None
    fact_ids: list[str] = field(default_factory=list)
    for_ui: list[dict] = field(default_factory=list)


async def _recall(account_id: str, text: str, language: str, cutoff_days: int) -> _Recall:
    """Ask Chroma what it remembers, and survive it not answering.

    A failure here means he replies without his long-term memory — a reply
    indistinguishable from one where the memory had nothing to say — so it is
    recorded on his instrument panel as well as in the log.
    """
    if not text.strip():
        return _Recall()

    try:
        pipeline = get_chroma_pipeline()
        facts = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: pipeline.query_similar_multi(
                account_id=account_id, message=text, top_k=5, days_cutoff=cutoff_days,
            ),
        )
    except Exception as exc:
        logger.error("[chat] Chroma retrieval failed — answering without memory: %s", exc)
        _note_degradation(account_id, "memory", str(exc))
        return _Recall()

    logger.info("[chat] chroma facts=%d", len(facts or []))
    if not facts:
        return _Recall()

    for_ui = []
    for fact in facts:
        meta = fact.get("metadata") or {}
        stamp = meta.get("created_at")
        for_ui.append({
            "id": fact.get("id", ""),
            "text": fact.get("text", ""),
            "category": meta.get("category", ""),
            "impressive": meta.get("impressive", 0),
            "time_label": humanize_timestamp(
                datetime.fromisoformat(stamp) if stamp else None, language
            ),
        })

    return _Recall(
        block=_build_chroma_block(facts, language),
        fact_ids=[fact["id"] for fact in facts],
        for_ui=for_ui,
    )


def _build_system_prompt(inputs: _Inputs, state: dict, skills: list) -> str:
    """Assemble what he is, plus what he can do, into one system prompt.

    Three parts and one rule each: the soul is who he is; the canon is the only
    identity pillar that loads into every conversation; the skills block carries
    the desk and the board with it because the header template asks for them.
    """
    threads_block = (
        f"<open_threads>\n{state['open_threads']}\n</open_threads>\n\n"
        if state["open_threads"] else ""
    )
    workbench_block = threads_block + (
        f"<workbench>\n{state['workbench']}\n</workbench>\n\n"
        if state["workbench"] else ""
    )

    identity = f"<identity>\n{inputs.soul}\n</identity>" if inputs.soul else ""
    canon = f"\n\n<canon>\n{state['canon']}\n</canon>" if state["canon"] else ""
    instructions = "\n\n" + skill_registry.build_prompt(
        lang=inputs.language,
        skills=skills,
        now_str=state["current_time"],
        workbench_block=workbench_block,
        timezone_label=state["timezone_label"],
        # A skill that fails to build is a skill he does not have this turn,
        # and the reply looks the same as one where he chose not to use it.
        on_lost=lambda ids: _note_degradation(
            inputs.account_id, "skills", ", ".join(ids)
        ),
    )
    return identity + canon + instructions


def _yield_chunk(chunk: str) -> list[str]:
    """One text chunk as SSE ``data:`` lines.

    A chunk can contain newlines and each one needs its own ``data:``; the
    client rejoins them. Splitting here is what keeps a multi-line reply from
    ending the event early.
    """
    lines = [f"data: {line}\n" for line in chunk.split("\n")]
    lines.append("\n")
    return lines


# How many times he may act, read the result and act again within one turn.
MAX_AGENT_LOOPS = 5


@dataclass
class _Reply:
    """The answer as it is being built, in the two forms it has to exist in.

    ``text`` is what the user sees and what goes back to the model as history.
    ``full`` is what is stored: the same words plus the command text and the
    markers ([GENERATED_IMAGE: …], [SAVED_FACT: …]) that let the client render
    the turn again later. They diverge, which is exactly why the loop that
    builds them needed six loose variables before this existed.
    """

    text: str = ""
    full: str = ""
    post_matches: list = field(default_factory=list)


async def _run_skills(
    *,
    actions: list,
    trailing_text: str,
    reply: _Reply,
    inputs: _Inputs,
    llm_messages: list[dict],
    system_prompt: str,
    skills: list,
    skill_ctx,
):
    """Run the commands he issued, one at a time, feeding each result back.

    Sequential on purpose: every result becomes the next prompt, so he can
    search, read what came back, and search again — up to
    :data:`MAX_AGENT_LOOPS` times. Yields SSE frames; the growing answer is
    accumulated into *reply*, which the caller owns.
    """
    pending = list(actions)
    round_number = 0
    _dbg(f"AGENT_LOOP_CHECK pending={len(pending)}")

    while round_number < MAX_AGENT_LOOPS and pending:
        round_number += 1
        skill, match = pending.pop(0)
        is_last_initial = not pending
        cmd_text = match.group(0)
        _dbg(f"AGENT_LOOP #{round_number} skill={skill.id} cmd={cmd_text[:80]}")

        # The command itself is shown as a badge for search and web; image
        # generation opts out, because there the picture is the feedback.
        if skill.stream_command_text:
            for line in _yield_chunk("\n" + cmd_text + "\n"):
                yield line

        for name, data in skill.pre_sse_events(match):
            yield _sse(name, data)

        # Image generation goes minutes without a byte, which is what a proxy cuts.
        result = None
        async for done, value in _pump(skill.execute(match, skill_ctx)):
            if done:
                result = value
            else:
                yield _SSE_KEEPALIVE

        for name, data in result.sse_events:
            yield _sse(name, data)

        for marker in result.db_markers:
            reply.full += "\n" + marker

        if skill.action_type == "inline":
            # Nothing goes back to the model: the skill's own output is the
            # answer. Only the text he had already written after the command
            # still needs saying.
            if is_last_initial and trailing_text:
                _dbg(f"TRAILING_APPEND len={len(trailing_text)}")
                reply.full += "\n\n" + trailing_text
                for line in _yield_chunk("\n\n" + trailing_text):
                    yield line
            continue

        continuation_prompt = result.continuation or ""
        if skill.id == "search_dialogue" and round_number < MAX_AGENT_LOOPS:
            from infrastructure.skills.search_dialogue.skill import skill as search_skill

            continuation_prompt = (
                search_skill.get_cont_hint(inputs.language, MAX_AGENT_LOOPS - round_number)
                + "\n\n" + continuation_prompt
            )
        if is_last_initial and trailing_text:
            continuation_prompt += (
                "\n\n" + skill_registry.get_trailing_hint(inputs.language) + "\n"
                + trailing_text
            )

        continuation_messages = [
            *llm_messages,
            {"role": "assistant", "content": reply.text},
            {"role": "user", "content": continuation_prompt},
        ]

        for line in _yield_chunk("\n\n"):
            yield line

        parts: list[str] = []
        async for chunk in inputs.client.stream(
            messages=continuation_messages, system_prompt=system_prompt
        ):
            if not chunk:
                continue
            parts.append(chunk)
            for line in _yield_chunk(chunk):
                yield line

        continuation = "".join(parts).strip()
        _dbg(f"CONTINUATION #{round_number} done len={len(continuation)}")
        logger.info(
            "[chat] continuation #%d done text=%s", round_number, _preview(continuation, 260)
        )

        clean, matches = skill_registry.strip_skills(continuation, skills)
        if clean:
            reply.text += "\n\n" + clean
        reply.full += "\n" + cmd_text + "\n\n" + continuation
        reply.post_matches.extend(
            (s, m) for s, m in matches if s.action_type == "post"
        )
        pending.extend(
            (s, m) for s, m in matches if s.action_type in ("agentic", "inline")
        )


async def _web_toggle(inputs: _Inputs, llm_messages: list[dict]):
    """Research the user's message up front, narrating progress as SSE.

    An async generator because the search takes minutes and the connection has
    to stay warm through it — it yields keepalives and the three ``web_*``
    events, and inserts the brief into *llm_messages* itself. Nothing is
    returned; the effect on the messages list is the result.
    """
    from infrastructure.agents import Source, research

    query = inputs.user_text[:200]
    yield _sse("web_start", {"query": query})

    result = None
    async for done, value in _pump(research(
        task=inputs.user_text,
        source=Source.WEB,
        api_key=inputs.api_key,
        account_id=inputs.account_id,
        lang=inputs.language,
    )):
        if done:
            result = value
        else:
            yield _SSE_KEEPALIVE

    _dbg(
        f"WEB_TOGGLE attempts={result.attempts} "
        f"citations={len(result.citations)} found={result.found}"
    )
    logger.info(
        "[chat] web toggle research attempts=%d found=%s", result.attempts, result.found
    )

    yield _sse("web_results", {
        "query": query,
        "brief": result.brief,
        "sources": [citation.to_dict() for citation in result.citations],
        "attempts": result.attempts,
    })
    yield _sse("web_done", {"query": query})

    if result.found:
        # Just before the current user turn, so the brief reads as something he
        # recalled rather than something appended after the question.
        llm_messages.insert(
            len(llm_messages) - 1,
            {"role": "assistant", "content": _build_web_block(result, inputs.language)},
        )


def _build_web_block(result, language: str) -> str:
    """Format a ResearchAgent web brief for injection before the user turn.

    Used by the manual web toggle; the [WEB_SEARCH: ...] command feeds its
    brief through the skill's own continuation prompt instead.
    """
    lines = [result.brief.strip()]
    if result.citations:
        lines.append("")
        lines.append("Источники:" if language == "ru" else "Sources:")
        for citation in result.citations:
            lines.append(
                f"- {citation.title} ({citation.url})" if citation.url else f"- {citation.title}"
            )
    body = "\n".join(lines).strip()
    return f"<web_search>\n{body}\n</web_search>"


# ── SSE plumbing ─────────────────────────────────────────────────────────────
#
# The stream is written by hand, so everything a library would give for free has
# to be here explicitly: a keepalive, an error event, and a place to persist a
# reply whose stream did not reach the end.

# Silence a proxy will tolerate. Localhost needs none of this; between a server
# and a phone there will be something that cuts a quiet connection, and both
# image generation and a web brief routinely go a minute without a byte.
_KEEPALIVE_EVERY_S = 15.0

# An SSE comment: keeps the connection warm and is ignored by the client.
_SSE_KEEPALIVE = ": keepalive\n\n"

# A task with no strong reference can be garbage-collected mid-flight.
_BG_TASKS: set[asyncio.Task] = set()


def _spawn_bg(coro) -> asyncio.Task:
    """Run *coro* detached from the request, keeping it alive until it finishes."""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


async def _pump(awaitable, *, every: float = _KEEPALIVE_EVERY_S):
    """Await *awaitable*, ticking while it works.

    Yields ``(False, None)`` every *every* seconds, then ``(True, result)`` once.
    Cancels the underlying task if the caller stops iterating (client hung up).
    """
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=every)
            if done:
                yield True, task.result()
                return
            yield False, None
    finally:
        if not task.done():
            task.cancel()


@dataclass
class _Inputs:
    """One chat request, after the form has been read and the settings merged.

    The endpoint used to do this inline: fourteen form fields, the stored
    settings underneath them, three try/except parses and the image resolution,
    all in the same scope as the prompt assembly and the SSE generator. Pulling
    it out is what makes the rest of `chat()` readable, and it is the only part
    of the request that can be tested without a database.
    """

    account_id: str
    api_key: str
    model: str
    soul: Optional[str]
    temperature: float
    top_p: float
    history_pairs: int
    cutoff_days: int
    do_web_search: bool
    messages: list[dict]
    user_text: str
    language: str
    image_items: list[tuple[bytes, str]]
    upload_urls: list[str]
    images_from_urls: bool

    @property
    def client(self) -> LLMClient:
        return LLMClient(
            api_key=self.api_key,
            model=self.model,
            temperature=self.temperature,
            top_p=self.top_p,
        )


def _clamp(value: Optional[str], default: int, low: int, high: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _as_float(value: Optional[str], default: float) -> float:
    try:
        return float(value) if value else default
    except (TypeError, ValueError):
        return default


def _content_type_of(filename: str) -> str:
    for suffix, content_type in (
        (".png", "image/png"), (".webp", "image/webp"), (".gif", "image/gif"),
    ):
        if filename.endswith(suffix):
            return content_type
    return "image/jpeg"


async def _read_images(
    image_urls_json: Optional[str],
    image: Optional[UploadFile],
    images: Optional[list[UploadFile]],
) -> tuple[list[tuple[bytes, str]], list[str], bool]:
    """Resolve pictures from either pre-uploaded URLs or a legacy multipart body.

    Returns ``(image_items, urls, came_from_urls)``. The two shapes exist because
    the client used to send the bytes with the message and now uploads them
    first; both are still accepted.
    """
    urls: list[str] = []
    from_urls = False

    if image_urls_json:
        try:
            parsed = json.loads(image_urls_json)
            if isinstance(parsed, list):
                urls = [u for u in parsed if isinstance(u, str)]
                from_urls = True
        except json.JSONDecodeError:
            pass

    items: list[tuple[bytes, str]] = []

    if from_urls:
        for url in urls:
            path = USER_UPLOADS_DIR / url.rsplit("/", 1)[-1]
            if path.is_file():
                items.append((path.read_bytes(), _content_type_of(path.name)))
        return items, urls, True

    uploaded = [item for item in (images or []) if item and item.filename]
    if image and image.filename:
        uploaded.append(image)
    if len(uploaded) > MAX_CHAT_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Up to {MAX_CHAT_IMAGES} images allowed per message.",
        )

    for upload in uploaded:
        payload = await upload.read()
        if payload:
            items.append((payload, upload.content_type or "image/jpeg"))
    urls = [_save_upload(payload, content_type) for payload, content_type in items]
    return items, urls, False


async def _read_inputs(
    *,
    messages: str,
    model: Optional[str],
    api_key: Optional[str],
    web_search: str,
    temperature: Optional[str],
    top_p: Optional[str],
    account_id: Optional[str],
    history_pairs: Optional[str],
    memory_cutoff_days: Optional[str],
    system_prompt: Optional[str],
    image_urls_json: Optional[str],
    image: Optional[UploadFile],
    images: Optional[list[UploadFile]],
) -> _Inputs:
    """Read the form, fill the gaps from stored settings, resolve the pictures.

    Every field is optional over the wire: the desktop client sends the lot, the
    phone sends almost none, and what is missing comes from ``data/settings.json``.
    """
    from infrastructure.settings_store import DEFAULT_MODEL, load_settings, load_soul

    stored = load_settings()

    try:
        parsed_messages: list[dict] = json.loads(messages)
    except json.JSONDecodeError:
        parsed_messages = []

    latest_user = next(
        (msg for msg in reversed(parsed_messages) if msg.get("role") == "user"), None
    )
    user_text = (latest_user or {}).get("content", "") if latest_user else ""

    image_items, upload_urls, from_urls = await _read_images(
        image_urls_json, image, images
    )

    return _Inputs(
        account_id=resolve(account_id),
        api_key=api_key or stored.get("openrouter_api_key", ""),
        model=model or stored.get("model", DEFAULT_MODEL),
        soul=system_prompt or (load_soul() or None),
        temperature=_as_float(temperature, stored.get("temperature", 0.7)),
        top_p=_as_float(top_p, stored.get("top_p", 0.9)),
        history_pairs=_clamp(
            history_pairs or str(stored.get("history_pairs", "")),
            settings.CHAT_HISTORY_PAIRS_DEFAULT,
            settings.CHAT_HISTORY_PAIRS_MIN,
            settings.CHAT_HISTORY_PAIRS_MAX,
        ),
        cutoff_days=_clamp(
            memory_cutoff_days or str(stored.get("memory_cutoff_days", "")),
            settings.MEMORY_CUTOFF_DAYS_DEFAULT,
            settings.MEMORY_CUTOFF_DAYS_MIN,
            settings.MEMORY_CUTOFF_DAYS_MAX,
        ),
        do_web_search=web_search.lower() == "true",
        messages=parsed_messages,
        user_text=user_text,
        language=detect_or_soul(user_text),
        image_items=image_items,
        upload_urls=upload_urls,
        images_from_urls=from_urls,
    )


def _sse(event: str, payload: dict) -> str:
    """One named SSE event, serialised in one place.

    This was written out by hand at eleven call sites as a pair of yields —
    ``yield "event: x\\n"`` then ``yield f"data: {...}\\n\\n"``. Two lines that
    must stay together, eleven chances to separate them.
    """
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# The stream's terminator. The client stops reading here.
_SSE_DONE = "data: [DONE]\n\n"


def _sse_error(message: str, pair_id: uuid.UUID) -> str:
    """One error event, so a failed stream is not a stream that just stopped.

    Headers went out with the first byte, so the status is already 200: without
    this the client cannot tell a crash from a model that chose to say nothing.
    """
    payload = json.dumps({"message": message, "pair_id": str(pair_id)}, ensure_ascii=False)
    return f"event: error\ndata: {payload}\n\n"




@router.delete("/chat/pair/{pair_id}")
async def delete_chat_pair(
    pair_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete all messages belonging to a pair_id. Used by client for error cleanup."""
    repo = MessageRepository(db)
    deleted = await repo.delete_pair(pair_id)
    return {"deleted": deleted}


@router.get("/chat/history")
async def chat_history(
    account_id: str = Query(ACCOUNT_ID),
    limit_pairs: int = Query(25, ge=1, le=100),
    before: Optional[str] = Query(None),
    after: Optional[str] = Query(
        None,
        description="Return only pairs newer than this timestamp — what a client "
                    "asks for when it comes back and wants to know what it missed.",
    ),
    db: AsyncSession = Depends(get_db),
):
    def _parse(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            # An unparseable cursor asks for the default page rather than 400:
            # a client with a corrupted cursor should still see its conversation.
            return None

    repo = MessageRepository(db)
    pairs, next_before, has_more = await repo.get_canonical_pairs_page(
        account_id=account_id,
        limit_pairs=limit_pairs,
        before=_parse(before),
        after=_parse(after),
    )
    return {
        "pairs": [
            {
                "pair_id": str(item["pair_id"]),
                "created_at": item["created_at"].isoformat() if item["created_at"] else None,
                "pair_created_at": item["pair_created_at"].isoformat() if item.get("pair_created_at") else None,
                "user_text": item["user_text"],
                "assistant_text": item["assistant_text"],
                "user_image_urls": item.get("user_image_urls"),
            }
            for item in pairs
        ],
        "next_before": next_before.isoformat() if next_before else None,
        "has_more": has_more,
    }


_HALLUC_MARKER_RE = re.compile(r"\[GENERATED[_ ]IMAGE:.*?\]", re.DOTALL | re.IGNORECASE)


def _strip_hallucinated_markers(text: str) -> str:
    """Remove [GENERATED_IMAGE: ...] markers the model copies out of its history."""
    return _HALLUC_MARKER_RE.sub("", text).strip()


def _assemble_llm_messages(
    *,
    recent_pairs: list[dict],
    memory_block: str,
    user_text: str,
    skills: list,
) -> list[dict]:
    """The conversation as the model will see it: history, memory, the question.

    Past assistant turns are stripped of internal markers first. They are ours,
    not his — left in, the model reads them as part of how it talks and starts
    producing them itself.
    """
    internal = skill_registry.build_internal_markers_re(skills)
    messages: list[dict] = []
    for item in reversed(recent_pairs):
        if item["user_text"]:
            messages.append({"role": "user", "content": item["user_text"]})
        if item["assistant_text"]:
            messages.append({
                "role": "assistant",
                "content": internal.sub("", item["assistant_text"]).strip(),
            })
    if memory_block:
        messages.append({"role": "assistant", "content": memory_block})
    messages.append({"role": "user", "content": user_text})
    return messages


def _assistant_rows(
    pair_id: uuid.UUID, account_id: Optional[str], text_full: str, text_clean: str
) -> list:
    """The rows one reply becomes: the canonical text, and chunks for searching.

    The two texts differ on purpose. What is stored whole is what she sees,
    markers and all; what is chunked and embedded is the cleaned text, because
    a marker is not something to find her way back to later.
    """
    created = now_local()
    return [
        build_canonical_row(
            pair_id=pair_id,
            account_id=resolve(account_id),
            role="assistant",
            text=text_full,
            created_at=created,
        ),
        *build_chunk_rows(
            pair_id=pair_id,
            account_id=resolve(account_id),
            role="assistant",
            text=text_clean,
            created_at=created,
        ),
    ]


async def _save_partial(pair_id: uuid.UUID, account_id: Optional[str], text: str) -> None:
    """Persist what she already saw when the stream did not reach the end.

    Opens its own session on purpose: this runs after the request has been torn
    down (the client hung up), and the request-scoped one is gone by then.
    Losing the reply entirely is worse than storing a clipped one — the client
    can still drop the pair by its pair_id.
    """
    text = (text or "").strip()
    if not text:
        return
    try:
        from infrastructure.database.engine import get_db_session
        rows = _assistant_rows(pair_id, account_id, text, text)
        await asyncio.get_running_loop().run_in_executor(None, fill_chunk_embeddings, rows)
        async with get_db_session() as _db:
            # Conditional on the pair still existing, and it has to be, because
            # this task is detached: computing the embeddings takes seconds, and
            # `DELETE /api/chat/pair/{id}` can arrive inside that window. Writing
            # unconditionally left a half-reply with no question in front of it.
            written = await MessageRepository(_db).bulk_save_if_pair_exists(rows, str(pair_id))
        if not written:
            logger.info(
                "[chat] dropped a partial reply for pair=%s — the pair was deleted "
                "while it was being prepared",
                pair_id,
            )
            return
        logger.warning(
            "[chat] saved a partial reply for pair=%s (%d chars) — stream did not finish",
            pair_id, len(text),
        )
        # A clipped reply is still history, and the client whose stream broke is
        # precisely the one that does not know what was kept.
        publish_pairs_changed(account_id=resolve(account_id), origin="chat")
    except Exception as exc:
        logger.error("[chat] could not save the partial reply for pair=%s: %s", pair_id, exc)


@dataclass
class _Parsed:
    """A finished reply, taken apart: what to show, and what to act on."""

    text: str
    actions: list
    post: list
    all_matches: list
    trailing: str


def _parse_reply(full_text: str, skills: list) -> _Parsed:
    """Separate what he said from what he asked the system to do."""
    text, all_matches = skill_registry.strip_skills(full_text, skills)

    actions = [(s, m) for s, m in all_matches if s.action_type in ("agentic", "inline")]
    post = [(s, m) for s, m in all_matches if s.action_type == "post"]

    # When only post-skills fire (save/schedule) mid-reply, cut the raw commands
    # out and keep every word around them: nothing here replaces the reply, so
    # the surrounding text is the reply.
    if not actions and post:
        stripped = full_text
        for _, m in sorted(post, key=lambda pair: pair[1].start(), reverse=True):
            stripped = stripped[:m.start()] + stripped[m.end():]
        text = re.sub(r"\n{3,}", "\n\n", stripped).strip()

    # Anything he wrote after the last command still belongs to her.
    trailing = ""
    if actions:
        last_end = max(m.end() for _, m in actions)
        tail_clean, _ = skill_registry.strip_skills(
            _strip_hallucinated_markers(full_text[last_end:]), skills
        )
        trailing = tail_clean.strip()

    return _Parsed(text=text, actions=actions, post=post,
                   all_matches=all_matches, trailing=trailing)


def _strip_raw_commands(text: str, skills: list) -> str:
    """Last pass before storing: no raw command text reaches the database."""
    cleaned = skill_registry.build_cleanup_re(skills).sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


async def _mark_facts_used(fact_ids: list) -> None:
    """Tell Chroma which recalled facts were actually put in front of him.

    Best-effort by design: a bookkeeping failure must not cost her the reply
    that is already on screen.
    """
    if not fact_ids:
        return
    try:
        pipeline = get_chroma_pipeline()
        loop = asyncio.get_running_loop()
        for fid in fact_ids:
            await loop.run_in_executor(None, lambda fid=fid: pipeline.update_usage(fid))
    except Exception as exc:
        logger.warning("[chat] Chroma update_usage failed: %s", exc)


@dataclass
class _Streamed:
    """What the model has said so far, and whether any of it is being held back."""

    parts: list[str] = field(default_factory=list)
    buffering: bool = False

    @property
    def text(self) -> str:
        return "".join(self.parts)


async def _stream_initial(
    client: LLMClient,
    *,
    messages: list[dict],
    image_items: list | None,
    system_prompt: str,
    command_re,
    state: _Streamed,
) -> AsyncIterator[str]:
    """Relay his first answer, holding it back the moment a command appears.

    Every chunk is kept in *state* whether or not it is sent on, because a reply
    cut short still has to be storable. Once a command opener shows up nothing
    more goes out: the text after it is arguments, not words for her, and what
    she should see is decided only when the whole reply has been parsed.

    That silence can last the length of the reply, which is long enough for a
    proxy to decide the connection is dead — hence the keepalives.
    """
    last_emit = time.monotonic()
    async for chunk in client.stream(
        messages=messages,
        image_items=image_items or None,
        system_prompt=system_prompt,
    ):
        if not chunk:
            continue
        state.parts.append(chunk)

        if state.buffering:
            if time.monotonic() - last_emit >= _KEEPALIVE_EVERY_S:
                last_emit = time.monotonic()
                yield _SSE_KEEPALIVE
            continue

        if command_re.search(state.text):
            state.buffering = True
            _dbg("BUFFER_START — command detected, buffering rest of stream")
            continue

        for sse_line in _yield_chunk(chunk):
            yield sse_line


async def _apply_post_skills(
    post_matches: list,
    *,
    text: str,
    text_full: str,
    ctx: SkillContext,
) -> tuple[str, list[str]]:
    """Run the skills that act *after* the reply, and say what they did.

    These do not change the answer, they follow from it — a fact saved, a
    message scheduled. Nothing here streams as it happens, so the frames come
    back as a list rather than a generator: they are all produced at the end
    anyway, and a plain function is easier to be sure of.

    The stored text keeps the raw commands and gains a marker per saved fact.
    That is deliberate: the marker is how the client renders "I remembered
    this", and the pair has to render the same way when it is reloaded from the
    database tomorrow.
    """
    frames: list[str] = []

    for skill, match in post_matches:
        if skill.id == "save_memory" and match.group(0) not in text_full:
            text_full += "\n" + match.group(0)

    save_matches = [m for s, m in post_matches if s.id == "save_memory"]
    sched_matches = [m for s, m in post_matches if s.id == "schedule_message"]
    _dbg(
        f"POST_SKILLS all_post={len(post_matches)} save_matches={len(save_matches)} "
        f"sched_matches={len(sched_matches)}"
    )

    from infrastructure.skills.save_memory.skill import skill as save_skill
    results = await save_skill.execute_batch(save_matches, text, ctx)
    logger.info("[chat] save_memory results=%d", len(results))

    for result in results:
        if result.get("dedup") in ("skipped", "replaced"):
            continue
        stars = result.get("impressive", 0)
        marker = f"\n[SAVED_FACT: {result['category']} | {stars} | {result['fact']}]"
        text_full += marker
        frames.extend(_yield_chunk(marker))

    from infrastructure.skills.schedule_message.skill import skill as sched_skill
    await sched_skill.execute_batch(sched_matches, ctx)

    return text_full, frames


@router.post("/chat")
async def chat(
    messages:    str           = Form(...),
    model:       Optional[str] = Form(None),
    api_key:     Optional[str] = Form(None),
    web_search:  str           = Form("false"),
    temperature: Optional[str] = Form(None),
    top_p:       Optional[str] = Form(None),
    account_id:         Optional[str] = Form(ACCOUNT_ID),
    history_pairs:      Optional[str] = Form(None),
    memory_cutoff_days: Optional[str] = Form(None),
    system_prompt:  Optional[str] = Form(None),
    image_urls_json: Optional[str] = Form(None, alias="image_urls"),
    image:          Optional[UploadFile] = File(None),
    images:         Optional[list[UploadFile]] = File(None),
    db:             AsyncSession = Depends(get_db),
):
    inputs = await _read_inputs(
        messages=messages, model=model, api_key=api_key, web_search=web_search,
        temperature=temperature, top_p=top_p, account_id=account_id,
        history_pairs=history_pairs, memory_cutoff_days=memory_cutoff_days,
        system_prompt=system_prompt, image_urls_json=image_urls_json,
        image=image, images=images,
    )
    account_id = inputs.account_id
    api_key = inputs.api_key
    model = inputs.model
    system_prompt = inputs.soul
    cutoff_days = inputs.cutoff_days
    do_web_search = inputs.do_web_search
    image_items = inputs.image_items
    upload_urls = inputs.upload_urls
    images_from_urls = inputs.images_from_urls
    current_user_text = inputs.user_text
    prompt_language = inputs.language
    client = inputs.client

    repo = MessageRepository(db)
    _dbg(f"REQUEST model={model} web_toggle={do_web_search} lang={prompt_language} user={_preview(current_user_text)}")
    logger.info(
        "[chat] request account=%s model=%s web_toggle=%s lang=%s images=%d history_pairs=%d cutoff_days=%d user=%s",
        resolve(account_id),
        model,
        do_web_search,
        prompt_language,
        len(image_items),
        inputs.history_pairs,
        cutoff_days,
        _preview(current_user_text),
    )

    pair_id = uuid.uuid4()
    user_created_at = now_local()
    saved_user = False
    if current_user_text.strip():
        user_rows = [
            build_canonical_row(
                pair_id=pair_id,
                account_id=resolve(account_id),
                role="user",
                text=current_user_text,
                created_at=user_created_at,
                image_urls=upload_urls if upload_urls else None,
            ),
            *build_chunk_rows(
                pair_id=pair_id,
                account_id=resolve(account_id),
                role="user",
                text=current_user_text,
                created_at=user_created_at,
            ),
        ]
        # torch encode is synchronous CPU work: on the event loop it freezes
        # every other request, including other clients' open SSE streams.
        await asyncio.get_running_loop().run_in_executor(None, fill_chunk_embeddings, user_rows)
        await repo.bulk_save(user_rows)
        saved_user = True

    recent_pairs = await repo.get_recent_canonical_pairs(
        account_id=resolve(account_id),
        limit_pairs=inputs.history_pairs,
        exclude_pair_ids=[pair_id] if saved_user else None,
    )
    logger.info("[chat] recent history pairs=%d", len(recent_pairs))

    recall = await _recall(
        inputs.account_id, current_user_text, prompt_language, cutoff_days
    )
    chroma_memory_block = recall.block
    chroma_fact_ids = recall.fact_ids
    chroma_facts_for_ui = recall.for_ui

    # Which state blocks chat is entitled to is decided in one place for all
    # four consumers — see infrastructure/autonomy/context.py.
    state = context.build(
        context.Consumer.CHAT,
        context.Request(account_id=inputs.account_id, lang=prompt_language),
    )
    enabled_skills = skill_registry.get_enabled(inputs.account_id)
    combined_system_prompt = _build_system_prompt(inputs, state, enabled_skills)

    logger.info(
        "[chat] prompt assembled system_chars=%d memory_block=%s",
        len(combined_system_prompt),
        "yes" if chroma_memory_block else "no",
    )

    llm_messages = _assemble_llm_messages(
        recent_pairs=recent_pairs,
        memory_block=chroma_memory_block,
        user_text=current_user_text,
        skills=enabled_skills,
    )
    _dbg(
        "LLM_MESSAGES "
        + json.dumps(
            [
                {
                    "role": msg["role"],
                    "preview": _preview(msg.get("content", ""), 160),
                }
                for msg in llm_messages
            ],
            ensure_ascii=False,
        )
    )
    logger.info("[chat] llm_messages count=%d", len(llm_messages))

    _CMD_OPEN_RE = skill_registry.build_open_re(enabled_skills)

    skill_ctx = SkillContext(
        db=db,
        client=client,
        account_id=resolve(account_id),
        api_key=api_key,
        lang=prompt_language,
        recent_pairs=recent_pairs,
        current_user_text=current_user_text,
        cutoff_days=cutoff_days,
        logger=logger,
        dbg=_dbg,
    )

    async def event_stream():
        # Always emit pair_id first so the client can clean up on error
        yield _sse("pair_id", {'pair_id': str(pair_id)})

        if upload_urls and not images_from_urls:
            yield _sse("image_urls", {'urls': upload_urls})

        streamed = _Streamed()
        # Guards the partial-save paths below: once the real row is in, a later
        # failure must not write a second, clipped copy of the same reply.
        saved_assistant = False
        try:
            # The toggle and [WEB_SEARCH: ...] both go through the one
            # orchestrator; this is the toggle's path.
            if do_web_search and current_user_text.strip():
                async for frame in _web_toggle(inputs, llm_messages):
                    yield frame

            logger.info("[chat] initial stream start")
            async for frame in _stream_initial(
                client,
                messages=llm_messages,
                image_items=image_items,
                system_prompt=combined_system_prompt,
                command_re=_CMD_OPEN_RE,
                state=streamed,
            ):
                yield frame
            buffering = streamed.buffering

            raw_full = streamed.text.strip()
            has_halluc = bool(_HALLUC_MARKER_RE.search(raw_full))
            full_text = _strip_hallucinated_markers(raw_full)
            if has_halluc:
                _dbg(f"HALLUC_STRIP removed [GENERATED_IMAGE:] raw_len={len(raw_full)} clean_len={len(full_text)}")
            _dbg(f"STREAM_DONE full_text_len={len(full_text)} buffered={buffering}")
            _dbg(f"FULL_TEXT>>>{full_text}<<<END")
            logger.info("[chat] initial stream done text=%s", _preview(full_text, 260))

            # ── Parse all skill commands via registry ─────────────────────
            parsed = _parse_reply(full_text, enabled_skills)
            assistant_text = parsed.text
            assistant_text_full = parsed.text
            action_matches = parsed.actions
            post_matches = parsed.post
            all_matches = parsed.all_matches
            has_actions = bool(action_matches)

            _dbg(f"PARSED actions={len(action_matches)} post={len(post_matches)} clean_len={len(assistant_text)}")
            logger.info("[chat] parsed skills actions=%d post=%d clean=%s",
                        len(action_matches), len(post_matches), _preview(assistant_text, 220))

            if not all_matches:
                _dbg("NO_SKILLS_DETECTED")

            if not has_actions and buffering:
                _dbg("BUFFER_FLUSH — no actions, flushing buffered post-only text")
                yield _sse("rewrite", {'text': assistant_text})

            if has_actions:
                _dbg(f"REWRITE clean_text before actions len={len(assistant_text)}")
                yield _sse("rewrite", {'text': assistant_text})

            trailing_text = parsed.trailing
            if trailing_text:
                _dbg(f"TRAILING_TEXT len={len(trailing_text)}: {trailing_text[:100]}")

            reply = _Reply(
                text=assistant_text,
                full=assistant_text_full,
                post_matches=list(post_matches),
            )
            async for frame in _run_skills(
                actions=action_matches,
                trailing_text=trailing_text,
                reply=reply,
                inputs=inputs,
                llm_messages=llm_messages,
                system_prompt=combined_system_prompt,
                skills=enabled_skills,
                skill_ctx=skill_ctx,
            ):
                yield frame
            assistant_text = reply.text
            assistant_text_full = reply.full
            all_post_matches = reply.post_matches

            assistant_text_full, post_frames = await _apply_post_skills(
                all_post_matches,
                text=assistant_text,
                text_full=assistant_text_full,
                ctx=skill_ctx,
            )
            for frame in post_frames:
                yield frame

            # ── Final cleanup ────────────────────────────────────────────
            cleaned = _strip_raw_commands(assistant_text_full, enabled_skills)
            if cleaned != assistant_text_full:
                _dbg(f"FINAL_CLEAN stripped raw cmds old_len={len(assistant_text_full)} new_len={len(cleaned)}")
                assistant_text_full = cleaned
            _dbg(f"SAVE_TO_DB assistant_text_full len={len(assistant_text_full)}")
            _dbg(f"SAVE_CONTENT>>>{assistant_text_full}<<<END")

            if assistant_text_full:
                assistant_rows = _assistant_rows(
                    pair_id, account_id, assistant_text_full, assistant_text
                )
                await asyncio.get_running_loop().run_in_executor(
                    None, fill_chunk_embeddings, assistant_rows
                )
                await repo.bulk_save(assistant_rows)
                saved_assistant = True
                # The pair is history now. The client that sent it already has
                # it and will recognise its own by pair_id; every other open
                # client is why this line exists.
                publish_pairs_changed(account_id=resolve(account_id), origin="chat")

            # Fire post-dialogue analysis in background (no delay for the user)
            _spawn_bg(_post_analyze_background(
                account_id=resolve(account_id),
                recent_pairs=recent_pairs,
                current_user_text=current_user_text,
                current_assistant_text=assistant_text,
                api_key=api_key,
            ))

            await _mark_facts_used(chroma_fact_ids)

            # Use the same chroma_facts that were injected into the model (no second query)
            yield _sse("memory", {'chroma_facts': chroma_facts_for_ui})

            # save_memory_results are now embedded in the text as [SAVED_FACT: ...] markers

            yield _SSE_DONE

        except (asyncio.CancelledError, GeneratorExit):
            # The client hung up. Nothing may be yielded from here — a generator
            # that yields while closing raises "async generator ignored
            # GeneratorExit" — so the partial reply is persisted by a detached
            # task that outlives this one.
            partial = streamed.text.strip()
            _dbg(f"CLIENT_GONE partial_len={len(partial)}")
            logger.info(
                "[chat] client disconnected for account=%s pair=%s after %d chars",
                account_id, pair_id, len(partial),
            )
            if partial and not saved_assistant:
                _spawn_bg(_save_partial(pair_id, account_id, partial))
            raise

        except Exception as e:
            import traceback
            _dbg(f"EXCEPTION: {e}\n{traceback.format_exc()}")
            logger.exception("[chat] Streaming error for account=%s: %s", account_id, e)
            # The session is still alive here, so save inline rather than detached.
            if not saved_assistant:
                await _save_partial(pair_id, account_id, streamed.text)
            yield _sse_error(str(e) or e.__class__.__name__, pair_id)
            yield _SSE_DONE

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
