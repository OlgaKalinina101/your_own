from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from infrastructure.logging.logger import setup_logger
from infrastructure.startup import preload_models, startup_progress

logger = setup_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[startup] Your Own backend starting…")

    loop = asyncio.get_running_loop()
    # Give the progress tracker a reference to the running loop BEFORE
    # spawning the thread — so call_soon_threadsafe works correctly.
    startup_progress.init(loop)

    preload_task = loop.run_in_executor(None, preload_models)

    yield  # server accepts requests immediately; models load in background

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

from api.chat import router as chat_router                  # noqa: E402
from api.memory import router as memory_router              # noqa: E402
from api.startup_api import router as startup_router        # noqa: E402
from api.chroma_memory import router as chroma_router       # noqa: E402

app.include_router(chat_router)
app.include_router(memory_router)
app.include_router(startup_router)
app.include_router(chroma_router)


@app.get("/")
def root():
    return {"status": "ok"}
