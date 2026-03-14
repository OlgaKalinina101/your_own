from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from infrastructure.auth import AUTH_TOKEN
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

from api.chat import router as chat_router, _GENERATED_IMAGES_DIR  # noqa: E402
from api.memory import router as memory_router                      # noqa: E402
from api.startup_api import router as startup_router                # noqa: E402
from api.chroma_memory import router as chroma_router               # noqa: E402
from api.settings_api import router as settings_router              # noqa: E402

app.include_router(chat_router)
app.include_router(memory_router)
app.include_router(startup_router)
app.include_router(chroma_router)
app.include_router(settings_router)

# Serve generated images as static files
app.mount("/api/generated_images", StaticFiles(directory=str(_GENERATED_IMAGES_DIR)), name="generated_images")


@app.get("/")
def root():
    return {"status": "ok"}
