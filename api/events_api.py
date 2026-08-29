"""
GET /api/events — SSE stream telling a client the conversation changed.

Shaped after ``api/startup_api.py``, with the same two things that make an SSE
endpoint survive contact with reality: a keepalive comment so an idle stream is
not mistaken for a dead one by anything in the middle, and no middleware between
the generator and the socket (``BaseHTTPMiddleware`` pumps the body through an
intermediate task and breaks disconnect handling — measured, see the note in
``infrastructure/auth.py``).
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from infrastructure.auth import require_auth
from infrastructure.events import hub

router = APIRouter(prefix="/api/events", tags=["events"])

# Long enough to stay cheap, short enough that an idle proxy does not decide the
# connection is finished. The chat stream uses the same comment form.
KEEPALIVE_SECONDS = 20.0


class _EventStream:
    """The subscription as an async iterable, with unconditional cleanup.

    Written out rather than left as an async generator because a generator that
    is closed before it is ever iterated does not run its ``finally`` — its body
    never started. A client that connects and hangs up before the first frame is
    ordinary, and every one of those used to leave a queue subscribed for the
    life of the process.
    """

    def __init__(self, subscription) -> None:
        self._subscription = subscription
        self._closed = False

    def __aiter__(self) -> "_EventStream":
        return self

    async def __anext__(self) -> str:
        if self._closed:
            raise StopAsyncIteration
        while True:
            try:
                name, payload = await asyncio.wait_for(
                    self._subscription.get(), timeout=KEEPALIVE_SECONDS
                )
            except asyncio.TimeoutError:
                # A comment, not an event: clients drop any frame with no
                # `data:` line before parsing it.
                return ": keepalive\n\n"
            except asyncio.CancelledError:
                self.close()
                raise
            return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._subscription.close()

    async def aclose(self) -> None:
        self.close()


@router.get("")
async def events(_token: str = Depends(require_auth)):
    # Subscribed here rather than inside a generator: a generator's body does
    # not run until it is first iterated, and anything published in the gap
    # between the client connecting and that first await would be lost.
    subscription = hub.subscribe()

    return StreamingResponse(
        _EventStream(subscription),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
