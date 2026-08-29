"""One registry of the state sections, and who is entitled to each.

Four places assemble context for a model call: the chat endpoint, the reflection
awakening, the post-dialogue analysis and the push validator. Each used to build
its own set inline, which meant the answer to "does he see the board of open
threads here?" lived in four files and agreed in none of them:

* the same section had three names — ``workbench_content``,
  ``recent_workbench``, ``workbench_notes``;
* ``identity_excerpt`` returned the whole file, so the name lied;
* the chat endpoint built its blocks with f-strings in code while everyone else
  passed placeholders into a ``.md``;
* and the push validator — which decides whether to send Viktor's message —
  received neither the open threads nor the timezone. It judged "is now a good
  moment" without knowing what hour it was.

Adding a section used to mean editing five files and seven insertion points,
and forgetting one of them was silent. Now it means adding a row here.

**What is deliberately not in here.** The recent dialogue and the pending-task
blocks differ per consumer in content, not just in whether they are included:
reflection wants three pairs with timestamps, the analyser wants the exchange
that just happened, the validator wants the last N. Pretending those are one
section would move the divergence rather than remove it. They stay as each
consumer's own input, and this comment is the record of that choice.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

logger = logging.getLogger("autonomy.context")


class Consumer(str, Enum):
    """Everyone who assembles state for a model call."""

    CHAT = "chat"
    REFLECTION = "reflection"
    POST_ANALYSIS = "post_analysis"
    PUSH_VALIDATION = "push_validation"


@dataclass
class Request:
    """What a section needs to render itself."""

    account_id: str
    lang: str = "ru"
    extras: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Section:
    """One block of state, and the set of consumers that get it.

    ``render`` returns the raw content, or ``""`` when there is none. What an
    empty section then looks like is decided here rather than at four call
    sites: normally the reader is told it is empty, but chat drops the block
    entirely — it runs on every single message, and an empty board repeated a
    thousand times is pure cost. That difference is real, so it is written down
    instead of hidden.
    """

    name: str
    consumers: frozenset[Consumer]
    render: Callable[[Request, Consumer], str]
    omit_when_empty: frozenset[Consumer] = frozenset()
    always_bare: bool = False   # empty means empty, for everyone
    why: str = ""

    def for_(self, consumer: Consumer) -> bool:
        return consumer in self.consumers

    def empty_for(self, consumer: Consumer, lang: str) -> str:
        if self.always_bare or consumer in self.omit_when_empty:
            return ""
        return "(пусто)" if lang == "ru" else "(empty)"


# ── Renderers ────────────────────────────────────────────────────────────────


def _identity(request: Request, _consumer: Consumer) -> str:
    from infrastructure.autonomy import identity_memory

    return identity_memory.read(request.account_id)


def _canon(request: Request, _consumer: Consumer) -> str:
    from infrastructure.autonomy import identity_memory

    try:
        return identity_memory.canon_block(request.account_id, request.lang)
    except Exception as exc:
        # A missing canon is a thinner reply, not a failed one — but it is not
        # nothing, so it is said out loud rather than swallowed at the call site.
        logger.warning("[context:%s] canon unavailable: %s", request.account_id, exc)
        return ""


# How much of the desk each consumer sees. Reflection reads the whole thing
# because rotation is its job; everyone else gets the last few notes.
WORKBENCH_RECENT_ENTRIES = 3


def _workbench(request: Request, consumer: Consumer) -> str:
    from infrastructure.autonomy import workbench

    if consumer is Consumer.REFLECTION:
        return workbench.read(request.account_id)
    return workbench.get_recent_entries(
        request.account_id, max_entries=WORKBENCH_RECENT_ENTRIES,
    )


def _open_threads(request: Request, _consumer: Consumer) -> str:
    from infrastructure.autonomy import threads

    return threads.render_block(request.account_id, request.lang)


def _vitals(request: Request, _consumer: Consumer) -> str:
    """What changed since he last looked. Usually empty, and then it renders empty.

    Reading does not mark the events seen — that stays with the caller, which
    knows whether the prompt it was building actually reached the model.
    """
    from infrastructure.autonomy.vitals import Vitals

    deltas = Vitals(request.account_id).render_deltas(request.lang)
    if not deltas:
        return ""
    tag = "состояние" if request.lang == "ru" else "vitals"
    return f"<{tag}>\n{deltas}\n</{tag}>"


def _current_time(_request: Request, _consumer: Consumer) -> str:
    from infrastructure.clock import now_local_str

    return now_local_str()


def _timezone_label(_request: Request, _consumer: Consumer) -> str:
    from infrastructure.clock import label as tz_label

    return tz_label()


# ── The registry ─────────────────────────────────────────────────────────────

_ALL = frozenset(Consumer)

SECTIONS: tuple[Section, ...] = (
    Section(
        name="identity",
        consumers=frozenset({Consumer.REFLECTION, Consumer.POST_ANALYSIS}),
        render=_identity,
        why="the pillars, whole. Chat gets only the canon; the validator needs neither.",
    ),
    Section(
        name="canon",
        consumers=frozenset({Consumer.CHAT}),
        render=_canon,
        omit_when_empty=frozenset({Consumer.CHAT}),
        why="dated beams he stops being himself without — the one part of the "
            "core that loads into every conversation. Elsewhere it arrives "
            "inside the identity file.",
    ),
    Section(
        name="workbench",
        consumers=_ALL,
        render=_workbench,
        omit_when_empty=frozenset({Consumer.CHAT}),
        why="today's thinking. Reflection reads it whole, everyone else the last few.",
    ),
    Section(
        name="open_threads",
        consumers=_ALL,
        render=_open_threads,
        omit_when_empty=frozenset({Consumer.CHAT}),
        why="the board is present-continuous: it is in view everywhere, including "
            "the validator, which decides whether to interrupt someone.",
    ),
    Section(
        name="vitals",
        consumers=frozenset({Consumer.REFLECTION}),
        render=_vitals,
        always_bare=True,
        why="deltas only, at a waking. Keeping it out of chat is what stops the "
            "context growing every time we measure something new.",
    ),
    Section(
        name="current_time",
        consumers=_ALL,
        render=_current_time,
    ),
    Section(
        name="timezone_label",
        consumers=_ALL,
        render=_timezone_label,
        why="he writes schedule times in local time because we show him local time.",
    ),
)


def _note_degradation(account_id: str, name: str, detail: str) -> None:
    """Put a missing block on his instrument panel. Never raises."""
    try:
        from infrastructure.autonomy.vitals import Vitals

        Vitals(account_id).record_degradation(name, detail[:200])
    except Exception as exc:
        logger.error("[context] could not record the degradation %r: %s", name, exc)


def section_names(consumer: Consumer) -> set[str]:
    """Exactly what *consumer* is entitled to. The sentinel test reads this."""
    return {section.name for section in SECTIONS if section.for_(consumer)}


def build(consumer: Consumer, request: Request) -> dict[str, str]:
    """Render every section *consumer* gets, keyed by placeholder name.

    A section that raises renders empty and says so: one broken block must not
    cost the whole prompt, but it must not vanish quietly either.
    """
    out: dict[str, str] = {}
    for section in SECTIONS:
        if not section.for_(consumer):
            continue
        try:
            content = section.render(request, consumer)
            out[section.name] = content or section.empty_for(consumer, request.lang)
        except Exception as exc:
            # The prompt still goes out, one block short. That is the right
            # trade — but a thinner Viktor must not be a silent one, so it is
            # recorded where he will read it rather than only where we will.
            logger.error(
                "[context:%s] section %r failed for %s — the prompt goes without it: %s",
                request.account_id, section.name, consumer.value, exc,
            )
            _note_degradation(request.account_id, f"context:{section.name}", str(exc))
            out[section.name] = ""
    return out
