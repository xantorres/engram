"""Active capture - the ``remember`` entry point.

An agent (or the user) deliberately stages a single fact. It lands as a pending
candidate; the bridge decides later whether it auto-logs or needs review.
"""

from __future__ import annotations

from engram.core.schema import Kind, LearnedBy, Memory
from engram.core.store import Store
from engram.core.text import clean_fact


def remember(
    store: Store,
    fact: str,
    *,
    kind: Kind = Kind.preference,
    confidence: float = 0.6,
    source: str = "tool:remember",
) -> Memory:
    return store.add(
        Memory(
            fact=clean_fact(fact),
            kind=kind,
            confidence=confidence,
            learned_by=LearnedBy.remember,
            source=source,
        )
    )
