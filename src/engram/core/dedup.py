"""Heuristic duplicate / conflict detection between two memory facts.

The goal is not perfect semantics but a safe gate: catch obvious restatements
(so we do not store the same fact twice) and catch value divergences on the same
subject (so a changed number forces human review instead of a silent overwrite).
"""

from __future__ import annotations

import re

# Precision tokens carry exact values that must match or signal a conflict.
_PRECISION_PATTERNS = [
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),  # email
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # ISO date
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),  # IBAN-ish
    re.compile(r"\b\d+[.,]\d{2}\b"),  # money
    re.compile(r"\b[A-Z0-9]{7,}\b"),  # identifiers (TIC/VAT/passport/...)
]

_STOPWORDS = frozenset(
    "the a an of to in on for and or is are was were be been being with at by"
    " my i you he she it we they this that".split()
)

# Facts name the same thing with different punctuation ("code-graph" vs
# "codegraph"), so each compound also yields its joined spelling. The parts are
# kept: a compound carries most of a fact's discriminating weight, and replacing
# "a-b-c" with one token leaves only sentence boilerplate to compare on, at
# which point every fact sharing a template looks like a duplicate of every
# other. Emitting both forms buys the match without paying that.
_SEPARATORS = re.compile(r"[-_]")
_COMPOUND = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)+")

_DUP_THRESHOLD = 0.5
_CONFLICT_OVERLAP = 0.34


def precision_tokens(text: str) -> set[str]:
    out: set[str] = set()
    for pat in _PRECISION_PATTERNS:
        out.update(m.group(0) for m in pat.finditer(text))
    return out


def _keep(word: str) -> bool:
    return len(word) >= 3 and word not in _STOPWORDS


def salient_tokens(text: str) -> set[str]:
    lowered = text.lower()
    out = {w for w in re.findall(r"[a-z0-9]+", lowered) if _keep(w)}
    out.update(
        joined
        for compound in _COMPOUND.findall(lowered)
        if _keep(joined := _SEPARATORS.sub("", compound))
    )
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def compare(a: str, b: str) -> str:
    """Return ``"duplicate"``, ``"conflict"``, or ``"distinct"`` for two facts."""
    overlap = _jaccard(salient_tokens(a), salient_tokens(b))
    pa, pb = precision_tokens(a), precision_tokens(b)

    if overlap >= _CONFLICT_OVERLAP and (pa or pb) and pa != pb:
        return "conflict"
    if overlap >= _DUP_THRESHOLD and pa == pb:
        return "duplicate"
    return "distinct"
