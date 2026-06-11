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


def clean_fact(text: str) -> str:
    """Strip C0 control characters and collapse all whitespace to single spaces."""
    return _WHITESPACE.sub(" ", _CONTROL.sub("", text)).strip()


def render_safe(text: str) -> str:
    """``clean_fact`` plus removal of the HTML-comment markers engram splices on."""
    return clean_fact(text).replace("<!--", "").replace("-->", "")
