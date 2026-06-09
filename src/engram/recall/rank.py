"""Select and rank memories for recall.

Recall surfaces only *promoted* and *fresh* memories. A query does keyword
overlap scoring; without one, results are ordered by confidence.
"""

from __future__ import annotations

import datetime as dt
import re

from engram.core.freshness import is_stale
from engram.core.schema import Memory, Status


def recallable(memories: list[Memory], *, today: dt.date | None = None) -> list[Memory]:
    today = today or dt.date.today()
    return [m for m in memories if m.status == Status.promoted and not is_stale(m, today=today)]


def rank(
    memories: list[Memory],
    query: str | None = None,
    *,
    limit: int = 20,
    today: dt.date | None = None,
) -> list[Memory]:
    pool = recallable(memories, today=today)
    if query:
        wanted = _tokens(query)
        scored = [(len(wanted & _tokens(m.fact)), m.confidence, m) for m in pool]
        scored = [s for s in scored if s[0] > 0]
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        ranked = [m for _, _, m in scored]
    else:
        ranked = sorted(pool, key=lambda m: m.confidence, reverse=True)
    return ranked[:limit]


def to_dict(memory: Memory) -> dict:
    return {
        "id": memory.id,
        "fact": memory.fact,
        "kind": memory.kind.value,
        "confidence": memory.confidence,
    }


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3}
