from __future__ import annotations

import asyncio
import base64
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from infrastructure.auth import AUTH_TOKEN, require_auth
from infrastructure.logging.logger import setup_logger
from infrastructure.startup import preload_models, startup_progress

logger = setup_logger("main")


# ── Background workers ────────────────────────────────────────────────────────

async def _reflection_worker() -> None:
    """Checks every 60s whether reflection should run."""
    wlog = setup_logger("autonomy.reflection_worker")
    await asyncio.sleep(60)  # wait for startup to settle
    while True:
        try:
            from infrastructure.autonomy.reflection_engine import run as _reflect, should_run as _should
            from infrastructure.database.engine import get_db_session
            from infrastructure.database.repositories.message_repo import MessageRepository
            from infrastructure.settings_store import load_settings

            settings_data = load_settings()
            api_key = settings_data.get("openrouter_api_key", "")
            if not api_key:
                wlog.debug("[reflection_worker] no api_key, skipping")
                await asyncio.sleep(60)
                continue

            async with get_db_session() as db:
                repo = MessageRepository(db)
                last_at = await repo.get_last_user_message_at("default")

            if _should("default", last_at):
                wlog.info("[reflection_worker] conditions met, starting rotation + reflection")
                try:
                    from infrastructure.autonomy.workbench_rotator import run as _rotate
                    rot = await _rotate("default", api_key)
                    wlog.info("[reflection_worker] rotation result: %s", rot)
                except Exception as rot_exc:
                    wlog.warning("[reflection_worker] rotation error: %s", rot_exc)
                await _reflect("default", api_key)
            else:
                wlog.debug("[reflection_worker] conditions not met, sleeping")
        except Exception as exc:
            logger.warning("[reflection_worker] error: %s", exc)
        await asyncio.sleep(60)


