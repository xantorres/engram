"""Optional semantic second opinion on whether one fact retires another.

The rules in :mod:`engram.core.supersede` need two facts to share a distinctive
word before they will call anything a contradiction. That bar is deliberate --
it is what stops a false contradiction pulling a true fact out of recall -- but
it means a pair that contradicts in meaning while sharing almost no wording gets
through. "Lives in Madrid" against "lives in Barcelona" shares only "lives".

This asks the user's own extractor model about exactly those pairs. It is off
unless ``[dedup] semantic`` is set, it is bounded so it cannot fan out to one
call per promoted fact, and it fails open: an unreachable or confused model
leaves the lexical answer alone rather than breaking a capture.
"""

from __future__ import annotations

from collections.abc import Callable

from engram.core.dedup import _jaccard, salient_tokens
from engram.extract.client import Extractor

# Below this overlap two facts share no subject worth spending a model call on.
# Without the floor every capture would be judged against every promoted fact.
RELATED_FLOOR = 0.2

_SYSTEM = (
    "You decide whether two statements about the same person can both be true "
    "right now. Answer with exactly one word: CONTRADICTS if believing both at "
    "once would be inconsistent, COMPATIBLE otherwise. Facts about different "
    "subjects, or that merely add detail, are COMPATIBLE."
)

_REASON = "a local model reads these as mutually exclusive"


class Judge:
    """Ask the extractor model whether ``new_fact`` retires ``old_fact``.

    Returns a reason string, matching what :func:`supersede.contradicts` returns,
    or ``None`` for "no opinion" -- the model said COMPATIBLE, or it could not be
    reached at all. Both mean the caller keeps whatever the lexical pass decided.
    """

    def __init__(self, extractor: Extractor):
        self._extractor = extractor

    def __call__(self, new_fact: str, old_fact: str) -> str | None:
        try:
            answer = self._extractor.complete(_SYSTEM, f"A: {new_fact}\nB: {old_fact}")
        except Exception:
            return None  # fail open: capture stays usable without the model
        return _REASON if "CONTRADICTS" in (answer or "").upper() else None


def consult(
    new_fact: str, old_fact: str, *, judge: Callable[[str, str], str | None] | None
) -> str | None:
    """Put a pair the lexical rules cleared to the model, if one is configured.

    Only pairs that already look plausibly related are worth a call; the rest
    would spend a request to be told what the token overlap already implies.
    """
    if judge is None:
        return None
    if _jaccard(salient_tokens(new_fact), salient_tokens(old_fact)) < RELATED_FLOOR:
        return None
    return judge(new_fact, old_fact)


def judge_for(config) -> Judge | None:
    """The judge this config asks for, or ``None`` when it is switched off."""
    if not getattr(config, "dedup", None) or not config.dedup.semantic:
        return None
    return Judge(Extractor(config.extractor))
