"""
GET /api/startup/status  — SSE stream of model preload progress.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from infrastructure.startup import startup_progress

router = APIRouter(prefix="/api/startup", tags=["startup"])


@router.get("/status")
async def startup_status():
    async def event_stream():
        sent = 0  # number of events already sent to this client

        while True:
            # Snapshot current events under lock
            with startup_progress._lock:
                all_events = list(startup_progress.events)
                is_done = startup_progress.done

            # Send anything new
            for ev in all_events[sent:]:
                yield f"data: {json.dumps(ev)}\n\n"
            sent = len(all_events)

            if is_done and sent >= len(all_events):
                break

            # Wait for more events or timeout (keep-alive)
            try:
                await asyncio.wait_for(
                    startup_progress.wait_next(sent), timeout=15.0
                )
            except asyncio.TimeoutError:
                yield ": ping\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
