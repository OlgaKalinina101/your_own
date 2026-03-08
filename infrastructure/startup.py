"""
Startup preload sequence.

Runs synchronously inside run_in_executor so it doesn't block the event loop.
Reports progress via a thread-safe list + asyncio.Event so the SSE endpoint
can poll without depending on a shared Queue across threads.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Callable

from infrastructure.logging.logger import setup_logger

logger = setup_logger("startup")


class StartupProgress:
    """
    Thread-safe progress tracker.

    The preload thread appends to `events` and sets `_event`.
    The SSE coroutine waits on `_event` and drains `events`.
    No asyncio primitives are created at import time — only when
    `init(loop)` is called from the running event loop.
    """

    def __init__(self) -> None:
        self.events: list[dict] = []   # all events ever emitted (replay buffer)
        self.done: bool = False
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event: asyncio.Event | None = None

    def init(self, loop: asyncio.AbstractEventLoop) -> None:
        """Call once from the running event loop before starting the executor."""
        self._loop = loop
        self._event = asyncio.Event()

    def put(self, event: dict) -> None:
        """Thread-safe: append event and wake up any waiting SSE coroutine."""
        with self._lock:
            self.events.append(event)
        if self._loop and self._event:
            self._loop.call_soon_threadsafe(self._event.set)

    async def wait_next(self, after_index: int) -> int:
        """
        Wait until there are more events past `after_index`.
        Returns the new index.
        """
        while True:
            with self._lock:
                if len(self.events) > after_index:
                    return len(self.events)
            if self._event:
                self._event.clear()
                # Re-check immediately after clearing to avoid race
                with self._lock:
                    if len(self.events) > after_index:
                        return len(self.events)
                await self._event.wait()
            else:
                await asyncio.sleep(0.2)


# Global singleton
startup_progress = StartupProgress()


def preload_models() -> None:
    """
    Heavy initialisation — runs in a thread pool executor.
    Each step emits a progress event readable by the SSE endpoint.
    """
    def step(name: str, fn: Callable) -> None:
        startup_progress.put({"step": name, "status": "running"})
        logger.info("[startup] %s …", name)
        try:
            fn()
            startup_progress.put({"step": name, "status": "ok"})
            logger.info("[startup] %s done", name)
        except Exception as exc:
            logger.exception("[startup] %s failed: %s", name, exc)
            startup_progress.put({"step": name, "status": "error", "detail": str(exc)})

    def load_embedding():
        from infrastructure.memory.embedder import MODEL_NAME, _load_model
        _load_model()

    from infrastructure.memory.embedder import MODEL_NAME
    step(f"Loading embedding model ({MODEL_NAME})", load_embedding)

    def load_morph_ru():
        from infrastructure.memory.focus_point import _get_morph_ru
        _get_morph_ru()

    step("Loading Russian lemmatiser (pymorphy3)", load_morph_ru)

    def load_ruwordnet():
        from infrastructure.memory.focus_point import _get_ruwordnet
        _get_ruwordnet()

    step("Loading RuWordNet", load_ruwordnet)

    def load_nltk():
        from infrastructure.memory.focus_point import _get_lemmatizer_en
        _get_lemmatizer_en()

    step("Loading English lemmatiser (NLTK WordNet)", load_nltk)

    startup_progress.done = True
    startup_progress.put({"step": "ready", "status": "ok", "done": True})
    logger.info("[startup] All models loaded — server ready")
