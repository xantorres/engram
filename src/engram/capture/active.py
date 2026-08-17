"""Active capture - the ``remember`` entry point.

An agent (or the user) deliberately stages a single fact. It lands as a pending
candidate; the bridge decides later whether it auto-logs or needs review, and
``engram promote`` can approve it straight from there.

Capture is where the two irreversible mistakes are cheapest to prevent: letting
a credential map into the store, and letting a new fact sit beside the older one
it contradicts. Both are handled here so every caller inherits them.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass

from engram.core import screen, supersede, tiers
from engram.core.locking import store_lock
from engram.core.schema import Kind, LearnedBy, Memory, Status
from engram.core.store import Store
from engram.core.text import clean_fact


@dataclass(frozen=True)
class CaptureResult:
    """What staging a fact did, including what it displaced."""

    memory: Memory | None = None
    admitted: bool = True
    reason: str = ""
    category: str = ""
    duplicate_of: str | None = None
    #: Facts this one contradicts. Capture never retires them - see supersede.
    disputed: tuple[str, ...] = ()
    #: Set when the contradiction is too large to believe; nothing was proposed.
    anomaly: str = ""


class CaptureRefused(RuntimeError):
    """A screen rejected the fact; ``result`` carries why."""

    def __init__(self, result: CaptureResult):
        super().__init__(result.reason)
        self.result = result


def stage(
    store: Store,
    fact: str,
    *,
    kind: Kind = Kind.preference,
    confidence: float = 0.6,
    source: str = "tool:remember",
    force: bool = False,
    judge: Callable[[str, str], str | None] | None = None,
) -> CaptureResult:
    """Screen, stage, and file whatever the new fact contradicts.

    ``force`` overrides the duplicate screen. It also overrides the credential
    screen, but such a fact is staged at tier 3 so it can never auto-promote:
    the user may decide engram is the right home for it, and still has to say so
    a second time at review.

    A contradiction found here is only ever *proposed*. Whoever is capturing -
    the user at a terminal or an agent through MCP - is staging a fact nobody
    has reviewed yet, and a fact nobody has reviewed does not get to overrule
    one somebody did. It becomes real when the newcomer is promoted.

    Screening and staging run under one lock. They are a single
    read-modify-write over the registry, and interleaving them with another
    writer would let a fact be judged against a store that no longer exists by
    the time it is written.
    """
    cleaned = clean_fact(fact)
    root = getattr(store, "root", None)
    lock = store_lock(root) if root is not None else contextlib.nullcontext()
    with lock:
        known = store.list()
        verdict = screen.assess(cleaned, existing=known, check_trivial=False)
        if not verdict.admitted and not force:
            return CaptureResult(
                admitted=False,
                reason=verdict.reason,
                category=verdict.category,
                duplicate_of=verdict.duplicate_of,
            )

        risk_tier = tiers.TIER_CURATED if verdict.category == "sensitive" else tiers.classify(kind)
        memory = store.add(
            Memory(
                fact=cleaned,
                kind=kind,
                confidence=confidence,
                learned_by=LearnedBy.remember,
                source=source,
                risk_tier=risk_tier,
            )
        )
        # Safe to reuse rather than re-parse: the lock is held, so nothing has
        # written since the snapshot was taken.
        promoted = [m for m in known if m.status == Status.promoted]
        verdict = supersede.propose(store, memory, promoted=promoted, judge=judge)
        return CaptureResult(memory=memory, disputed=verdict.ids, anomaly=verdict.anomaly)


def remember(
    store: Store,
    fact: str,
    *,
    kind: Kind = Kind.preference,
    confidence: float = 0.6,
    source: str = "tool:remember",
    force: bool = False,
) -> Memory:
    """Stage a fact, raising :class:`CaptureRefused` if a screen rejects it."""
    result = stage(store, fact, kind=kind, confidence=confidence, source=source, force=force)
    if not result.admitted:
        raise CaptureRefused(result)
    return result.memory
