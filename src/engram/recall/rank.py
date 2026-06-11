"""Select and rank memories for recall.

Recall surfaces only *promoted* and *fresh* memories. A query does keyword
overlap scoring; without one, results are ordered by confidence.
"""

from __future__ import annotations

import datetime as dt
import re

from engram.core.freshness import effective_confidence, is_stale
from engram.core.schema import Memory, Status


def recallable(
    memories: list[Memory], *, today: dt.date | None = None, project: str | None = None
) -> list[Memory]:
    today = today or dt.date.today()
    pool = [m for m in memories if m.status == Status.promoted and not is_stale(m, today=today)]
    if project is not None:
        # Scoped recall keeps unscoped (global) facts so a project context still
        # carries the user's universal preferences alongside its own.
        pool = [m for m in pool if m.project == project or m.project is None]
    return pool


def rank(
    memories: list[Memory],
    query: str | None = None,
    *,
    limit: int = 20,
    today: dt.date | None = None,
    project: str | None = None,
) -> list[Memory]:
    today = today or dt.date.today()
    pool = recallable(memories, today=today, project=project)

    def weight(memory: Memory) -> float:
        return effective_confidence(memory, today=today)


    if query:
        wanted = _tokens(query)
        scored = [(len(wanted & _tokens(m.fact)), weight(m), m) for m in pool]
        scored = [s for s in scored if s[0] > 0]
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        ranked = [m for _, _, m in scored]
    else:
        ranked = sorted(pool, key=weight, reverse=True)
    return ranked[:limit]


def to_dict(memory: Memory) -> dict:
    return {
        "id": memory.id,
        "fact": memory.fact,
        "kind": memory.kind.value,
        "confidence": memory.confidence,
    }


_STOPWORDS = frozenset(
    "the and for with you your has have are was were will would can could should"
    " this that these those they them their what when where which who how why"
    " about into from out off over under not but our please help need want".split()
)


def _tokens(text: str) -> set[str]:
    return {
        w
        for w in re.findall(r"[a-z0-9]+", text.lower())
        if len(w) >= 3 and w not in _STOPWORDS
    }