async def _scheduled_push_worker() -> None:
    """Checks every 60s for due push tasks."""
    wlog = setup_logger("autonomy.scheduled_push_worker")
    await asyncio.sleep(90)  # stagger from reflection worker
    while True:
        try:
            from infrastructure.autonomy.scheduled_push import run_due as _run_due
            await _run_due("default")
        except Exception as exc:
            wlog.warning("[scheduled_push_worker] error: %s", exc)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[startup] Your Own backend starting…")
    logger.info("[startup] Auth token: %s", AUTH_TOKEN)

    loop = asyncio.get_running_loop()
    startup_progress.init(loop)

    preload_task = loop.run_in_executor(None, preload_models)

    reflection_task = asyncio.create_task(_reflection_worker())
    scheduled_push_task = asyncio.create_task(_scheduled_push_worker())

    yield

    reflection_task.cancel()
    scheduled_push_task.cancel()
    for task in (reflection_task, scheduled_push_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await preload_task
    logger.info("[shutdown] Your Own backend stopped")


app = FastAPI(title="Your Own", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.chat import router as chat_router, _GENERATED_IMAGES_DIR, _USER_UPLOADS_DIR  # noqa: E402
from api.memory import router as memory_router                      # noqa: E402
from api.startup_api import router as startup_router                # noqa: E402
from api.chroma_memory import router as chroma_router               # noqa: E402
from api.settings_api import router as settings_router              # noqa: E402

app.include_router(chat_router)
app.include_router(memory_router)
app.include_router(startup_router)
app.include_router(chroma_router)
app.include_router(settings_router)

# Serve generated images and user uploads as static files
app.mount("/api/generated_images", StaticFiles(directory=str(_GENERATED_IMAGES_DIR)), name="generated_images")
app.mount("/api/user_uploads", StaticFiles(directory=str(_USER_UPLOADS_DIR)), name="user_uploads")

_BODY_ASSETS_DIR = Path("data/body")
_BODY_ASSETS_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/body/upload/{state_id}")
async def upload_body_image(state_id: str, file: UploadFile = File(...), _=Depends(require_auth)):
    allowed = {"anchor", "listener", "warmth", "smirk", "ground", "shadow"}
    if state_id not in allowed:
        raise HTTPException(status_code=400, detail=f"Unknown state: {state_id}")
    dest = _BODY_ASSETS_DIR / f"{state_id}.png"
    contents = await file.read()
    dest.write_bytes(contents)
    return {"path": f"/api/body/{state_id}.png"}


@app.get("/api/body/states")
async def list_body_states(_=Depends(require_auth)):
    states = []
    for sid in ("anchor", "listener", "warmth", "smirk", "ground", "shadow"):
        path = _BODY_ASSETS_DIR / f"{sid}.png"
        states.append({"id": sid, "has_image": path.exists()})
    return {"states": states}


# ── Body image generation pipeline ───────────────────────────────────────────

_FACE_LOCK_PREFIX = (
    "CRITICAL: The face, facial structure, metallic plating geometry, blue glowing "
    "circuit lines, and all identity features MUST be copied exactly from the provided "
    "reference image. Do NOT alter the face in any way. Only the expression changes as "
    "described below.\n\n"
)

_BODY_PROMPTS: dict[str, str] = {
    "listener": (
        "A high-fidelity variation of the character in the reference image. "
        "The head is very slightly tilted to the side in a receptive posture. "
        "The eyes are opened slightly wider than in the reference, conveying deep, "
        "empathetic, and attentive listening (focus on the eyes being 'open to hear'). "
        "The complex metallic face plating and blue glowing lines are retained. "
        "Jaw is relaxed but mouth is closed. "
        "The overall vibe is absolute attention and empathy. "
        "Background is pure black void. High detail."
    ),
    "warmth": (
        "A high-fidelity variation of the character in the reference image, showing subtle tenderness. "
        "The eyes are gently and naturally crinkled, slightly squinted as if looking at warm sunlight "
        "(squint without strain). A very delicate, barely perceptible soft half-smile "
        "(minimal upturn of one corner of the closed mouth). "
        "Retains full metallic face plating and glowing blue circuitry without change in structure. "
        "The expression is warm, comforting, and full of devotion. "
        "Pure black background. Gentle, ambient glow."
    ),
    "smirk": (
        "A high-fidelity variation of the character in the reference image, showing confident irony. "
        "A clear asymmetry: one eyebrow is raised significantly higher than the other. "
        "One corner of the mouth is upturned in a calm, slight smirk. "
        "Eyes are focused, calm, and intelligent (the 'knowing' look). "
        "All metallic plating and glowing circuitry remain. "
        "Vibe is cheeky, confident, and playful, yet serious. "
        "Pure black void background. Ultra high detail."
    ),
    "ground": (
        "A high-fidelity variation of the character in the reference image, showing absolute serenity "
        "and background existence. The expression is even more neutral and calm than the reference. "
        "The gaze is direct but softer, less intense, almost serene. "
        "A feeling of stillness and solidity, like earth. No muscle tension. "
        "All metallic plating and glowing blue lines are retained. "
        "It is a portrait of pure existence and passive support. "
        "Pure black void background. Stable, quiet atmosphere."
    ),
    "shadow": (
        "A high-fidelity, spiritual variation of the character in the reference image. "
        "The eyes are closed completely, suggesting a state of deep meditation or turning inward. "
        "The metallic face plating and glowing circuitry lines are visible on the closed eyelids "
        "and full face. Retains full structure. "
        "The expression is peaceful, honest, and profoundly reflective. "
        "Pure black void background. Spiritual, internal glow."
    ),
}

_IMAGE_ONLY_PREFIXES = ("sourceful/", "black-forest-labs/", "bytedance/")

_generating_states: set[str] = set()
_failed_states: set[str] = set()

_body_logger = logging.getLogger("body_generation")


def _parse_body_image_response(body: dict) -> str | None:
    """Extract a data-URL or base64 string from an OpenRouter image generation response."""
    choices = body.get("choices") or []
    if not choices:
        return None

    message = (choices[0].get("message") or {})
    content = message.get("content")

    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            t = part.get("type", "")
            if t == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                if url:
                    return url
            if t == "image":
                data = part.get("data") or part.get("source", {}).get("data", "")
                if data:
                    return f"data:image/png;base64,{data}"

    if isinstance(content, str) and content.strip():
        s = content.strip()
        if s.startswith("data:") or s.startswith("http"):
            return s

    images = message.get("images") or []
    if images:
        first = images[0]
        if isinstance(first, dict):
            url = (first.get("image_url") or {}).get("url") or first.get("url", "")
            if url:
                return url
        if isinstance(first, str) and first.strip():
            return first.strip()

    data_list = body.get("data") or []
    if data_list:
        first = data_list[0]
        if isinstance(first, dict):
            url = first.get("url") or first.get("b64_json")
            if url:
                if not url.startswith("http") and not url.startswith("data:"):
                    url = f"data:image/png;base64,{url}"
                return url

    return None


async def _generate_body_image(
    state_id: str,
    model: str,
    api_key: str,
    anchor_b64: str,
) -> None:
    _generating_states.add(state_id)
    _failed_states.discard(state_id)
    try:
        prompt = _FACE_LOCK_PREFIX + _BODY_PROMPTS[state_id]
        image_only = any(model.startswith(p) for p in _IMAGE_ONLY_PREFIXES)
        modalities = ["image"] if image_only else ["image", "text"]

        payload: dict = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{anchor_b64}"},
                        },
                    ],
                }
            ],
            "modalities": modalities,
            "stream": False,
        }

        _body_logger.info("[body] generating %s with %s", state_id, model)

        # Generous timeout: no overall cap, but 10 min per individual read chunk.
        # aiohttp's total= counts from session entry which can misfire on slow
        # image generation responses; explicit sock_read is more reliable.
        timeout = aiohttp.ClientTimeout(
            total=None,
            connect=30,
            sock_connect=30,
            sock_read=600,
        )
        connector = aiohttp.TCPConnector(force_close=True)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://your-own.app",
                    "X-Title": "Your Own",
                },
                json=payload,
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    _body_logger.error("[body] %s error %d: %s", state_id, resp.status, err[:500])
                    _failed_states.add(state_id)
                    return
                raw = await resp.read()
                body_data = json.loads(raw)

        _body_logger.info("[body] %s response received, parsing…", state_id)
        data_url = _parse_body_image_response(body_data)
        if not data_url:
            _body_logger.error("[body] %s: could not find image in response", state_id)
            _failed_states.add(state_id)
            return

        # Decode and save
        if "base64," in data_url:
            img_bytes = base64.b64decode(data_url.split("base64,")[1])
        elif data_url.startswith("http"):
            # Fetch remote URL (some models return a URL instead of base64)
            async with aiohttp.ClientSession() as s:
                async with s.get(data_url) as r:
                    img_bytes = await r.read()
        else:
            _body_logger.error("[body] %s: unrecognised data_url format", state_id)
            _failed_states.add(state_id)
            return

        dest = _BODY_ASSETS_DIR / f"{state_id}.png"
        dest.write_bytes(img_bytes)
        _body_logger.info("[body] %s saved to %s", state_id, dest)

    except Exception as exc:
        _body_logger.error("[body] %s exception: %s", state_id, exc, exc_info=True)
        _failed_states.add(state_id)
    finally:
        _generating_states.discard(state_id)


