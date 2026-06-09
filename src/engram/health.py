"""Read-only health report over the memory store.

Surfaces memories that need attention: stale (past their decay horizon),
low-confidence, unverified auto-captures, and value conflicts between two
promoted facts.
"""

from __future__ import annotations

import datetime as dt

from engram.core.dedup import compare
from engram.core.freshness import is_stale
from engram.core.schema import LearnedBy, Memory, Status

_AUTO_SOURCES = {LearnedBy.harvest, LearnedBy.imported}


def doctor(memories: list[Memory], *, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    promoted = [m for m in memories if m.status == Status.promoted]
    report: dict[str, list] = {
        "stale": [],
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
            if compare(first.fact, second.fact) == "conflict":
                report["conflicts"].append((first.id, second.id))
    return report
