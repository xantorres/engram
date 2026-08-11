"""Detect when a newly captured fact retires one already in recall.

Without this, a promoted fact is true forever. The world moves - a tool is
uninstalled, a preference changes - and the store keeps asserting the old
answer to every session that asks. That is worse than an empty store, because
an agent has no way to know the confident answer is stale.

Three signals are treated as contradiction, all deliberately narrow:

* **value divergence** - the same subject carrying a different value, whether a
  precise one (a VAT number, a date, an identifier) or a plain substitution
  where two facts share only their sentence frame and disagree on the subject
  itself. :mod:`engram.core.dedup` finds both.
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

import contextlib
import re
from collections.abc import Callable

from engram.core import atomic, dedup, semantic, tiers
from engram.core.locking import store_lock
from engram.core.schema import Memory, Status
from engram.core.store import MarkdownStore, Store

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

def contradicts(
    new_fact: str,
    old_fact: str,
    *,
    judge: Callable[[str, str], str | None] | None = None,
) -> str | None:
    """Why ``new_fact`` retires ``old_fact``, or ``None`` if they can coexist.

    ``judge`` is consulted only once every lexical rule has declined. Those rules
    require a distinctive shared word, so a pair that disagrees in meaning while
    sharing almost no wording reaches here as a false negative; the model is the
    only thing that can see it. Absent or unreachable, the lexical answer stands.
    """
    verdict = dedup.compare(new_fact, old_fact)
    if verdict == "duplicate":
        return None
    if verdict == "conflict":
        return "the same subject carries a different value"
    anchor = dedup.shared_anchor(new_fact, old_fact)
    if anchor is None:
        return semantic.consult(new_fact, old_fact, judge=judge)
    if _REMOVAL.search(new_fact):
        return f"{anchor!r} is reported gone, but this fact still asserts it"
    if _EXCLUSIVE.search(new_fact) and _EXCLUSIVE.search(old_fact):
        return f"both claim an exclusive role for {anchor!r}"
    return semantic.consult(new_fact, old_fact, judge=judge)


def flag_contradicted(
    store: Store,
    candidate: Memory,
    *,
    promoted: list[Memory] | None = None,
    retire: bool = True,
    judge: Callable[[str, str], str | None] | None = None,
) -> tuple[str, ...]:
    """Retire every promoted fact ``candidate`` contradicts; return their ids.

    A retired fact becomes ``stale``, which drops it out of recall immediately,
    and is filed for review so a human decides whether it is re-verified or
    rejected. Nothing is deleted and the newcomer is not promoted in its place -
    resolving the collision stays a human call.

    ``promoted`` lets a caller that already holds the store lock and has loaded
    the registry hand it over rather than pay for a second parse. Outside the
    lock it would be a stale snapshot, so callers that do not hold one omit it.

    ``retire`` decides whether the older fact actually leaves recall. Dropping a
    reviewed fact is a write on knowledge a human approved, so an agent may not
    do it unilaterally: with ``retire=False`` the contradiction is filed for
    review and the old fact stays promoted. Without that split, ``recall`` plus
    one ``remember("<noun> was removed")`` would let any connected agent erase
    an arbitrary memory - less authorisation than promoting one takes, which is
    backwards.

    Retiring is two writes - drop the fact out of recall, then file it for
    review - and the second failing is the dangerous half. A fact left
    ``superseded`` with no queue entry is gone from recall *and* absent from the
    review set, so nobody is ever asked about it again. The registry write is
    therefore undone if the queue write fails, mirroring the promotion bridge.
    """
    root = getattr(store, "root", None)
    lock = store_lock(root) if root is not None else contextlib.nullcontext()
    flagged: list[str] = []
    with lock:
        pool = promoted if promoted is not None else store.list(status=Status.promoted)
        for existing in pool:
            if existing.id == candidate.id:
                continue
            reason = contradicts(candidate.fact, existing.fact, judge=judge)
            if reason is None:
                continue
            if not retire:
                store.enqueue(
                    existing, dest="memory.md", reason=f"disputed by {candidate.id}: {reason}"
                )
                flagged.append(existing.id)
                continue
            retired = existing.model_copy(
                update={"status": Status.superseded, "risk_tier": tiers.TIER_CURATED}
            )
            if isinstance(store, MarkdownStore):
                _, write_result = store.update_with_token(retired)
                undo_token = write_result["undo_token"]
            else:
                store.update(retired)
                undo_token = None
            try:
                store.enqueue(
                    retired, dest="memory.md", reason=f"superseded by {candidate.id}: {reason}"
                )
            except Exception:
                if undo_token is not None and root is not None:
                    atomic.restore_from_bak(undo_token, root=root)
                raise
            flagged.append(existing.id)
    return tuple(flagged)
