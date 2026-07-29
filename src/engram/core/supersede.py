"""Detect when a newly captured fact retires one already in recall.

Without this, a promoted fact is true forever. The world moves - a tool is
uninstalled, a preference changes - and the store keeps asserting the old
answer to every session that asks. That is worse than an empty store, because
an agent has no way to know the confident answer is stale.

Three signals are treated as contradiction, all deliberately narrow:

* **value divergence** - the same subject carrying a different precise value
  (a VAT number, a date, an identifier). :mod:`engram.core.dedup` already finds
  these.
* **exclusive-role collision** - both facts claim a role only one thing can hold
  ("primary", "sole", "default") and they share a distinctive subject token.
* **removal** - the new fact says something is gone ("removed", "uninstalled",
  "no longer") and an older fact still speaks of it. This one needs no marker on
  the older fact, because a fact that merely *mentions* a deleted tool is
  already wrong.

Anything subtler is left alone. A false contradiction pulls a true fact out of
recall, so every rule is gated on a distinctive shared token and the resolution
is human: the retired fact goes to the review queue, it is not deleted.

Deliberately out of reach: a compound fact that is only *partly* stale. A list
of five tools where one was uninstalled is retired whole, because engram cannot
edit the user's sentence for them - it can only stop asserting it and ask.
"""

from __future__ import annotations

import re

from engram.core import dedup, tiers
from engram.core.schema import Memory, Status
from engram.core.store import Store

# Words that assert a role only one filler can occupy at a time.
_EXCLUSIVE = re.compile(
    r"(?i)\b(?:primary|sole|only|main|default|preferred|current|currently"
    r"|replaces?|replaced|replacing|supersedes?|instead)\b"
)

# A claim that something is gone. Only ever read on the *new* fact - it is the
# one reporting the change, and it retires whatever still speaks of the subject.
_REMOVAL = re.compile(
    r"(?i)\b(?:removed|removal|uninstalled|deleted|dropped|retired|deprecated"
    r"|decommissioned|no\s+longer|not\s+installed|gone)\b"
)

# The shared token that proves two claims are about the same subject.
# Short words are too common to carry that weight ("tool", "user", "file").
_MIN_ANCHOR_LEN = 6

_MARKER_TOKENS = frozenset(
    {
        "primary",
        "sole",
        "only",
        "main",
        "default",
        "preferred",
        "current",
        "currently",
        "replace",
        "replaces",
        "replaced",
        "replacing",
        "supersede",
        "supersedes",
        "instead",
    }
)


def _shared_anchor(a: str, b: str) -> str | None:
    """The longest distinctive token both facts name, if any."""
    shared = (dedup.salient_tokens(a) & dedup.salient_tokens(b)) - _MARKER_TOKENS
    candidates = [t for t in shared if len(t) >= _MIN_ANCHOR_LEN]
    return max(candidates, key=len) if candidates else None


def contradicts(new_fact: str, old_fact: str) -> str | None:
    """Why ``new_fact`` retires ``old_fact``, or ``None`` if they can coexist."""
    verdict = dedup.compare(new_fact, old_fact)
    if verdict == "duplicate":
        return None
    if verdict == "conflict":
        return "a precise value diverges"
    anchor = _shared_anchor(new_fact, old_fact)
    if anchor is None:
        return None
    if _REMOVAL.search(new_fact):
        return f"{anchor!r} is reported gone, but this fact still asserts it"
    if _EXCLUSIVE.search(new_fact) and _EXCLUSIVE.search(old_fact):
        return f"both claim an exclusive role for {anchor!r}"
    return None


def flag_contradicted(
    store: Store, candidate: Memory, *, promoted: list[Memory] | None = None
) -> tuple[str, ...]:
    """Retire every promoted fact ``candidate`` contradicts; return their ids.

    A retired fact becomes ``stale``, which drops it out of recall immediately,
    and is filed for review so a human decides whether it is re-verified or
    rejected. Nothing is deleted and the newcomer is not promoted in its place -
    resolving the collision stays a human call.

    ``promoted`` lets a caller that has already loaded the registry hand it over
    rather than pay for a second parse. Pass it only when the snapshot predates
    no other retirement, otherwise a fact can be retired twice.
    """
    flagged: list[str] = []
    for existing in promoted if promoted is not None else store.list(status=Status.promoted):
        if existing.id == candidate.id:
            continue
        reason = contradicts(candidate.fact, existing.fact)
        if reason is None:
            continue
        retired = existing.model_copy(
            update={"status": Status.stale, "risk_tier": tiers.TIER_CURATED}
        )
        store.update(retired)
        store.enqueue(retired, dest="memory.md", reason=f"superseded by {candidate.id}: {reason}")
        flagged.append(existing.id)
    return tuple(flagged)
