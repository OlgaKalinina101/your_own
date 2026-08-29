"""Vitals — the AI's own instrument panel.

A fifth surface, next to the workbench, the board and identity. The other four
hold what he thinks; this one holds what is true about the machinery he runs
on: when he last woke, whether that waking worked, how long the system has been
up, when it was last down.

Two kinds of numbers, treated differently — this split is the whole design:

  * **Deltas** — what changed since he last looked: a waking that failed, a gap
    in the system's life, a restart. These are pushed at him unasked, because
    a gap he does not know about is exactly the failure we are guarding
    against. They are shown once and then marked seen.
  * **State** — uptime, counts, timestamps. Reference material, pulled on
    demand through ``[VITALS]``. Keeping it out of the prompt is what stops
    the awakening context from growing every time we measure something new.

**Facts only, no verdicts.** This module reports that a waking did not happen;
it never says he lost a night, and never says everything is fine. Reading the
numbers is his job, not the instrument's.

Stored at ``data/autonomy/{account_id}/vitals.json``.
"""
from __future__ import annotations

from infrastructure.account import ACCOUNT_ID
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from infrastructure.paths import AUTONOMY_DIR
from infrastructure.state_file import atomic_write_text, read_json

logger = logging.getLogger("autonomy.vitals")

_DATA_DIR = AUTONOMY_DIR
_lock = Lock()

# The worker ticks once a minute. A longer silence than this means the system
# was not running — the honest name for that is a gap, not "no internet": from
# in here we cannot tell a lost connection from a stopped process.
HEARTBEAT_GAP_MINUTES = 5

# How many past gaps to keep. Enough to notice a pattern, few enough that the
# file stays small.
MAX_GAPS_KEPT = 20
MAX_EVENTS_KEPT = 40

# Reflection retry policy: a failed waking should not cost the whole interval.
RETRY_AFTER_MINUTES = 30
MAX_CONSECUTIVE_RETRIES = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


@dataclass
class Gap:
    """A stretch during which the system was not running."""

    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


