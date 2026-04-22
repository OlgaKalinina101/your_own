"""Shared command parser for post_analyzer and reflection_engine.

Parses the bracketed commands that the LLM can emit in autonomy contexts:
  [SEND_MESSAGE: text]
  [SCHEDULE_MESSAGE: YYYY-MM-DD HH:MM | text]
  [CANCEL_MESSAGE: YYYY-MM-DD HH:MM]
  [CANCEL_ALL_SCHEDULED]
  [RESCHEDULE_MESSAGE: YYYY-MM-DD HH:MM -> YYYY-MM-DD HH:MM]
  [REWRITE_MESSAGE: YYYY-MM-DD HH:MM | new text]

The parser returns structured ParsedCommand objects; callers decide what to do
with them.  Keeping parsing separate from execution makes both easy to test.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal


class CmdType(str, Enum):
    SEND_MESSAGE = "SEND_MESSAGE"
    SCHEDULE_MESSAGE = "SCHEDULE_MESSAGE"
    CANCEL_MESSAGE = "CANCEL_MESSAGE"
    CANCEL_ALL_SCHEDULED = "CANCEL_ALL_SCHEDULED"
    RESCHEDULE_MESSAGE = "RESCHEDULE_MESSAGE"
    REWRITE_MESSAGE = "REWRITE_MESSAGE"


@dataclass
class SendMessage:
    type: Literal[CmdType.SEND_MESSAGE] = CmdType.SEND_MESSAGE
    text: str = ""


@dataclass
class ScheduleMessage:
    type: Literal[CmdType.SCHEDULE_MESSAGE] = CmdType.SCHEDULE_MESSAGE
    ts_str: str = ""    # "YYYY-MM-DD HH:MM" local time
    text: str = ""


@dataclass
class CancelMessage:
    type: Literal[CmdType.CANCEL_MESSAGE] = CmdType.CANCEL_MESSAGE
    ts_str: str = ""    # "YYYY-MM-DD HH:MM" local time


@dataclass
class CancelAllScheduled:
    type: Literal[CmdType.CANCEL_ALL_SCHEDULED] = CmdType.CANCEL_ALL_SCHEDULED


@dataclass
class RescheduleMessage:
    type: Literal[CmdType.RESCHEDULE_MESSAGE] = CmdType.RESCHEDULE_MESSAGE
    old_ts_str: str = ""
    new_ts_str: str = ""


@dataclass
class RewriteMessage:
    type: Literal[CmdType.REWRITE_MESSAGE] = CmdType.REWRITE_MESSAGE
    ts_str: str = ""
    new_text: str = ""


ParsedCommand = SendMessage | ScheduleMessage | CancelMessage | CancelAllScheduled | RescheduleMessage | RewriteMessage

# ── Regexes ───────────────────────────────────────────────────────────────────

_TS = r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}"

_SEND_RE = re.compile(
    r"\[SEND[_ ]MESSAGE:\s*(?P<text>.+?)\]",
    re.IGNORECASE | re.DOTALL,
)
_SCHEDULE_RE = re.compile(
    rf"\[SCHEDULE[_ ]MESSAGE:\s*(?P<ts>{_TS})\s*\|\s*(?P<text>.+?)\]",
    re.IGNORECASE | re.DOTALL,
)
_CANCEL_RE = re.compile(
    rf"\[CANCEL[_ ]MESSAGE:\s*(?P<ts>{_TS})\]",
    re.IGNORECASE,
)
_CANCEL_ALL_RE = re.compile(
    r"\[CANCEL[_ ]ALL[_ ]SCHEDULED\]",
    re.IGNORECASE,
)
_RESCHEDULE_RE = re.compile(
    rf"\[RESCHEDULE[_ ]MESSAGE:\s*(?P<old>{_TS})\s*->\s*(?P<new>{_TS})\]",
    re.IGNORECASE,
)
_REWRITE_RE = re.compile(
    rf"\[REWRITE[_ ]MESSAGE:\s*(?P<ts>{_TS})\s*\|\s*(?P<text>.+?)\]",
    re.IGNORECASE | re.DOTALL,
)

# All command regexes in one pass — used for stripping commands from free text.
_ALL_CMDS_RE = re.compile(
    r"\[(?:(?:SEND|SCHEDULE|CANCEL|RESCHEDULE|REWRITE)[_ ]MESSAGE:[^\]]*|CANCEL[_ ]ALL[_ ]SCHEDULED)\]",
    re.IGNORECASE | re.DOTALL,
)


def parse_commands(response: str) -> list[ParsedCommand]:
    """Extract all autonomy commands from an LLM response string.

    Returns a list of ParsedCommand objects in the order they appear in the text.
    Each entry is one of: SendMessage, ScheduleMessage, CancelMessage,
    RescheduleMessage, RewriteMessage.
    """
    # Collect all matches with their position so we preserve order.
    hits: list[tuple[int, ParsedCommand]] = []

    for m in _SEND_RE.finditer(response):
        hits.append((m.start(), SendMessage(text=m.group("text").strip())))

    for m in _SCHEDULE_RE.finditer(response):
        ts = " ".join(m.group("ts").split())   # normalise any extra whitespace
        hits.append((m.start(), ScheduleMessage(ts_str=ts, text=m.group("text").strip())))

    for m in _CANCEL_RE.finditer(response):
        ts = " ".join(m.group("ts").split())
        hits.append((m.start(), CancelMessage(ts_str=ts)))

    for m in _CANCEL_ALL_RE.finditer(response):
        hits.append((m.start(), CancelAllScheduled()))

    for m in _RESCHEDULE_RE.finditer(response):
        old = " ".join(m.group("old").split())
        new = " ".join(m.group("new").split())
        hits.append((m.start(), RescheduleMessage(old_ts_str=old, new_ts_str=new)))

    for m in _REWRITE_RE.finditer(response):
        ts = " ".join(m.group("ts").split())
        hits.append((m.start(), RewriteMessage(ts_str=ts, new_text=m.group("text").strip())))

    hits.sort(key=lambda x: x[0])
    return [cmd for _, cmd in hits]


def strip_commands(response: str) -> str:
    """Remove all autonomy command brackets from a response, leaving only free text."""
    return _ALL_CMDS_RE.sub("", response).strip()
