"""Read-only health report over the memory store.

Surfaces memories that need attention: stale (past their decay horizon),
low-confidence, unverified auto-captures, and value conflicts between two
promoted facts.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter

from engram.core.dedup import compare
from engram.core.freshness import is_stale
from engram.core.schema import LearnedBy, Memory, Status
from engram.core.supersede import MAX_SUPERSEDES

_AUTO_SOURCES = {LearnedBy.harvest, LearnedBy.imported}


def _supersession_faults(memories: list[Memory]) -> tuple[list, list]:
    """Retirements that should never have been applied.

    Both are errors rather than observations. A fact that retired a dozen others
    matched on something that was not a subject, and a retirement authored by a
    fact nobody promoted was never anyone's decision - engram no longer produces
    either, so finding one means a store still carries the damage.
    """
    status = {m.id: m.status for m in memories}
    authors = Counter(m.superseded_by for m in memories if m.superseded_by)
    mass = [(author, count) for author, count in authors.items() if count > MAX_SUPERSEDES]
    unauthorized = [
        (m.id, m.superseded_by)
        for m in memories
        if m.superseded_by and status.get(m.superseded_by) is not Status.promoted
    ]
    return sorted(mass), sorted(unauthorized)


def doctor(memories: list[Memory], *, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    promoted = [m for m in memories if m.status == Status.promoted]
    mass, unauthorized = _supersession_faults(memories)
    report: dict[str, list] = {
        "stale": [],
        "superseded": [m.id for m in memories if m.status == Status.superseded],
        "mass_supersede": mass,
        "unauthorized_supersede": unauthorized,
        "low_confidence": [],
        "unverified": [],
        "conflicts": [],
    }
    for memory in promoted:
        if is_stale(memory, today=today):
            report["stale"].append(memory.id)
        if memory.confidence < 0.5:
            report["low_confidence"].append(memory.id)
        if memory.last_verified is None and memory.learned_by in _AUTO_SOURCES:
            report["unverified"].append(memory.id)
    for i, first in enumerate(promoted):
        for second in promoted[i + 1 :]:
            if first.project != second.project:
                continue  # cross-project facts never conflict
            if compare(first.fact, second.fact) == "conflict":
                report["conflicts"].append((first.id, second.id))
    return report
