"""The chat SSE contract, pinned on the server's side.

`contracts/chat-sse.json` is read by the desktop's parser test and is meant to
be read by the phone's. This file makes it binding on the third party too, so
the fixtures describe what the backend actually emits rather than what someone
remembered it emitting.

The load-bearing assertion is `test_text_chunks_carry_no_event_line`. Both
clients decide "is this reply text or an event I do not know?" purely by whether
an `event:` line is present. If a text chunk ever grew one, or a named event
lost one, every unrecognised frame would start appearing inside the reply.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest

from api.chat import _sse, _sse_error, _yield_chunk, _SSE_DONE, _SSE_KEEPALIVE

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts" / "chat-sse.json").read_text("utf-8")
)

# Every event name reachable from api/chat.py or a skill's sse_events.
# Adding one here without a fixture fails `test_every_emitted_event_has_a_fixture`,
# and adding the fixture then fails both clients until they handle it. That
# chain is the only thing keeping two clients with no shared code in step.
EMITTED_EVENTS = {
    "pair_id",
    "image_urls",
    "rewrite",
    "memory",
    "error",
    "image_start",
    "image_ready",
    "image_cancel",
    "search_start",
    "search_results",
    "web_start",
    "web_results",
    "web_done",
}


def _fixture_event_names() -> set[str]:
    """Names of real events pinned by fixtures.

    Frames flagged ``synthetic`` are excluded: they pin client behaviour for
    things the backend does not send — ``skill``, which both clients have
    filtered from the start although nothing ever emitted it, and a deliberately
    invented name standing in for "an event from a newer server".
    """
    names = set()
    for frame in CONTRACT["frames"]:
        if frame.get("synthetic"):
            continue
        match = re.match(r"^event: (.+)$", frame["raw"], re.MULTILINE)
        if match:
            names.add(match.group(1))
    return names


class TestTheInvariantClientsRelyOn:
    def test_text_chunks_carry_no_event_line(self):
        """The whole 'unknown event' rule rests on this.

        Per *line*, not per frame. A chunk whose own text begins with "event: "
        is still safe, because the line the client sees begins with "data: ".
        That distinction is exactly why clients match on a line's prefix rather
        than searching the frame — and why this assertion has to as well.
        """
        for chunk in ["hello", "multi\nline", "[DONE] as text", "event: not really"]:
            frame = "".join(_yield_chunk(chunk))
            offending = [ln for ln in frame.split("\n") if ln.startswith("event: ")]
            assert not offending, f"a text chunk grew an event line: {offending}"

    def test_a_multi_line_chunk_becomes_several_data_lines(self):
        frame = "".join(_yield_chunk("a\nb"))
        assert frame == "data: a\ndata: b\n\n"

    def test_named_events_always_carry_an_event_line(self):
        frame = _sse("rewrite", {"text": "x"})
        assert frame.startswith("event: rewrite\n")
        assert frame.endswith("\n\n")

    def test_keepalive_has_no_data_line(self):
        """A comment, not an event — clients must drop it before parsing."""
        assert "data:" not in _SSE_KEEPALIVE
        assert _SSE_KEEPALIVE.startswith(":")

    def test_the_terminator_is_untyped(self):
        assert _SSE_DONE == "data: [DONE]\n\n"
        assert "event:" not in _SSE_DONE


class TestFramesMatchTheFixtures:
    @pytest.mark.parametrize(
        "event,payload",
        [
            ("pair_id", {"pair_id": "3f2b"}),
            ("rewrite", {"text": "final answer"}),
            ("image_start", {"prompt": "a cat"}),
            ("image_cancel", {}),
        ],
    )
    def test_frame_round_trips(self, event, payload):
        frame = _sse(event, payload)
        name, _, data = frame.partition("\n")
        assert name == f"event: {event}"
        assert json.loads(data[len("data: ") :].strip()) == payload

    def test_error_frame_shape(self):
        frame = _sse_error("upstream refused", uuid.UUID(int=1))
        assert frame.startswith("event: error\n")
        payload = json.loads(frame.split("\n")[1][len("data: ") :])
        assert payload["message"] == "upstream refused"
        assert "pair_id" in payload

    def test_every_emitted_event_has_a_fixture(self):
        missing = EMITTED_EVENTS - _fixture_event_names()
        assert not missing, (
            "these events are emitted but no fixture pins them, so a client may "
            f"be dropping them silently: {sorted(missing)}"
        )

    def test_no_fixture_describes_an_event_nobody_sends(self):
        stale = _fixture_event_names() - EMITTED_EVENTS
        assert not stale, f"fixtures for events the backend no longer sends: {sorted(stale)}"