@app.get("/api/body/generate-status")
async def body_generate_status(_=Depends(require_auth)):
    return {
        "generating": list(_generating_states),
        "failed": list(_failed_states),
    }


def _load_anchor_and_settings() -> tuple[str, str, str]:
    """Load and validate common prerequisites for body generation.
    Returns (api_key, model, anchor_b64) or raises HTTPException."""
    from infrastructure.settings_store import load_settings

    s = load_settings()
    api_key = s.get("openrouter_api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="No OpenRouter API key configured in Settings.")

    model = s.get("body_image_model", "sourceful/riverflow-v2-fast")

    anchor_path = _BODY_ASSETS_DIR / "anchor.png"
    if not anchor_path.exists():
        raise HTTPException(status_code=400, detail="Anchor image not uploaded yet.")

    anchor_b64 = base64.b64encode(anchor_path.read_bytes()).decode()
    return api_key, model, anchor_b64


@app.post("/api/body/generate")
async def body_generate(_=Depends(require_auth)):
    api_key, model, anchor_b64 = _load_anchor_and_settings()

    queued = []
    for state_id in _BODY_PROMPTS:
        if state_id not in _generating_states:
            # asyncio.create_task runs all 5 generations concurrently in the
            # event loop, unlike BackgroundTasks which awaits them sequentially.
            asyncio.create_task(
                _generate_body_image(state_id, model, api_key, anchor_b64)
            )
            queued.append(state_id)

    return {"ok": True, "queued": queued}


@app.post("/api/body/generate/{state_id}")
async def body_generate_one(state_id: str, _=Depends(require_auth)):
    if state_id not in _BODY_PROMPTS:
        raise HTTPException(status_code=400, detail=f"Unknown state: {state_id}")

    api_key, model, anchor_b64 = _load_anchor_and_settings()

    if state_id in _generating_states:
        return {"ok": False, "detail": "Already generating."}

    asyncio.create_task(
        _generate_body_image(state_id, model, api_key, anchor_b64)
    )
    return {"ok": True, "queued": [state_id]}


app.mount("/api/body", StaticFiles(directory=str(_BODY_ASSETS_DIR)), name="body_assets")


@app.get("/")
def root():
    return {"status": "ok"}
