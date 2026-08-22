"""Agents — the single orchestrator through which every search in the app runs.

One entry point (:func:`research`) and one class (:class:`ResearchAgent`).
Chat and reflection never touch a search backend directly: they hand the
agent a task, the agent probes a source, judges whether the result answers
the task, re-queries with a better formulation if it does not, and returns
a brief.

Backends live in :mod:`infrastructure.agents.sources` as plain async
functions registered in ``PROBES`` — adding a source is one function plus
one enum member, not a new class.
"""
from infrastructure.agents.research import (
    Citation,
    ResearchAgent,
    ResearchResult,
    Source,
    research,
)

__all__ = [
    "Citation",
    "ResearchAgent",
    "ResearchResult",
    "Source",
    "research",
]
