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

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database.engine import get_db
from infrastructure.database.repositories.message_repo import MessageRepository
from infrastructure.llm.client import LLMClient
from infrastructure.logging.logger import setup_logger
from infrastructure.memory.live_store import (
    build_canonical_row,
    build_chunk_rows,
    fill_chunk_embeddings,
    now_local,
)
from infrastructure.memory.focus_point import detect_language
from infrastructure.memory.retrieval import humanize_timestamp
from infrastructure.memory.chroma_pipeline import get_chroma_pipeline
from infrastructure.auth import require_auth
from infrastructure.autonomy import workbench as wb
from infrastructure.settings_store import now_local_str
from infrastructure.skills import registry as skill_registry
from infrastructure.skills.base import SkillContext
from settings import settings

logger = setup_logger("chat")
MAX_CHAT_IMAGES = 8

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_LOGS_DIR = _PROJECT_ROOT / "logs"
_GENERATED_IMAGES_DIR = _PROJECT_ROOT / "generated_images"
_USER_UPLOADS_DIR = _PROJECT_ROOT / "user_uploads"

_LOGS_DIR.mkdir(parents=True, exist_ok=True)
_GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
_USER_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

_DBG_PATH = _LOGS_DIR / "chat_debug.log"

def _dbg(msg: str) -> None:
    try:
        with _DBG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
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
    (_USER_UPLOADS_DIR / filename).write_bytes(payload)
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
    account_id: str = Query("default"),
    limit_pairs: int = Query(25, ge=1, le=100),
    before: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    before_dt: Optional[datetime] = None
    if before:
        try:
            before_dt = datetime.fromisoformat(before.replace("Z", "+00:00"))
        except ValueError:
            before_dt = None

    repo = MessageRepository(db)
    pairs, next_before, has_more = await repo.get_canonical_pairs_page(
        account_id=account_id,
        limit_pairs=limit_pairs,
        before=before_dt,
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


@router.post("/chat")
async def chat(
    messages:    str           = Form(...),
    model:       Optional[str] = Form(None),
    api_key:     Optional[str] = Form(None),
    web_search:  str           = Form("false"),
    temperature: Optional[str] = Form(None),
    top_p:       Optional[str] = Form(None),
    account_id:         Optional[str] = Form("default"),
    history_pairs:      Optional[str] = Form(None),
    memory_cutoff_days: Optional[str] = Form(None),
    system_prompt:  Optional[str] = Form(None),
    image_urls_json: Optional[str] = Form(None, alias="image_urls"),
    image:          Optional[UploadFile] = File(None),
    images:         Optional[list[UploadFile]] = File(None),
    db:             AsyncSession = Depends(get_db),
):
    from infrastructure.settings_store import load_settings, load_soul
    srv = load_settings()

    api_key = api_key or srv.get("openrouter_api_key", "")
    from infrastructure.settings_store import DEFAULT_MODEL
    model = model or srv.get("model", DEFAULT_MODEL)
    if not system_prompt:
        system_prompt = load_soul() or None

    print(f"[CHAT_DEBUG] ENDPOINT_HIT model={model}", flush=True)
    _dbg(f"ENDPOINT_HIT model={model} web_search={web_search}")

    try:
        parsed_messages: list[dict] = json.loads(messages)
    except json.JSONDecodeError:
        parsed_messages = []

    do_web_search = web_search.lower() == "true"

    try:
        temp_float = float(temperature) if temperature else srv.get("temperature", 0.7)
    except ValueError:
        temp_float = srv.get("temperature", 0.7)

    try:
        top_p_float = float(top_p) if top_p else srv.get("top_p", 0.9)
    except ValueError:
        top_p_float = srv.get("top_p", 0.9)

    def clamp(value: Optional[str], default: int, min_value: int, max_value: int) -> int:
        try:
            parsed = int(value) if value is not None else default
        except ValueError:
            parsed = default
        return max(min_value, min(max_value, parsed))

    history_pairs_int = clamp(
        history_pairs or str(srv.get("history_pairs", "")),
        settings.CHAT_HISTORY_PAIRS_DEFAULT,
        settings.CHAT_HISTORY_PAIRS_MIN,
        settings.CHAT_HISTORY_PAIRS_MAX,
    )
    cutoff_days = clamp(
        memory_cutoff_days or str(srv.get("memory_cutoff_days", "")),
        settings.MEMORY_CUTOFF_DAYS_DEFAULT,
        settings.MEMORY_CUTOFF_DAYS_MIN,
        settings.MEMORY_CUTOFF_DAYS_MAX,
    )

    # --- resolve images (pre-uploaded URLs or legacy FormData uploads) ---
    upload_urls: list[str] = []
    images_from_urls = False

    if image_urls_json:
        try:
            parsed_urls = json.loads(image_urls_json)
            if isinstance(parsed_urls, list):
                upload_urls = [u for u in parsed_urls if isinstance(u, str)]
                images_from_urls = True
        except json.JSONDecodeError:
            pass

    uploaded_images: list[UploadFile] = []
    if not images_from_urls:
        if images:
            uploaded_images.extend([item for item in images if item and item.filename])
        if image and image.filename:
            uploaded_images.append(image)
        if len(uploaded_images) > MAX_CHAT_IMAGES:
            raise HTTPException(status_code=400, detail=f"Up to {MAX_CHAT_IMAGES} images allowed per message.")

    image_items: list[tuple[bytes, str]] = []

    if images_from_urls:
        for rel_url in upload_urls:
            fname = rel_url.rsplit("/", 1)[-1]
            fpath = _USER_UPLOADS_DIR / fname
            if fpath.is_file():
                ct = "image/jpeg"
                if fname.endswith(".png"):
                    ct = "image/png"
                elif fname.endswith(".webp"):
                    ct = "image/webp"
                elif fname.endswith(".gif"):
                    ct = "image/gif"
                image_items.append((fpath.read_bytes(), ct))
    else:
        for uploaded in uploaded_images:
            payload = await uploaded.read()
            if payload:
                image_items.append((payload, uploaded.content_type or "image/jpeg"))
        for payload, content_type in image_items:
            upload_urls.append(_save_upload(payload, content_type))

    client = LLMClient(
        api_key=api_key,
        model=model,
        temperature=temp_float,
        top_p=top_p_float,
    )

    repo = MessageRepository(db)
    latest_user = next((msg for msg in reversed(parsed_messages) if msg.get("role") == "user"), None)
    current_user_text = (latest_user or {}).get("content", "") if latest_user else ""
    prompt_language = detect_language(current_user_text) if current_user_text.strip() else "en"
    _dbg(f"REQUEST model={model} web_toggle={do_web_search} lang={prompt_language} user={_preview(current_user_text)}")
    logger.info(
        "[chat] request account=%s model=%s web_toggle=%s lang=%s images=%d history_pairs=%d cutoff_days=%d user=%s",
        account_id or "default",
        model,
        do_web_search,
        prompt_language,
        len(image_items),
        history_pairs_int,
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
                account_id=account_id or "default",
                role="user",
                text=current_user_text,
                created_at=user_created_at,
                image_urls=upload_urls if upload_urls else None,
            ),
            *build_chunk_rows(
                pair_id=pair_id,
                account_id=account_id or "default",
                role="user",
                text=current_user_text,
                created_at=user_created_at,
            ),
        ]
        fill_chunk_embeddings(user_rows)
        await repo.bulk_save(user_rows)
        saved_user = True

    recent_pairs = await repo.get_recent_canonical_pairs(
        account_id=account_id or "default",
        limit_pairs=history_pairs_int,
        exclude_pair_ids=[pair_id] if saved_user else None,
    )
    logger.info("[chat] recent history pairs=%d", len(recent_pairs))

    # ── Chroma long-term memory block ──────────────────────────────────────
    chroma_memory_block: Optional[str] = None
    chroma_fact_ids: list[str] = []
    chroma_facts_for_ui: list[dict] = []
    if current_user_text.strip():
        try:
            pipeline = get_chroma_pipeline()
            chroma_facts = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: pipeline.query_similar_multi(
                    account_id=account_id or "default",
                    message=current_user_text,
                    top_k=5,
                    days_cutoff=cutoff_days,
                )
            )
            if chroma_facts:
                chroma_fact_ids = [f["id"] for f in chroma_facts]
                chroma_memory_block = _build_chroma_block(chroma_facts, prompt_language)
                for f in chroma_facts:
                    meta = f.get("metadata") or {}
                    ts = meta.get("created_at")
                    chroma_facts_for_ui.append({
                        "id": f.get("id", ""),
                        "text": f.get("text", ""),
                        "category": meta.get("category", ""),
                        "impressive": meta.get("impressive", 0),
                        "time_label": humanize_timestamp(
                            datetime.fromisoformat(ts) if ts else None,
                            prompt_language,
                        ),
                    })
            logger.info("[chat] chroma facts=%d", len(chroma_facts or []))
        except Exception as exc:
            logger.warning("[chat] Chroma retrieval failed: %s", exc)

    # Current time for SCHEDULE_MESSAGE and general awareness
    from infrastructure.settings_store import tz_label
    _now_str = now_local_str()
    _tz_label = tz_label()

    # Recent workbench entries for context
    _recent_wb = wb.get_recent_entries(account_id or "default")
    _workbench_block = (
        f"<workbench>\n{_recent_wb}\n</workbench>\n\n"
        if _recent_wb else ""
    )

    # ── Skill registry ─────────────────────────────────────────────────────
    enabled_skills = skill_registry.get_enabled(account_id or "default")
    skill_instructions = "\n\n" + skill_registry.build_prompt(
        lang=prompt_language,
        skills=enabled_skills,
        now_str=_now_str,
        workbench_block=_workbench_block,
        timezone_label=_tz_label,
    )
    _identity_block = f"<identity>\n{system_prompt}\n</identity>" if system_prompt else ""
    combined_system_prompt = _identity_block + skill_instructions
    logger.info(
        "[chat] prompt assembled system_chars=%d memory_block=%s",
        len(combined_system_prompt),
        "yes" if chroma_memory_block else "no",
    )

    llm_messages: list[dict] = []
    _INTERNAL_MARKERS_RE = skill_registry.build_internal_markers_re(enabled_skills)

    def _clean_for_llm(text: str) -> str:
        """Strip internal markers that the LLM should never see or learn to reproduce."""
        return _INTERNAL_MARKERS_RE.sub("", text).strip()

    for item in reversed(recent_pairs):
        if item["user_text"]:
            llm_messages.append({"role": "user", "content": item["user_text"]})
        if item["assistant_text"]:
            llm_messages.append({"role": "assistant", "content": _clean_for_llm(item["assistant_text"])})
    if chroma_memory_block:
        llm_messages.append({"role": "assistant", "content": chroma_memory_block})
    llm_messages.append({"role": "user", "content": current_user_text})
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

    def _yield_chunk(chunk: str):
        """Yields SSE lines for a text chunk."""
        lines = []
        for line in chunk.split("\n"):
            lines.append(f"data: {line}\n")
        lines.append("\n")
        return lines

    _HALLUC_MARKER_RE = re.compile(r"\[GENERATED[_ ]IMAGE:.*?\]", re.DOTALL | re.IGNORECASE)

    def _strip_hallucinated_markers(text: str) -> str:
        """Remove [GENERATED_IMAGE: ...] markers that the LLM copies from chat history."""
        return _HALLUC_MARKER_RE.sub("", text).strip()

    _CMD_OPEN_RE = skill_registry.build_open_re(enabled_skills)

    skill_ctx = SkillContext(
        db=db,
        client=client,
        account_id=account_id or "default",
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
        yield f"event: pair_id\ndata: {json.dumps({'pair_id': str(pair_id)})}\n\n"

        if upload_urls and not images_from_urls:
            yield "event: image_urls\n"
            yield f"data: {json.dumps({'urls': upload_urls})}\n\n"

        assistant_parts: list[str] = []
        stream_completed = False
        buffering = False
        try:
            logger.info("[chat] initial stream start")
            async for chunk in client.stream(
                messages=llm_messages,
                web_search=do_web_search,
                image_items=image_items or None,
                system_prompt=combined_system_prompt,
            ):
                if not chunk:
                    continue
                assistant_parts.append(chunk)

                if buffering:
                    continue

                accumulated = "".join(assistant_parts)
                if _CMD_OPEN_RE.search(accumulated):
                    buffering = True
                    _dbg("BUFFER_START — command detected, buffering rest of stream")
                    continue

                for sse_line in _yield_chunk(chunk):
                    yield sse_line

            stream_completed = True
            raw_full = "".join(assistant_parts).strip()
            has_halluc = bool(_HALLUC_MARKER_RE.search(raw_full))
            full_text = _strip_hallucinated_markers(raw_full)
            if has_halluc:
                _dbg(f"HALLUC_STRIP removed [GENERATED_IMAGE:] raw_len={len(raw_full)} clean_len={len(full_text)}")
            _dbg(f"STREAM_DONE full_text_len={len(full_text)} buffered={buffering}")
            _dbg(f"FULL_TEXT>>>{full_text}<<<END")
            logger.info("[chat] initial stream done text=%s", _preview(full_text, 260))

            # ── Parse all skill commands via registry ─────────────────────
            assistant_text, all_matches = skill_registry.strip_skills(full_text, enabled_skills)

            action_matches = [(s, m) for s, m in all_matches if s.action_type in ("agentic", "inline")]
            post_matches = [(s, m) for s, m in all_matches if s.action_type == "post"]
            has_actions = bool(action_matches)

            # When only post-skills fire (save/schedule) mid-reply, strip
            # the raw commands but keep ALL surrounding text intact.
            if not has_actions and post_matches:
                stripped = full_text
                for _, m in sorted(post_matches, key=lambda x: x[1].start(), reverse=True):
                    stripped = stripped[:m.start()] + stripped[m.end():]
                assistant_text = re.sub(r"\n{3,}", "\n\n", stripped).strip()

            assistant_text_full = assistant_text

            _dbg(f"PARSED actions={len(action_matches)} post={len(post_matches)} clean_len={len(assistant_text)}")
            logger.info("[chat] parsed skills actions=%d post=%d clean=%s",
                        len(action_matches), len(post_matches), _preview(assistant_text, 220))

            if not all_matches:
                _dbg("NO_SKILLS_DETECTED")

            if not has_actions and buffering:
                _dbg("BUFFER_FLUSH — no actions, flushing buffered post-only text")
                yield "event: rewrite\n"
                yield f"data: {json.dumps({'text': assistant_text})}\n\n"

            if has_actions:
                _dbg(f"REWRITE clean_text before actions len={len(assistant_text)}")
                yield "event: rewrite\n"
                yield f"data: {json.dumps({'text': assistant_text})}\n\n"

            trailing_text = ""
            if action_matches:
                last_action_end = max(m.end() for _, m in action_matches)
                raw_tail = _strip_hallucinated_markers(full_text[last_action_end:])
                tail_clean, _ = skill_registry.strip_skills(raw_tail, enabled_skills)
                trailing_text = tail_clean.strip()
                if trailing_text:
                    _dbg(f"TRAILING_TEXT len={len(trailing_text)}: {trailing_text[:100]}")

            # ── Sequential agentic loop ──────────────────────────────────────
            MAX_AGENT_LOOPS = 5
            agent_loop = 0
            pending_actions: list[tuple] = list(action_matches)
            # Accumulate post-skill matches (initial + from continuations)
            all_post_matches: list[tuple] = list(post_matches)
            _dbg(f"AGENT_LOOP_CHECK pending={len(pending_actions)}")

            while agent_loop < MAX_AGENT_LOOPS and pending_actions:
                agent_loop += 1
                current_skill, action_match = pending_actions.pop(0)
                is_last_initial = not pending_actions
                cmd_text = action_match.group(0)
                _dbg(f"AGENT_LOOP #{agent_loop} skill={current_skill.id} cmd={cmd_text[:80]}")

                # Stream the command text as a badge (search/web) unless the skill opts out (image)
                if current_skill.stream_command_text:
                    for sse_line in _yield_chunk("\n" + cmd_text + "\n"):
                        yield sse_line

                # Emit pre-SSE events (search_start, web_start, image_start, etc.)
                for event_name, event_data in current_skill.pre_sse_events(action_match):
                    yield f"event: {event_name}\n"
                    yield f"data: {json.dumps(event_data)}\n\n"

                # Execute the skill
                result = await current_skill.execute(action_match, skill_ctx)

                # Emit post-SSE events (search_results, image_ready, image_cancel, etc.)
                for event_name, event_data in result.sse_events:
                    yield f"event: {event_name}\n"
                    yield f"data: {json.dumps(event_data)}\n\n"

                # Append DB markers (e.g. [GENERATED_IMAGE: ...])
                for marker in result.db_markers:
                    assistant_text_full += "\n" + marker

                if current_skill.action_type == "inline":
                    if is_last_initial and trailing_text:
                        _dbg(f"TRAILING_APPEND len={len(trailing_text)}")
                        assistant_text_full += "\n\n" + trailing_text
                        for sse_line in _yield_chunk("\n\n" + trailing_text):
                            yield sse_line
                    continue

                # ── Agentic continuation ──────────────────────────────────
                continuation_prompt = result.continuation or ""

                # For search: prepend cont_hint if more loops remain
                if current_skill.id == "search_memories" and agent_loop < MAX_AGENT_LOOPS:
                    from infrastructure.skills.search_memories.skill import skill as search_skill
                    cont_hint = search_skill.get_cont_hint(prompt_language, MAX_AGENT_LOOPS - agent_loop)
                    continuation_prompt = cont_hint + "\n\n" + continuation_prompt

                if is_last_initial and trailing_text:
                    hint_prefix = "\n\n" + skill_registry.get_trailing_hint(prompt_language) + "\n"
                    continuation_prompt += hint_prefix + trailing_text

                continuation_messages = list(llm_messages)
                continuation_messages.append({"role": "assistant", "content": assistant_text})
                continuation_messages.append({"role": "user", "content": continuation_prompt})

                separator = "\n\n"
                for sse_line in _yield_chunk(separator):
                    yield sse_line

                if current_skill.id == "web_search":
                    yield "event: web_done\n"
                    yield f"data: {json.dumps({'query': action_match.group(1).strip()})}\n\n"

                continuation_parts: list[str] = []
                async for chunk in client.stream(
                    messages=continuation_messages,
                    web_search=result.continuation_web_search,
                    system_prompt=combined_system_prompt,
                ):
                    if not chunk:
                        continue
                    continuation_parts.append(chunk)
                    for sse_line in _yield_chunk(chunk):
                        yield sse_line

                continuation_text = "".join(continuation_parts).strip()
                _dbg(f"CONTINUATION #{agent_loop} done len={len(continuation_text)}")
                logger.info("[chat] continuation #%d done text=%s", agent_loop, _preview(continuation_text, 260))

                cont_clean, cont_matches = skill_registry.strip_skills(continuation_text, enabled_skills)
                cont_actions = [(s, m) for s, m in cont_matches if s.action_type in ("agentic", "inline")]
                cont_posts = [(s, m) for s, m in cont_matches if s.action_type == "post"]

                if cont_clean:
                    assistant_text = assistant_text + "\n\n" + cont_clean
                assistant_text_full = assistant_text_full + "\n" + cmd_text + "\n\n" + continuation_text
                all_post_matches.extend(cont_posts)
                pending_actions.extend(cont_actions)

            # Append SAVE_MEMORY command text to full text for persistent rendering
            for s, sm in all_post_matches:
                if s.id == "save_memory" and sm.group(0) not in assistant_text_full:
                    assistant_text_full += "\n" + sm.group(0)

            # ── Post-skills: SAVE_MEMORY ─────────────────────────────────
            save_matches_raw = [m for s, m in all_post_matches if s.id == "save_memory"]
            _dbg(f"POST_SKILLS all_post={len(all_post_matches)} save_matches={len(save_matches_raw)} sched_matches={len([m for s, m in all_post_matches if s.id == 'schedule_message'])}")
            from infrastructure.skills.save_memory.skill import skill as save_skill
            save_memory_results = await save_skill.execute_batch(save_matches_raw, assistant_text, skill_ctx)
            logger.info("[chat] save_memory results=%d", len(save_memory_results))

            for sr in save_memory_results:
                if sr.get("dedup") in ("skipped", "replaced"):
                    continue
                stars = sr.get("impressive", 0)
                marker = f"\n[SAVED_FACT: {sr['category']} | {stars} | {sr['fact']}]"
                assistant_text_full += marker
                for sse_line in _yield_chunk(marker):
                    yield sse_line

            # ── Post-skills: SCHEDULE_MESSAGE ────────────────────────────
            sched_matches_raw = [m for s, m in all_post_matches if s.id == "schedule_message"]
            from infrastructure.skills.schedule_message.skill import skill as sched_skill
            await sched_skill.execute_batch(sched_matches_raw, skill_ctx)

            # ── Final cleanup ────────────────────────────────────────────
            _CLEANUP_RE = skill_registry.build_cleanup_re(enabled_skills)
            cleaned = _CLEANUP_RE.sub("", assistant_text_full)
            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
            if cleaned != assistant_text_full:
                _dbg(f"FINAL_CLEAN stripped raw cmds old_len={len(assistant_text_full)} new_len={len(cleaned)}")
                assistant_text_full = cleaned
            _dbg(f"SAVE_TO_DB assistant_text_full len={len(assistant_text_full)}")
            _dbg(f"SAVE_CONTENT>>>{assistant_text_full}<<<END")

            if assistant_text_full and not assistant_text_full.startswith("[OpenRouter error"):
                assistant_created_at = now_local()
                assistant_rows = [
                    build_canonical_row(
                        pair_id=pair_id,
                        account_id=account_id or "default",
                        role="assistant",
                        text=assistant_text_full,
                        created_at=assistant_created_at,
                    ),
                    *build_chunk_rows(
                        pair_id=pair_id,
                        account_id=account_id or "default",
                        role="assistant",
                        text=assistant_text,
                        created_at=assistant_created_at,
                    ),
                ]
                fill_chunk_embeddings(assistant_rows)
                await repo.bulk_save(assistant_rows)

            # Fire post-dialogue analysis in background (no delay for the user)
            _bg_task = asyncio.create_task(_post_analyze_background(
                account_id=account_id or "default",
                recent_pairs=recent_pairs,
                current_user_text=current_user_text,
                current_assistant_text=assistant_text,
                api_key=api_key,
            ))
            _bg_task.add_done_callback(lambda t: t.result() if not t.cancelled() and not t.exception() else None)

            # Update Chroma usage for retrieved facts
            if chroma_fact_ids:
                try:
                    _chroma_pipeline = get_chroma_pipeline()
                    for fid in chroma_fact_ids:
                        await asyncio.get_running_loop().run_in_executor(
                            None, lambda fid=fid: _chroma_pipeline.update_usage(fid)
                        )
                except Exception as exc:
                    logger.warning("[chat] Chroma update_usage failed: %s", exc)

            # Use the same chroma_facts that were injected into the model (no second query)
            yield "event: memory\n"
            yield f"data: {json.dumps({'chroma_facts': chroma_facts_for_ui})}\n\n"

            # save_memory_results are now embedded in the text as [SAVED_FACT: ...] markers

        except Exception as e:
            import traceback
            _dbg(f"EXCEPTION: {e}\n{traceback.format_exc()}")
            logger.exception("[chat] Streaming error for account=%s: %s", account_id, e)
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
