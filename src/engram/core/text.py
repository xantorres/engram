"""Normalise and neutralise fact text at the store's boundaries.

``clean_fact`` runs at ingest - lossy by design: a multi-line fact is flattened
to a single line so it can never corrupt the line-oriented log or registry body.
``render_safe`` runs at every render so a fact can never inject the HTML-comment
markers engram uses to splice its block into a user's context file.
"""

from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")

# One atomic assertion about the user fits comfortably. The cap is a safety
# bound, not a style rule: dedup runs a backtracking regex over every fact once
# per stored memory, and its cost grows with the square of the input, so an
# unbounded fact turns a single capture into minutes of pinned CPU.
MAX_FACT_CHARS = 2000


def clean_fact(text: str) -> str:
    """Strip C0 control characters, collapse whitespace, and bound the length."""
    return _WHITESPACE.sub(" ", _CONTROL.sub("", text)).strip()[:MAX_FACT_CHARS]


def render_safe(text: str) -> str:
    """``clean_fact`` plus removal of the HTML-comment markers engram splices on."""
    return clean_fact(text).replace("<!--", "").replace("-->", "")