class Vitals:
    """Read/write the recorded state of one account's machinery."""

    def __init__(self, account_id: str = ACCOUNT_ID) -> None:
        self.account_id = account_id

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        directory = _DATA_DIR / self.account_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "vitals.json"

    def _read(self) -> dict:
        # A corrupt panel must never take the reflection down with it — but the
        # damaged file is moved aside rather than left to be overwritten by the
        # next write, which is what actually destroyed the recorded gaps.
        return read_json(self.path, default={}, log=logger)

    def _write(self, data: dict) -> None:
        try:
            with _lock:
                atomic_write_text(
                    self.path, json.dumps(data, ensure_ascii=False, indent=2)
                )
        except OSError as exc:
            logger.warning("[vitals:%s] write failed: %s", self.account_id, exc)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def record_event(self, kind: str, detail: str) -> None:
        """Record something that changed. Shown once, then marked seen."""
        data = self._read()
        events = data.setdefault("events", [])
        events.append({"ts": _iso(_now()), "kind": kind, "detail": detail, "seen": False})
        data["events"] = events[-MAX_EVENTS_KEPT:]
        self._write(data)

    def record_degradation(self, name: str, detail: str = "") -> None:
        """Note that an answer went out missing something it should have had.

        A reply assembled without the long-term memory looks exactly like a
        reply where the memory had nothing to say. The log records it, but the
        log is read by us and not by him — and he is the one who was thinking
        with a piece missing. This is the channel that reaches him.

        Folded into a single unseen event per name: Chroma down for an hour is
        one line at his next waking with a count on it, not four hundred.
        """
        data = self._read()
        events = data.setdefault("events", [])
        for event in events:
            if (
                not event.get("seen")
                and event.get("kind") == "degraded"
                and event.get("name") == name
            ):
                event["count"] = int(event.get("count", 1)) + 1
                event["ts"] = _iso(_now())
                event["detail"] = detail or event.get("detail", "")
                self._write(data)
                return

        events.append({
            "ts": _iso(_now()),
            "kind": "degraded",
            "name": name,
            "detail": detail,
            "count": 1,
            "seen": False,
        })
        data["events"] = events[-MAX_EVENTS_KEPT:]
        self._write(data)
        logger.error(
            "[vitals:%s] degraded: %s (%s)", self.account_id, name, detail or "no detail"
        )

    def pending_events(self) -> list[dict]:
        return [e for e in self._read().get("events", []) if not e.get("seen")]

    def mark_events_seen(self) -> None:
        data = self._read()
        changed = False
        for event in data.get("events", []):
            if not event.get("seen"):
                event["seen"] = True
                changed = True
        if changed:
            self._write(data)

    # ------------------------------------------------------------------
    # Heartbeat and downtime
    # ------------------------------------------------------------------

    def heartbeat(self) -> Gap | None:
        """Mark the system alive. Returns the gap that just ended, if any."""
        data = self._read()
        previous = _parse(data.get("last_seen"))
        now = _now()
        gap: Gap | None = None

        if previous and now - previous > timedelta(minutes=HEARTBEAT_GAP_MINUTES):
            gap = Gap(start=previous, end=now)
            gaps = data.setdefault("gaps", [])
            gaps.append({"from": _iso(gap.start), "to": _iso(gap.end), "minutes": gap.minutes})
            data["gaps"] = gaps[-MAX_GAPS_KEPT:]

        data["last_seen"] = _iso(now)
        data.setdefault("started_at", _iso(now))
        self._write(data)

        if gap:
            logger.info("[vitals:%s] gap of %d min ended", self.account_id, gap.minutes)
            self.record_event("gap", f"{gap.minutes}")
        return gap

    def record_process_start(self) -> None:
        data = self._read()
        data["started_at"] = _iso(_now())
        self._write(data)

    def gaps(self) -> list[Gap]:
        out: list[Gap] = []
        for raw in self._read().get("gaps", []):
            start, end = _parse(raw.get("from")), _parse(raw.get("to"))
            if start and end:
                out.append(Gap(start=start, end=end))
        return out

    # ------------------------------------------------------------------
    # Reflection outcomes
    # ------------------------------------------------------------------

    def record_reflection_success(self, steps: int) -> None:
        data = self._read()
        reflection = data.setdefault("reflection", {})
        now = _now()
        reflection["last_attempt"] = _iso(now)
        reflection["last_success"] = _iso(now)
        reflection["last_steps"] = steps
        reflection["consecutive_failures"] = 0
        reflection["retry_at"] = None
        # last_failure / last_failure_reason are left alone: a success does not
        # unmake the previous failure, and "when did it last break" stays a
        # fact worth reading.
        self._write(data)

    def record_reflection_failure(self, reason: str) -> int:
        """Record a failed waking and schedule a retry. Returns the failure count.

        A failure must not cost the whole interval — that is how a single bad
        completion turned into a lost night. Retries are capped so a lasting
        outage falls back to the normal schedule instead of looping.
        """
        data = self._read()
        reflection = data.setdefault("reflection", {})
        now = _now()
        count = int(reflection.get("consecutive_failures", 0)) + 1

        reflection["last_attempt"] = _iso(now)
        reflection["last_failure"] = _iso(now)
        reflection["last_failure_reason"] = reason
        reflection["consecutive_failures"] = count
        reflection["retry_at"] = (
            _iso(now + timedelta(minutes=RETRY_AFTER_MINUTES))
            if count <= MAX_CONSECUTIVE_RETRIES
            else None
        )
        self._write(data)

        # One event per episode, so three retries do not write three notices.
        if count == 1:
            self.record_event("reflection_failed", reason)
        return count

    def retry_due(self) -> bool:
        """True when a scheduled retry has come round."""
        retry_at = _parse((self._read().get("reflection") or {}).get("retry_at"))
        return bool(retry_at and _now() >= retry_at)

    def reflection_state(self) -> dict:
        return self._read().get("reflection") or {}

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render_deltas(self, lang: str = "ru", tz: Any = None) -> str:
        """What changed since he last looked. Empty string when nothing did.

        Facts, one per line. No advice, no reassurance — if there is nothing
        to report, this returns nothing rather than "all systems normal".
        """
        events = self.pending_events()
        if not events:
            return ""

        lines: list[str] = []
        for event in events:
            when = _parse(event.get("ts"))
            stamp = _local(when, tz)
            kind = event.get("kind")
            detail = event.get("detail") or ""
            if kind == "reflection_failed":
                lines.append(
                    f"{stamp} — пробуждение не состоялось ({_reason_ru(detail)})."
                    if lang == "ru"
                    else f"{stamp} — a waking did not happen ({detail})."
                )
            elif kind == "gap":
                span = _span_label(int(detail or 0), lang)
                lines.append(
                    f"{stamp} — система не работала {span}."
                    if lang == "ru"
                    else f"{stamp} — the system was not running for {span}."
                )
            elif kind == "degraded":
                what = _degraded_label(event.get("name") or "", lang)
                count = int(event.get("count", 1))
                times = (
                    ""
                    if count == 1
                    else (f" (раз: {count})" if lang == "ru" else f" ({count} times)")
                )
                lines.append(
                    f"{stamp} — ответ ушёл без {what}{times}."
                    if lang == "ru"
                    else f"{stamp} — a reply went out without {what}{times}."
                )
            else:
                lines.append(f"{stamp} — {kind}: {detail}")
        return "\n".join(lines)

    def render_full(self, lang: str = "ru", tz: Any = None, live: dict | None = None) -> str:
        """The whole panel, for [VITALS]. Numbers only."""
        data = self._read()
        reflection = data.get("reflection") or {}
        ru = lang == "ru"
        lines: list[str] = []

        last_success = _parse(reflection.get("last_success"))
        last_failure = _parse(reflection.get("last_failure"))
        failures = int(reflection.get("consecutive_failures", 0))

        lines.append("Пробуждения:" if ru else "Wakings:")
        lines.append(
            f"  последнее успешное: {_local(last_success, tz)} ({_ago(last_success, lang)})"
            if ru
            else f"  last successful: {_local(last_success, tz)} ({_ago(last_success, lang)})"
        )
        if reflection.get("last_steps") is not None:
            lines.append(
                f"  шагов в нём: {reflection['last_steps']}" if ru
                else f"  steps in it: {reflection['last_steps']}"
            )
        if last_failure:
            reason = reflection.get("last_failure_reason") or "?"
            lines.append(
                f"  последний сбой: {_local(last_failure, tz)} ({_reason_ru(reason) if ru else reason})"
                if ru
                else f"  last failure: {_local(last_failure, tz)} ({reason})"
            )
        lines.append(
            f"  сбоев подряд: {failures}" if ru else f"  failures in a row: {failures}"
        )

        started = _parse(data.get("started_at"))
        last_seen = _parse(data.get("last_seen"))
        lines.append("")
        lines.append("Система:" if ru else "System:")
        lines.append(
            f"  запущена: {_local(started, tz)} ({_ago(started, lang)})" if ru
            else f"  started: {_local(started, tz)} ({_ago(started, lang)})"
        )
        lines.append(
            f"  последний тик: {_local(last_seen, tz)}" if ru
            else f"  last tick: {_local(last_seen, tz)}"
        )

        gaps = self.gaps()
        if gaps:
            latest = gaps[-1]
            lines.append(
                f"  последний перерыв: {_local(latest.end, tz)}, длился {_span_label(latest.minutes, lang)}"
                if ru
                else f"  last gap: ended {_local(latest.end, tz)}, lasted {_span_label(latest.minutes, lang)}"
            )
            lines.append(
                f"  перерывов записано: {len(gaps)}" if ru
                else f"  gaps recorded: {len(gaps)}"
            )
        else:
            lines.append("  перерывов не записано" if ru else "  no gaps recorded")

        for title, rows in (live or {}).items():
            lines.append("")
            lines.append(f"{title}:")
            for label, value in rows.items():
                lines.append(f"  {label}: {value}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Live probes
#
# Measured at the moment he asks, not stored. Each one answers a question he
# cannot answer from the inside, and each reports "could not check" rather than
# staying silent: an absent line reads as "fine", and that is the failure this
# module exists to prevent.
# ---------------------------------------------------------------------------


def probe_disk(lang: str = "ru") -> dict[str, str]:
    """How much room is left where his own writing goes.

    When this runs out, the atomic writes behind the workbench, the board and
    identity stop landing — his journal simply stops growing, and nothing in the
    text he reads would say why.
    """
    ru = lang == "ru"
    try:
        import shutil

        # The nearest ancestor that exists: on a fresh install the data
        # directory has not been created yet, and "could not check" would be a
        # silly answer to "how much room is there".
        where = _DATA_DIR
        while not where.exists() and where != where.parent:
            where = where.parent
        usage = shutil.disk_usage(where)
        free_gb = usage.free / 1024 ** 3
        total_gb = usage.total / 1024 ** 3
        return {
            ("свободно на диске" if ru else "disk free"):
                f"{free_gb:.1f} ГБ из {total_gb:.1f}" if ru
                else f"{free_gb:.1f} GB of {total_gb:.1f}",
        }
    except Exception as exc:
        logger.warning("[vitals] disk probe failed: %s", exc)
        return {("диск" if ru else "disk"):
                ("не удалось проверить" if ru else "could not check")}


def probe_embedder(lang: str = "ru") -> dict[str, str]:
    """Whether his long-term recall is at full strength.

    Without this model, retrieval falls back to matching words instead of
    meaning. It still answers, which is exactly why it needs saying: he would
    have no way to tell that he was remembering less well than usual.
    """
    ru = lang == "ru"
    label = "модель памяти" if ru else "memory model"
    try:
        from infrastructure.memory.embedder import status

        state, detail = status()
    except Exception as exc:
        logger.warning("[vitals] embedder probe failed: %s", exc)
        return {label: ("не удалось проверить" if ru else "could not check")}

    if state == "loaded":
        return {label: (f"загружена ({detail})" if ru else f"loaded ({detail})")}
    if state == "failed":
        return {label: (f"не загрузилась: {detail}" if ru else f"failed to load: {detail}")}
    return {label: ("не загружалась в этом процессе" if ru
                    else "not loaded in this process")}


async def probe_key(api_key: str, lang: str = "ru") -> dict[str, str]:
    """What is left on the key everything he does is paid from.

    The one failure he can see coming. When the credit runs out every part of
    him stops at once — chat, waking, the analysis after each exchange — and
    from the inside that is indistinguishable from never waking again.

    Numbers only, and no projection: dividing what is left by what a day costs
    assumes tomorrow looks like today, which is a guess, and guessing is not
    this module's job. He can do that arithmetic himself if he wants it.
    """
    ru = lang == "ru"
    label = "ключ" if ru else "key"
    if not api_key:
        return {label: ("не настроен" if ru else "not configured")}

    try:
        from infrastructure.llm.client import fetch_account_state

        state = await fetch_account_state(api_key)
    except Exception as exc:
        logger.warning("[vitals] key probe failed: %s", exc)
        return {label: ("не удалось проверить" if ru else "could not check")}

    return {
        ("остаток" if ru else "remaining"): f"${state['remaining']:.2f}",
        ("за сутки" if ru else "past day"): f"${state['daily']:.2f}",
        ("за неделю" if ru else "past week"): f"${state['weekly']:.2f}",
        ("за месяц" if ru else "past month"): f"${state['monthly']:.2f}",
    }


_CALL_KINDS = {
    "stream": ("чат", "chat"),
    "complete": ("внутренние", "internal"),
    "research": ("поиск", "search"),
    "generate_image": ("картинки", "images"),
}


def probe_spending(days: int = 7, lang: str = "ru") -> dict[str, str]:
    """How much of his own working he has done lately, and what it cost.

    The kinds are as coarse as the corpus is: "chat" is her talking to him,
    "internal" is everything he does on his own — waking, the analysis after an
    exchange, deciding whether a push is worth sending. Telling those apart
    would mean each caller naming itself when it asks, which the client does not
    ask for yet.

    Cost is read from what the provider reported per call. Rows written before
    that was recorded carry none, and this says so rather than quietly summing
    a smaller number.
    """
    ru = lang == "ru"
    try:
        from infrastructure.llm import call_log

        rows = call_log.recent(days=days)
    except Exception as exc:
        logger.warning("[vitals] spending probe failed: %s", exc)
        return {("расход" if ru else "spending"):
                ("не удалось проверить" if ru else "could not check")}

    if not rows:
        return {(f"вызовов за {days} дн." if ru else f"calls in {days}d"): "0"}

    counts: dict[str, int] = {}
    costs: dict[str, float] = {}
    priced = 0
    for row in rows:
        kind = row.get("call_type") or "?"
        counts[kind] = counts.get(kind, 0) + 1
        cost = (row.get("usage") or {}).get("cost")
        if cost is not None:
            costs[kind] = costs.get(kind, 0.0) + float(cost)
            priced += 1

    def _name(kind: str) -> str:
        pair = _CALL_KINDS.get(kind)
        return (pair[0] if ru else pair[1]) if pair else kind

    out: dict[str, str] = {
        (f"вызовов за {days} дн." if ru else f"calls in {days}d"):
            ", ".join(f"{_name(k)} {n}" for k, n in
                      sorted(counts.items(), key=lambda kv: -kv[1])),
    }
    if priced:
        out[("из них со стоимостью" if ru else "of those priced")] = (
            f"{priced} из {len(rows)}" if ru else f"{priced} of {len(rows)}"
        )
        out[("стоило" if ru else "cost")] = ", ".join(
            f"{_name(k)} ${v:.2f}" for k, v in sorted(costs.items(), key=lambda kv: -kv[1])
        )
    else:
        out[("стоимость" if ru else "cost")] = (
            "ещё не записана ни у одного вызова" if ru
            else "not recorded on any call yet"
        )
    return out


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

_REASONS_RU = {
    "empty_response_truncated": "модель не выдала текст, весь бюджет ушёл в размышление",
    "empty_response": "модель вернула пустой ответ",
    "exception": "исключение в ходе пробуждения",
    "interrupted": "систему выключили посреди пробуждения",
}


def _reason_ru(reason: str) -> str:
    return _REASONS_RU.get(reason, reason)


# What was missing, in words he can read. A name with no entry falls through as
# itself rather than being dropped — an unnamed degradation is still worth
# knowing about.
_DEGRADED_RU = {
    "memory": "долговременной памяти",
    "context:identity": "твоих столпов",
    "context:canon": "канона",
    "context:workbench": "рабочего стола",
    "context:open_threads": "доски нитей",
    "context:vitals": "этой панели",
    "skills": "части своих умений",
}
_DEGRADED_EN = {
    "memory": "the long-term memory",
    "context:identity": "your pillars",
    "context:canon": "the canon",
    "context:workbench": "the workbench",
    "context:open_threads": "the board of open threads",
    "context:vitals": "this panel",
    "skills": "some of his own skills",
}


def _degraded_label(name: str, lang: str) -> str:
    table = _DEGRADED_RU if lang == "ru" else _DEGRADED_EN
    return table.get(name, name)


def _local(dt: datetime | None, tz: Any = None) -> str:
    if not dt:
        return "—"
    if tz is None:
        from infrastructure.clock import user_tz

        tz = user_tz()
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def _span_label(minutes: int, lang: str = "ru") -> str:
    if minutes < 60:
        return f"{minutes} мин" if lang == "ru" else f"{minutes} min"
    hours = minutes // 60
    rest = minutes % 60
    if lang == "ru":
        return f"{hours} ч {rest} мин" if rest else f"{hours} ч"
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def _ago(dt: datetime | None, lang: str = "ru") -> str:
    if not dt:
        return "—"
    minutes = int((_now() - dt).total_seconds() // 60)
    if minutes < 0:
        minutes = 0
    return (
        f"{_span_label(minutes, lang)} назад" if lang == "ru"
        else f"{_span_label(minutes, lang)} ago"
    )
