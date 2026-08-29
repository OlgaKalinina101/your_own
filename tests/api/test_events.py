"""The change channel: a client that is merely open must learn what happened.

The complaint that produced this: chat on the phone, walk to the desktop, and
the conversation is not there — with nothing in the interface able to fetch it.
Three writers share one conversation (this client, the other client, and the
assistant through `send_push_and_save`) and a client only ever heard about its
own.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from infrastructure.events import EventHub, QUEUE_LIMIT


def _app():
    import main

    return main.app


async def _next(subscription, timeout: float = 1.0):
    return await asyncio.wait_for(subscription.get(), timeout=timeout)


class TestHub:
    @pytest.mark.asyncio
    async def test_an_event_published_before_the_first_read_is_not_lost(self):
        """The race that made subscribe() eager.

        A lazy async generator registers nothing until it is first iterated, so
        an event published in the gap vanished — no error, just a message that
        never arrived.
        """
        hub = EventHub()
        subscription = hub.subscribe()
        hub.publish("pairs_changed", {"origin": "chat"})
        assert await _next(subscription) == ("pairs_changed", {"origin": "chat"})
        subscription.close()

    @pytest.mark.asyncio
    async def test_every_subscriber_receives_it(self):
        """Two open clients is the entire point."""
        hub = EventHub()
        a, b = hub.subscribe(), hub.subscribe()
        assert hub.subscriber_count == 2
        hub.publish("pairs_changed", {"origin": "assistant"})
        assert (await _next(a))[0] == "pairs_changed"
        assert (await _next(b))[0] == "pairs_changed"
        a.close()
        b.close()

    @pytest.mark.asyncio
    async def test_publishing_with_nobody_listening_is_fine(self):
        """Called from a background worker; it must never be able to raise."""
        EventHub().publish("pairs_changed", {"origin": "assistant"})

    @pytest.mark.asyncio
    async def test_a_client_that_stops_reading_cannot_grow_memory(self):
        hub = EventHub()
        subscription = hub.subscribe()

        for i in range(QUEUE_LIMIT * 3):
            hub.publish("pairs_changed", {"n": i})

        queue = next(iter(hub._subscribers))
        assert queue.qsize() <= QUEUE_LIMIT
        # The newest survived: for a hint-shaped event the latest one makes the
        # older ones redundant, so dropping the oldest is the right end to lose.
        drained = [queue.get_nowait()[1]["n"] for _ in range(queue.qsize())]
        assert drained[-1] == QUEUE_LIMIT * 3 - 1
        subscription.close()

    @pytest.mark.asyncio
    async def test_hanging_up_unsubscribes(self):
        """The normal way one of these streams ends is the client going away."""
        hub = EventHub()
        subscription = hub.subscribe()
        assert hub.subscriber_count == 1
        subscription.close()
        assert hub.subscriber_count == 0
        # A publish after the hang-up must not raise on the way to nobody.
        hub.publish("pairs_changed", {})


class TestEndpoint:
    @pytest.mark.asyncio
    async def test_it_needs_a_token(self):
        transport = httpx.ASGITransport(app=_app(), client=("127.0.0.1", 5555))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get("/api/events")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_a_published_event_reaches_a_connected_client(self):
        """Driven through the response object, not over HTTP.

        httpx's ASGITransport buffers a response body to completion before
        handing it back, which is fine for the chat stream (it ends) and hangs
        forever on this one (it does not). The existing SSE test gets away with
        `response.text` for exactly that reason.
        """
        from api.events_api import events
        from infrastructure.events import publish_pairs_changed

        response = await events(_token="ignored")
        frames = response.body_iterator

        publish_pairs_changed(account_id="default", origin="assistant")
        frame = await asyncio.wait_for(frames.__anext__(), timeout=5)

        assert frame.startswith("event: pairs_changed\n")
        assert '"origin": "assistant"' in frame
        assert frame.endswith("\n\n")
        await frames.aclose()

    @pytest.mark.asyncio
    async def test_the_stream_says_it_must_not_be_buffered(self):
        from api.events_api import events

        response = await events(_token="ignored")
        assert response.media_type == "text/event-stream"
        assert response.headers.get("cache-control") == "no-cache"
        # Without this a proxy holds the whole stream until it ends, and this
        # one never ends.
        assert response.headers.get("x-accel-buffering") == "no"
        await response.body_iterator.aclose()

    @pytest.mark.asyncio
    async def test_hanging_up_releases_the_subscription(self):
        from api.events_api import events
        from infrastructure.events import hub

        before = hub.subscriber_count
        response = await events(_token="ignored")
        assert hub.subscriber_count == before + 1
        await response.body_iterator.aclose()
        assert hub.subscriber_count == before
