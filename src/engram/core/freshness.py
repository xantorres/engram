"""Decay parsing and staleness detection.

A memory carries a ``decay`` horizon (e.g. ``"180d"``). Past ``last_verified``
(or ``learned_at`` if never re-seen) plus that horizon, it is stale and should
drop out of recall until re-confirmed.
"""

from __future__ import annotations

import datetime as dt
import re

from engram.core.schema import Memory

_UNIT_DAYS = {"h": 1 / 24, "d": 1, "w": 7, "m": 30, "y": 365}


def parse_decay(spec: str) -> dt.timedelta:
    match = re.fullmatch(r"\s*(\d+)\s*([hdwmy])\s*", spec or "")
    if not match:
        raise ValueError(f"invalid decay spec: {spec!r}")
    amount, unit = int(match.group(1)), match.group(2)
    return dt.timedelta(days=amount * _UNIT_DAYS[unit])


def is_stale(memory: Memory, *, today: dt.date | None = None) -> bool:
    today = today or dt.date.today()
    base = memory.last_verified or memory.learned_at
    return (today - base) > parse_decay(memory.decay)
