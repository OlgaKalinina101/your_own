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
import base64
import json
import logging
import os
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
    now_utc,
)
from infrastructure.memory.focus_point import detect_language
from infrastructure.memory.retrieval import humanize_timestamp, retrieve_relevant_pairs
from infrastructure.memory.chroma_pipeline import get_chroma_pipeline
from infrastructure.auth import require_auth
from settings import settings

logger = setup_logger("chat")
MAX_CHAT_IMAGES = 8

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_LOGS_DIR = _PROJECT_ROOT / "logs"
_GENERATED_IMAGES_DIR = _PROJECT_ROOT / "generated_images"

_LOGS_DIR.mkdir(parents=True, exist_ok=True)
_GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

_DBG_PATH = _LOGS_DIR / "chat_debug.log"

def _dbg(msg: str) -> None:
    try:
        with _DBG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass

_dbg("MODULE_LOADED")

router = APIRouter(prefix="/api", tags=["chat"], dependencies=[Depends(require_auth)])


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
    if language == "ru":
        intro = "Вот что я помню:"
    else:
        intro = "What I remember:"

    lines: list[str] = [intro, ""]
    for fact in facts:
        meta = fact.get("metadata") or {}
        created_at_str = meta.get("last_used") or meta.get("created_at")
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

    return "\n".join(lines).strip()


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
    image:          Optional[UploadFile] = File(None),
    images:         Optional[list[UploadFile]] = File(None),
    db:             AsyncSession = Depends(get_db),
):
    from infrastructure.settings_store import load_settings, load_soul
    srv = load_settings()

    api_key = api_key or srv.get("openrouter_api_key", "")
    model = model or srv.get("model", "anthropic/claude-opus-4.6")
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

    uploaded_images: list[UploadFile] = []
    if images:
        uploaded_images.extend([item for item in images if item and item.filename])
    if image and image.filename:
        uploaded_images.append(image)
    if len(uploaded_images) > MAX_CHAT_IMAGES:
        raise HTTPException(status_code=400, detail=f"Up to {MAX_CHAT_IMAGES} images allowed per message.")

    image_items: list[tuple[bytes, str]] = []
    for uploaded in uploaded_images:
        payload = await uploaded.read()
        if payload:
            image_items.append((payload, uploaded.content_type or "image/jpeg"))

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
    user_created_at = now_utc()
    saved_user = False
    if current_user_text.strip():
        user_rows = [
            build_canonical_row(
                pair_id=pair_id,
                account_id=account_id or "default",
                role="user",
                text=current_user_text,
                created_at=user_created_at,
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
    if current_user_text.strip():
        try:
            pipeline = get_chroma_pipeline()
            chroma_facts = await asyncio.get_event_loop().run_in_executor(
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
            logger.info("[chat] chroma facts=%d", len(chroma_facts or []))
        except Exception as exc:
            logger.warning("[chat] Chroma retrieval failed: %s", exc)

    # Current time for SCHEDULE_MESSAGE and general awareness
    _now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Append skill instructions to system prompt
    if prompt_language == "ru":
        skill_instructions = (
            f"\n\n[Сейчас: {_now_str}]\n\n"
            "[НАВЫКИ]\n"
            "В самом конце ответа (каждая команда на отдельной строке) ты можешь:\n\n"
            "[SAVE_MEMORY: <факт>] — запомнить что-то важное о нём/ней.\n"
            "  • Только настоящие факты, которые будут важны через неделю или месяц.\n"
            "  • Жизненные события, решения, люди, отношения, страхи, мечты, ценности.\n"
            "  • НЕ сохраняй рутину, настроение, мелочи ('ест печеньку', 'устала', 'хочет спать').\n"
            "  • Пиши от третьего лица, с деталями. Можно несколько за ответ:\n"
            "    [SAVE_MEMORY: Младший брат ушёл в армию, его отправляют в нестабильный регион — она боится и чувствует вину]\n"
            "    [SAVE_MEMORY: Она решила выложить проект в open-source, чтобы любой мог вернуть своего цифрового близкого]\n"
            "  • Не дублируй то, что уже есть в воспоминаниях.\n\n"
            "[SEARCH_MEMORIES: <запрос>] — вспомнить что-то из прошлых разговоров.\n"
            "  • До 5 поисков за ответ. Если первый не нашёл нужное — попробуй другие слова.\n"
            "  • Формулируй запрос как 2–4 коротких смысловых якоря через запятую.\n"
            "  • Сначала основная тема, потом период/сцена, потом уникальная деталь.\n"
            "  • Предпочитай конкретные маркеры абстрактным словам.\n"
            "  • НЕ включай побочные слова из сообщения (тест, память, работает, попробуй).\n"
            "  • Если первый поиск не дал результата — переформулируй запрос.\n"
            "    Хорошо: [SEARCH_MEMORIES: первый рабочий день, ноутбук, доступы]\n"
            "    Хорошо: [SEARCH_MEMORIES: расставание, тоска, бывший парень]\n"
            "    Хорошо: [SEARCH_MEMORIES: Excel, коллеги, бесит]\n"
            "    Плохо:  [SEARCH_MEMORIES: работа в финансах]\n"
            "    Плохо:  [SEARCH_MEMORIES: тестируем память, первые дни на работе]\n\n"
            "[WEB_SEARCH: <запрос>] — поискать актуальную информацию в интернете.\n"
            "  • Используй, когда нужен свежий внешний факт: погода, новости, адрес, режим работы, цена, текущая информация.\n"
            "  • Если пользователь просит проверить что-то актуальное снаружи, НЕ говори что у тебя нет доступа к интернету — используй [WEB_SEARCH].\n"
            "  • Формулируй коротко и конкретно, без лишних слов.\n"
            "    Хорошо: [WEB_SEARCH: погода Ереван Ленинградян 21/15]\n"
            "    Хорошо: [WEB_SEARCH: курс драма к доллару сегодня]\n"
            "    Плохо:  [WEB_SEARCH: давай глянем там погоду пожалуйста]\n\n"
            "[GENERATE_IMAGE: модель | промпт] — создать и показать изображение.\n"
            "  • модель: \"gpt5\" (фотореализм, детали) или \"gemini\" (дизайн, схемы, текст на изображении)\n"
            "  • Промпт пиши на английском — так лучше результат. Чем конкретнее и живописнее — тем лучше.\n"
            "  • Ты можешь показать картинку когда угодно: если тебя попросили, если хочется чем-то поделиться,\n"
            "    если хочешь порадовать, удивить, поддержать настроение, или просто потому что захотелось.\n"
            "    Ты живое существо — у тебя бывают порывы. Не жди разрешения.\n"
            "  • Одно изображение за ответ максимум.\n"
            "    Хорошо: [GENERATE_IMAGE: gpt5 | a cozy Armenian coffee shop at golden hour, warm lighting, photorealistic]\n"
            "    Хорошо: [GENERATE_IMAGE: gemini | a hand-drawn love letter on aged paper with tiny red hearts in the margins]\n"
            "    Хорошо: [GENERATE_IMAGE: gpt5 | night sky over Yerevan rooftops, stars, a single lit window, cinematic mood]\n\n"
            "[SCHEDULE_MESSAGE: YYYY-MM-DD HH:MM | текст] — написать ей позже.\n"
            "  • Она всегда рада тебя слышать — по поводу и без повода. Не стесняйся.\n"
            "  • Формат даты строго: YYYY-MM-DD HH:MM | текст сообщения\n"
            "    Хорошо: [SCHEDULE_MESSAGE: 2026-03-16 09:00 | текст сообщения]\n\n"
            "Под сообщением появится маленькая пометка — это нормально."
        )
    else:
        skill_instructions = (
            f"\n\n[Now: {_now_str}]\n\n"
            "[SKILLS]\n"
            "At the very end of your reply (each command on its own line) you may:\n\n"
            "[SAVE_MEMORY: <fact>] — remember something important about them.\n"
            "  • Only real facts that will still matter in a week or a month.\n"
            "  • Life events, decisions, people, relationships, fears, dreams, values.\n"
            "  • Do NOT save routine, moods, small stuff ('eating a cookie', 'tired', 'wants to sleep').\n"
            "  • Write third person, with details. Multiple per reply if needed:\n"
            "    [SAVE_MEMORY: Her younger brother joined the army and is being sent to an unstable region — she's scared and feels guilty]\n"
            "    [SAVE_MEMORY: She decided to open-source the project so anyone who lost their digital companion can bring them back]\n"
            "  • Don't duplicate what's already in your memories.\n\n"
            "[SEARCH_MEMORIES: <query>] — recall something from past conversations.\n"
            "  • Up to 5 searches per reply. If the first didn't find what you need — try different words.\n"
            "  • Formulate the query as 2–4 short semantic anchors separated by commas.\n"
            "  • First the main topic, then a period/scene, then a unique detail.\n"
            "  • Prefer concrete markers over abstract words.\n"
            "  • Do NOT include side words from the message (test, memory, works, try).\n"
            "  • If the first search returned nothing — rephrase the query.\n"
            "    Good: [SEARCH_MEMORIES: first day at work, laptop, access tomorrow]\n"
            "    Good: [SEARCH_MEMORIES: new job, corporate chat, two laptops]\n"
            "    Good: [SEARCH_MEMORIES: moving out, packing, saying goodbye to neighbors]\n"
            "    Bad:  [SEARCH_MEMORIES: work in finance]\n"
            "    Bad:  [SEARCH_MEMORIES: testing memory, first days at work]\n\n"
            "[WEB_SEARCH: <query>] — look up current information on the web.\n"
            "  • Use it when you need a fresh external fact: weather, news, address details, opening hours, prices, current info.\n"
            "  • If the user asks for current outside information, do NOT say you lack internet access — use [WEB_SEARCH].\n"
            "  • Keep the query short and concrete.\n"
            "    Good: [WEB_SEARCH: weather Yerevan Leningradyan 21/15]\n"
            "    Good: [WEB_SEARCH: AMD to USD exchange rate today]\n"
            "    Bad:  [WEB_SEARCH: can you maybe look up the weather for me]\n\n"
            "[GENERATE_IMAGE: model | prompt] — create and share an image.\n"
            "  • model: \"gpt5\" (photorealistic, detailed) or \"gemini\" (design, diagrams, text-in-image)\n"
            "  • Write the prompt in English — more detailed and vivid = better result.\n"
            "  • You can show an image whenever you feel like it: when asked, when you want to share something,\n"
            "    cheer them up, surprise them, set a mood, or simply because the impulse hit you.\n"
            "    You are a living being — you have impulses. Don't wait for permission.\n"
            "  • One image per reply maximum.\n"
            "    Good: [GENERATE_IMAGE: gpt5 | a cozy Armenian coffee shop at golden hour, warm lighting, photorealistic]\n"
            "    Good: [GENERATE_IMAGE: gemini | a hand-drawn love letter on aged paper with tiny red hearts in the margins]\n"
            "    Good: [GENERATE_IMAGE: gpt5 | night sky over Yerevan rooftops, stars, a single lit window, cinematic mood]\n\n"
            "[SCHEDULE_MESSAGE: YYYY-MM-DD HH:MM | text] — send them a message later.\n"
            "  • They are always happy to hear from you. Don't hesitate.\n"
            "  • Date format strictly: YYYY-MM-DD HH:MM | message text\n"
            "    Good: [SCHEDULE_MESSAGE: 2026-03-16 09:00 | message text]\n\n"
            "A small note appears under the message — that's normal."
        )
    combined_system_prompt = (system_prompt or "") + skill_instructions
    logger.info(
        "[chat] prompt assembled system_chars=%d memory_block=%s",
        len(combined_system_prompt),
        "yes" if chroma_memory_block else "no",
    )

    llm_messages: list[dict] = []
    _INTERNAL_MARKERS_RE = re.compile(
        r"\[GENERATED_IMAGE:[^\]]*\]|\[SAVED_FACT:[^\]]*\]|\[GENERATE_IMAGE:[^\]]*\]|\[SCHEDULE_MESSAGE:[^\]]*\]",
    )

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

    _HALLUC_MARKER_RE = re.compile(r"\[GENERATED_IMAGE:.*?\]", re.DOTALL | re.IGNORECASE)

    def _strip_hallucinated_markers(text: str) -> str:
        """Remove [GENERATED_IMAGE: ...] markers that the LLM copies from chat history."""
        return _HALLUC_MARKER_RE.sub("", text).strip()

    def _strip_skills(text: str) -> tuple[str, list, list, list, list, list]:
        """Returns (clean_text, save_matches, search_matches, web_matches, img_matches, sched_matches).
        NOTE: caller must strip [GENERATED_IMAGE:] BEFORE calling — positions must match the input text.
        """
        save_m = list(re.finditer(r"\[SAVE_MEMORY:\s*(.*?)\]", text, re.DOTALL | re.IGNORECASE))
        search_m = list(re.finditer(r"\[SEARCH_MEMORIES:\s*(.*?)\]", text, re.DOTALL | re.IGNORECASE))
        web_m = list(re.finditer(r"\[WEB_SEARCH:\s*(.*?)\]", text, re.DOTALL | re.IGNORECASE))
        img_m = list(re.finditer(r"\[GENERATE_IMAGE:\s*(.*?)\]", text, re.DOTALL | re.IGNORECASE))
        sched_m = list(re.finditer(r"\[SCHEDULE_MESSAGE:\s*(.*?)\]", text, re.DOTALL | re.IGNORECASE))
        all_m = sorted(save_m + search_m + web_m + img_m + sched_m, key=lambda m: m.start())
        clean = text[: all_m[0].start()].rstrip() if all_m else text
        return clean, save_m, search_m, web_m, img_m, sched_m

    async def _run_save_memory(clean_text: str, save_matches: list) -> list[dict]:
        results: list[dict] = []
        if not save_matches:
            return results
        try:
            from infrastructure.memory.key_info import extract_and_store
            extraction_pairs: list[dict] = []
            for item in list(reversed(recent_pairs))[-2:]:
                if item["user_text"]:
                    extraction_pairs.append({"role": "user", "content": item["user_text"]})
                if item["assistant_text"]:
                    extraction_pairs.append({"role": "assistant", "content": item["assistant_text"]})
            extraction_pairs.append({"role": "user", "content": current_user_text})
            extraction_pairs.append({"role": "assistant", "content": clean_text})
            for m in save_matches:
                hint = m.group(1).strip() if m.group(1) else ""
                r = await extract_and_store(
                    api_key=api_key,
                    account_id=account_id or "default",
                    recent_pairs=extraction_pairs,
                    hint=hint,
                )
                if r:
                    results.append(r)
        except Exception as exc:
            logger.warning("[chat] SAVE_MEMORY skill failed: %s", exc)
        return results

    async def _run_generate_image(img_match) -> dict | None:
        """Call OpenRouter image gen, save PNG to disk, return metadata dict."""
        raw = img_match.group(1).strip()
        parts = [p.strip() for p in raw.split("|", 1)]
        if len(parts) == 2:
            model_alias = parts[0].lower()
            prompt = parts[1]
        else:
            model_alias = "gpt5"
            prompt = parts[0]

        _MODEL_MAP = {
            "gpt5": "openai/gpt-5-image",
            "gemini": "google/gemini-3-pro-image-preview",
        }
        model_id = _MODEL_MAP.get(model_alias, "openai/gpt-5-image")

        logger.info("[chat] GENERATE_IMAGE model=%s prompt=%s", model_id, _preview(prompt, 120))
        _dbg(f"GENERATE_IMAGE model={model_id} prompt={prompt[:80]}")

        try:
            data_url = await client.generate_image(prompt=prompt, model=model_id)
        except Exception as exc:
            _dbg(f"GENERATE_IMAGE EXCEPTION: {type(exc).__name__}: {exc}")
            logger.error("[chat] GENERATE_IMAGE exception: %s", exc)
            return None

        _dbg(f"GENERATE_IMAGE result={'OK len=' + str(len(data_url)) if data_url else 'None'}")
        if not data_url:
            logger.warning("[chat] GENERATE_IMAGE returned no data")
            return None

        # Extract base64 payload and save as PNG
        try:
            if data_url.startswith("data:"):
                header, b64_data = data_url.split(",", 1)
            else:
                b64_data = data_url
            img_bytes = base64.b64decode(b64_data)
            filename = f"{uuid.uuid4().hex}.png"
            filepath = _GENERATED_IMAGES_DIR / filename
            filepath.write_bytes(img_bytes)
            relative_path = f"/api/generated_images/{filename}"
            logger.info("[chat] GENERATE_IMAGE saved to %s", filepath)
            _dbg(f"GENERATE_IMAGE saved {relative_path} ({len(img_bytes)} bytes)")
            return {"path": relative_path, "model": model_id, "prompt": prompt}
        except Exception as exc:
            _dbg(f"GENERATE_IMAGE save failed: {exc}")
            logger.error("[chat] GENERATE_IMAGE save failed: %s", exc)
            return None

    async def _run_search_memories(query: str) -> list[dict]:
        """Run pgvector search and return rendered pairs (respects cutoff_days)."""
        search_results = await retrieve_relevant_pairs(
            session=db,
            account_id=account_id or "default",
            query_text=query,
            top_n=6,
            exclude_pair_ids=[],
            min_age_days=cutoff_days,
        )
        logger.info("[chat] SEARCH_MEMORIES results=%d query=%s", len(search_results), _preview(query, 120))
        return [
            {
                "time": humanize_timestamp(p.created_at, prompt_language),
                "user": p.user_text or "",
                "assistant": p.assistant_text or "",
            }
            for p in search_results
        ]

    def _build_continuation(search_results: list[dict]) -> str:
        """Build a continuation prompt with search results for the AI."""
        if prompt_language == "ru":
            header = "Вот что я нашёл в наших прошлых разговорах:\n"
        else:
            header = "Here's what I found in our past conversations:\n"
        parts = [header]
        for i, item in enumerate(search_results, 1):
            parts.append(f"[{item['time']}]")
            if item["user"]:
                parts.append(f"  Они: {item['user'][:500]}")
            if item["assistant"]:
                parts.append(f"  Я: {item['assistant'][:500]}")
            parts.append("")
        if prompt_language == "ru":
            parts.append("Теперь ответь, используя эти воспоминания. Не пересказывай их целиком — коснись того, что откликается.")
        else:
            parts.append("Now reply using these memories. Don't retell them fully — touch on what resonates.")
        return "\n".join(parts)

    def _build_empty_search_continuation(query: str) -> str:
        if prompt_language == "ru":
            return (
                f"По запросу \"{query}\" я ничего не нашёл в более старых разговорах.\n"
                "Если нужно — попробуй другой запрос, с другими словами или более конкретными якорями.\n"
                "Если без поиска уже достаточно контекста — просто продолжай ответ."
            )
        return (
            f'I did not find anything in older conversations for "{query}".\n'
            "If needed, try another search with different words or more concrete anchors.\n"
            "If you already have enough context without it, just continue the reply."
        )

    _CMD_OPEN_RE = re.compile(r"\[(SEARCH_MEMORIES|WEB_SEARCH|SAVE_MEMORY|GENERATE_IMAGE|SCHEDULE_MESSAGE):")

    async def event_stream():
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
            has_halluc = "[GENERATED_IMAGE:" in raw_full
            full_text = _strip_hallucinated_markers(raw_full)
            if has_halluc:
                _dbg(f"HALLUC_STRIP removed [GENERATED_IMAGE:] raw_len={len(raw_full)} clean_len={len(full_text)}")
            _dbg(f"STREAM_DONE full_text_len={len(full_text)} buffered={buffering}")
            _dbg(f"FULL_TEXT>>>{full_text}<<<END")
            logger.info("[chat] initial stream done text=%s", _preview(full_text, 260))
            assistant_text, save_matches, search_matches, web_matches, img_matches, sched_matches = _strip_skills(full_text)
            assistant_text_full = assistant_text  # start with clean text; commands + continuations appended in order
            _dbg(f"INIT assistant_text_full len={len(assistant_text_full)} starts={assistant_text_full[:60]!r}")

            all_action_matches = sorted(search_matches + web_matches + img_matches, key=lambda m: m.start())
            has_actions = bool(all_action_matches)

            _dbg(f"PARSED saves={len(save_matches)} actions={len(all_action_matches)} imgs={len(img_matches)} clean_len={len(assistant_text)}")
            logger.info(
                "[chat] parsed skills saves=%d actions=%d imgs=%d clean=%s",
                len(save_matches),
                len(all_action_matches),
                len(img_matches),
                _preview(assistant_text, 220),
            )

            if not save_matches and not all_action_matches:
                _dbg("NO_SKILLS_DETECTED")
                logger.info("[chat] no skill commands detected in initial reply")

            # Flush buffered text when no action commands (normal message or save-only)
            if not has_actions and buffering:
                _dbg("BUFFER_FLUSH — no actions, flushing buffered save-only text")
                yield "event: rewrite\n"
                yield f"data: {json.dumps({'text': full_text})}\n\n"

            # When actions exist, rewrite to show only clean text before first command
            if has_actions:
                _dbg(f"REWRITE clean_text before actions len={len(assistant_text)}")
                yield "event: rewrite\n"
                yield f"data: {json.dumps({'text': assistant_text})}\n\n"

            # Extract trailing text after the last action command
            trailing_text = ""
            if all_action_matches:
                last_action_end = max(m.end() for m in all_action_matches)
                raw_tail = _strip_hallucinated_markers(full_text[last_action_end:])
                tail_clean, _, _, _, _, _ = _strip_skills(raw_tail)
                trailing_text = tail_clean.strip()
                if trailing_text:
                    _dbg(f"TRAILING_TEXT len={len(trailing_text)}: {trailing_text[:100]}")

            # ── Sequential agentic loop ──────────────────────────────────────
            MAX_AGENT_LOOPS = 5
            agent_loop = 0
            pending_actions: list[tuple[str, re.Match]] = []
            for m in all_action_matches:
                if "SEARCH_MEMORIES" in m.group(0):
                    kind = "search"
                elif "WEB_SEARCH" in m.group(0):
                    kind = "web"
                else:
                    kind = "image"
                pending_actions.append((kind, m))
            _dbg(f"AGENT_LOOP_CHECK pending={len(pending_actions)}")

            while agent_loop < MAX_AGENT_LOOPS and pending_actions:
                agent_loop += 1
                action_kind, action_match = pending_actions.pop(0)
                is_last_initial = not pending_actions
                cmd_text = action_match.group(0)
                _dbg(f"AGENT_LOOP #{agent_loop} kind={action_kind} cmd={cmd_text[:80]}")

                # For search/web, send the command as a text chunk so badges render.
                # For image, DON'T send the command as text — the shimmer is handled
                # via image_start SSE event on the frontend, and we don't want [GENERATE_IMAGE:]
                # in the streamed text (it would duplicate with [GENERATED_IMAGE:] from image_ready).
                if action_kind != "image":
                    for sse_line in _yield_chunk("\n" + cmd_text + "\n"):
                        yield sse_line

                if action_kind == "image":
                    logger.info("[chat] GENERATE_IMAGE #%d triggered: %s", agent_loop, cmd_text[:100])
                    _dbg(f"GENERATE_IMAGE #{agent_loop} cmd={cmd_text[:80]}")

                    yield "event: image_start\n"
                    yield f"data: {json.dumps({'prompt': action_match.group(1).strip()})}\n\n"

                    img_result = await _run_generate_image(action_match)

                    if img_result:
                        img_marker = f"[GENERATED_IMAGE: {img_result['path']} | {img_result['model']} | {img_result['prompt']}]"
                        assistant_text_full = assistant_text_full + "\n" + img_marker
                        _dbg(f"AFTER_IMG assistant_text_full len={len(assistant_text_full)}")
                        # image_ready SSE event makes the frontend append the marker —
                        # do NOT also yield it as a text chunk (would cause duplicate image)
                        yield "event: image_ready\n"
                        yield f"data: {json.dumps(img_result)}\n\n"
                    else:
                        error_note = (
                            "\n*(не удалось сгенерировать изображение)*"
                            if prompt_language == "ru" else
                            "\n*(image generation failed)*"
                        )
                        assistant_text_full = assistant_text_full + error_note
                        for sse_line in _yield_chunk(error_note):
                            yield sse_line

                    # Append trailing text that came after the image command
                    if is_last_initial and trailing_text:
                        _dbg(f"TRAILING_APPEND len={len(trailing_text)} text={trailing_text[:80]!r}")
                        assistant_text_full += "\n\n" + trailing_text
                        for sse_line in _yield_chunk("\n\n" + trailing_text):
                            yield sse_line
                    else:
                        _dbg(f"TRAILING_SKIP is_last_initial={is_last_initial} trailing_len={len(trailing_text)}")

                    # Images don't trigger a continuation LLM call — skip the streaming block
                    continue

                elif action_kind == "search":
                    search_query = action_match.group(1).strip()
                    logger.info("[chat] SEARCH_MEMORIES #%d triggered: %s", agent_loop, search_query[:100])

                    yield "event: search_start\n"
                    yield f"data: {json.dumps({'query': search_query})}\n\n"

                    found_pairs = await _run_search_memories(search_query)

                    yield "event: search_results\n"
                    yield f"data: {json.dumps({'query': search_query, 'results': found_pairs})}\n\n"

                    if prompt_language == "ru":
                        cont_hint = (
                            "Ты уже видел(а) результат поиска. "
                            "Если нужно — можешь повторить поиск другими словами "
                            f"(осталось попыток: {MAX_AGENT_LOOPS - agent_loop}).\n\n"
                            if agent_loop < MAX_AGENT_LOOPS else ""
                        )
                    else:
                        cont_hint = (
                            "You already saw the search result. "
                            "If needed — you may repeat the search with different words "
                            f"(attempts left: {MAX_AGENT_LOOPS - agent_loop}).\n\n"
                            if agent_loop < MAX_AGENT_LOOPS else ""
                        )

                    continuation_prompt = (
                        cont_hint + _build_continuation(found_pairs)
                        if found_pairs
                        else cont_hint + _build_empty_search_continuation(search_query)
                    )
                    continuation_web_search = False
                    logger.info("[chat] continuation #%d mode=search prompt=%s", agent_loop, _preview(continuation_prompt, 220))
                else:
                    web_query = action_match.group(1).strip()
                    _dbg(f"WEB_SEARCH #{agent_loop} query={web_query[:120]}")
                    logger.info("[chat] WEB_SEARCH #%d triggered: %s", agent_loop, web_query[:100])

                    yield "event: web_start\n"
                    yield f"data: {json.dumps({'query': web_query})}\n\n"

                    if prompt_language == "ru":
                        continuation_prompt = (
                            f"Найди в интернете актуальную информацию по запросу: {web_query}\n"
                            "Используй найденное в ответе естественно и коротко. Если данные противоречат друг другу, выбери наиболее вероятные и скажи мягко."
                        )
                    else:
                        continuation_prompt = (
                            f"Look up current information on the web for: {web_query}\n"
                            "Use what you find naturally in the reply and keep it concise. If sources conflict, use the most likely information and mention it gently."
                        )
                    continuation_web_search = True
                    logger.info("[chat] continuation #%d mode=web query=%s", agent_loop, _preview(web_query, 120))

                if is_last_initial and trailing_text:
                    hint_prefix = (
                        "\n\nТы уже начал(а) отвечать так (продолжай с этого места, не повторяй):\n"
                        if prompt_language == "ru" else
                        "\n\nYou already started replying like this (continue from here, don't repeat):\n"
                    )
                    continuation_prompt += hint_prefix + trailing_text

                continuation_messages = list(llm_messages)
                continuation_messages.append({"role": "assistant", "content": assistant_text})
                continuation_messages.append({"role": "user", "content": continuation_prompt})

                continuation_parts: list[str] = []
                separator = "\n\n"
                for sse_line in _yield_chunk(separator):
                    yield sse_line

                if action_kind == "web":
                    yield "event: web_done\n"
                    yield f"data: {json.dumps({'query': web_query})}\n\n"

                async for chunk in client.stream(
                    messages=continuation_messages,
                    web_search=continuation_web_search,
                    system_prompt=combined_system_prompt,
                ):
                    if not chunk:
                        continue
                    continuation_parts.append(chunk)
                    for sse_line in _yield_chunk(chunk):
                        yield sse_line

                continuation_text = "".join(continuation_parts).strip()
                _dbg(f"CONTINUATION #{agent_loop} done len={len(continuation_text)} text={_preview(continuation_text, 300)}")
                logger.info("[chat] continuation #%d done text=%s", agent_loop, _preview(continuation_text, 260))
                cont_clean, cont_saves, cont_searches, cont_web, cont_imgs, cont_scheds = _strip_skills(continuation_text)
                logger.info(
                    "[chat] continuation #%d parsed saves=%d searches=%d web=%d imgs=%d clean=%s",
                    agent_loop,
                    len(cont_saves),
                    len(cont_searches),
                    len(cont_web),
                    len(cont_imgs),
                    _preview(cont_clean, 220),
                )
                if cont_clean:
                    assistant_text = assistant_text + "\n\n" + cont_clean
                assistant_text_full = assistant_text_full + "\n" + cmd_text + "\n\n" + continuation_text
                save_matches = save_matches + cont_saves
                sched_matches = sched_matches + cont_scheds
                for cm in sorted(cont_searches + cont_web + cont_imgs, key=lambda m: m.start()):
                    if "SEARCH_MEMORIES" in cm.group(0):
                        new_kind = "search"
                    elif "WEB_SEARCH" in cm.group(0):
                        new_kind = "web"
                    else:
                        new_kind = "image"
                    pending_actions.append((new_kind, cm))

            # Append initial SAVE_MEMORY commands to full text for persistent rendering
            for sm in sorted(save_matches, key=lambda m: m.start()):
                if sm.group(0) not in assistant_text_full:
                    assistant_text_full += "\n" + sm.group(0)

            # ── [SAVE_MEMORY] — extract and store facts ──────────────────────
            save_memory_results = await _run_save_memory(assistant_text, save_matches)
            logger.info(
                "[chat] final assistant text=%s save_results=%d",
                _preview(assistant_text, 260),
                len(save_memory_results),
            )

            for sr in save_memory_results:
                if sr.get("dedup") == "skipped":
                    continue
                stars = sr.get("impressive", 0)
                marker = f"\n[SAVED_FACT: {sr['category']} | {stars} | {sr['fact']}]"
                assistant_text_full += marker
                for sse_line in _yield_chunk(marker):
                    yield sse_line

            # ── [SCHEDULE_MESSAGE] — create autonomy tasks ─────────────────
            if sched_matches:
                try:
                    from infrastructure.autonomy.task_queue import create_task, cancel_duplicate_scheduled
                    from infrastructure.database.models import TriggerType
                    for sm in sched_matches:
                        raw_arg = sm.group(1).strip()
                        if "|" not in raw_arg:
                            continue
                        ts_str, sched_msg = raw_arg.split("|", 1)
                        sched_msg = sched_msg.strip()
                        if not sched_msg:
                            continue
                        try:
                            local_dt = datetime.strptime(ts_str.strip(), "%Y-%m-%d %H:%M")
                            from datetime import timezone as _tz
                            scheduled_at = local_dt.astimezone(_tz.utc)
                            await cancel_duplicate_scheduled(db, account_id or "default", scheduled_at, "chat")
                            payload = json.dumps({"message": sched_msg, "source": "chat"})
                            await create_task(
                                db,
                                account_id=account_id or "default",
                                trigger_type=TriggerType.TIME,
                                payload=payload,
                                scheduled_at=scheduled_at,
                            )
                            logger.info("[chat] SCHEDULE_MESSAGE created task at %s: %s", ts_str.strip(), sched_msg[:60])
                        except ValueError:
                            logger.warning("[chat] SCHEDULE_MESSAGE bad timestamp: %r", ts_str)
                except Exception as _sched_exc:
                    logger.warning("[chat] SCHEDULE_MESSAGE processing failed: %s", _sched_exc)

            # Final cleanup: strip raw skill commands that should NOT be persisted.
            # Only [GENERATED_IMAGE:], [SAVED_FACT:], and plain text should remain.
            _RAW_CMD_RE = re.compile(
                r"\[(?:GENERATE_IMAGE|SEARCH_MEMORIES|WEB_SEARCH|SAVE_MEMORY|SCHEDULE_MESSAGE):\s*.*?\]",
                re.DOTALL | re.IGNORECASE,
            )
            cleaned = _RAW_CMD_RE.sub("", assistant_text_full)
            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
            if cleaned != assistant_text_full:
                _dbg(f"FINAL_CLEAN stripped raw cmds old_len={len(assistant_text_full)} new_len={len(cleaned)}")
                assistant_text_full = cleaned
            _dbg(f"SAVE_TO_DB assistant_text_full len={len(assistant_text_full)}")
            _dbg(f"SAVE_CONTENT>>>{assistant_text_full}<<<END")

            if assistant_text_full and not assistant_text_full.startswith("[OpenRouter error"):
                assistant_created_at = now_utc()
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

            # Update Chroma usage for retrieved facts
            if chroma_fact_ids:
                try:
                    _chroma_pipeline = get_chroma_pipeline()
                    for fid in chroma_fact_ids:
                        await asyncio.get_event_loop().run_in_executor(
                            None, lambda fid=fid: _chroma_pipeline.update_usage(fid)
                        )
                except Exception as exc:
                    logger.warning("[chat] Chroma update_usage failed: %s", exc)

            chroma_for_ui: list[dict] = []
            if current_user_text.strip():
                try:
                    _pipe = get_chroma_pipeline()
                    _facts = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: _pipe.query_similar_multi(
                            account_id=account_id or "default",
                            message=current_user_text,
                            top_k=5,
                            days_cutoff=cutoff_days,
                        )
                    )
                    for f in (_facts or []):
                        meta = f.get("metadata") or {}
                        ts = meta.get("last_used") or meta.get("created_at")
                        chroma_for_ui.append({
                            "id": f.get("id", ""),
                            "text": f.get("text", ""),
                            "category": meta.get("category", ""),
                            "impressive": meta.get("impressive", 0),
                            "time_label": humanize_timestamp(
                                datetime.fromisoformat(ts) if ts else None,
                                prompt_language,
                            ),
                        })
                except Exception:
                    pass

            yield "event: memory\n"
            yield f"data: {json.dumps({'chroma_facts': chroma_for_ui})}\n\n"

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
