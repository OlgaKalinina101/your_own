"""In-process fan-out, so a client can be told the conversation changed.

Why this exists
---------------
The conversation has three writers, not two: the desktop, the phone, and the
assistant itself — ``send_push_and_save`` persists a message and notifies Pushy,
which reaches the phone and nothing else. A client that is merely *open* learned
about none of it. The desktop went further and cached the first page of history
for the life of the process, so switching devices meant restarting the app.

Fetching on focus fixes switching devices. It does not fix a message arriving
while you are looking at the window, which is exactly what an assistant that
writes on its own schedule does. That needs the server to say something.

Why in-process is enough
------------------------
``SingleProcessLock`` (see ``infrastructure/single_process.py``) makes "exactly
one process" an enforced invariant rather than a hope — the same invariant the
file-backed state already depends on. A second process would fail to start, so
there is no second process for a subscriber to be attached to. If that ever
changes, this module is the thing to replace, and its whole surface is two
methods.

What the events carry
---------------------
A hint, not the payload: "something changed, at this time". The client then asks
for it through the ordinary history endpoint. One fetch path serves both the
client that got the hint and the client that was closed when it was sent, so
there is no second way for history to arrive and no chance of the two
disagreeing.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("events")

# Per-subscriber backlog. A client that stops reading must not grow the server's
# memory, and for hint-shaped events the newest one makes the older ones
# redundant anyway — so an overflowing queue drops the oldest and keeps going.
QUEUE_LIMIT = 32


class EventHub:
    """Broadcast to every current subscriber. Slow subscribers lose old events."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[tuple[str, dict]]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: str, payload: dict) -> None:
        """Hand an event to every subscriber. Never raises, never blocks.

        Called from request handlers and background workers alike; a failure to
        notify must not be able to fail the thing that was being notified about.
        """
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()  # drop the oldest
                except asyncio.QueueEmpty:  # pragma: no cover - racing with a reader
                    pass
            try:
                queue.put_nowait((event, payload))
            except asyncio.QueueFull:  # pragma: no cover - racing with a reader
                logger.warning("[events] dropped %s for a stalled subscriber", event)

    def subscribe(self) -> "Subscription":
        """Start receiving events, from this call onwards.

        Deliberately not an async generator. A generator's body does not run
        until the first ``__anext__``, so anything published between the client
        connecting and the stream first awaiting would be dropped — a race with
        a very small window and no symptom except a message that never arrives.
        Registering here makes "subscribed" mean the same thing as "will be
        told".
        """
        queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self._subscribers.add(queue)
        return Subscription(self, queue)

    def _unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)


class Subscription:
    """One client's view of the hub. Close it, or it stays subscribed."""

    def __init__(self, hub: "EventHub", queue: asyncio.Queue[tuple[str, dict]]) -> None:
        self._hub = hub
        self._queue = queue

    async def get(self) -> tuple[str, dict]:
        return await self._queue.get()

    def close(self) -> None:
        self._hub._unsubscribe(self._queue)

    def __enter__(self) -> "Subscription":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


hub = EventHub()


# ── The one event this app publishes so far ──────────────────────────────────

EVENT_PAIRS_CHANGED = "pairs_changed"


def publish_pairs_changed(*, account_id: str, origin: str) -> None:
    """Say that the stored conversation now differs from what a client holds.

    ``origin`` is for the log and for a client that wants to ignore its own
    echo; it is not a filter here, because a client must be able to reconcile
    its own optimistic copy against what was actually stored.
    """
    hub.publish(EVENT_PAIRS_CHANGED, {"account_id": account_id, "origin": origin})
