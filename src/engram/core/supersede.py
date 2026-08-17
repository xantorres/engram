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

Each is read against the clause that makes the claim, and only counts when both
facts name the same subject - the words a personal store is *framed* in ("the
user prefers...") are shared by everything and prove nothing.

Anything subtler is left alone. A false contradiction pulls a true fact out of
recall, so three things bound the damage:

* **authority** - only a promoted fact may retire another. Capture files what a
  newcomer *would* retire on its own review envelope and touches nothing else,
  so an unreviewed harvest cannot overrule a human's decision.
* **a cap** - one fact retiring more than :data:`MAX_SUPERSEDES` others is a
  broken rule, not a discovery, and is held back whole for :mod:`engram.health`
  to report. An explicit revocation of a whole class is the exception.
* **reversibility** - the retired record keeps who retired it, when and why, so
  ``engram restore`` puts it back without anyone reading YAML by hand.

Deliberately out of reach: a compound fact that is only *partly* stale. A list
of five tools where one was uninstalled is retired whole, because engram cannot
edit the user's sentence for them - it can only stop asserting it and ask.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass

from engram.core import atomic, dedup, semantic, tiers
from engram.core.locking import store_lock
from engram.core.schema import Kind, Memory, Status
from engram.core.store import MarkdownStore, Store

# One fact correcting another is ordinary; one fact correcting a dozen is a
# matching rule that has stopped discriminating. The cap turns that from a
# silent mass retirement into something a human is told about.
MAX_SUPERSEDES = 3

# Kinds a single fact can plausibly be about at once. Harvest labels the same
# sentence about a tool `tooling` or `infra` depending on how it was phrased, so
# those two are read as one; everything else has to match. A note filed as a
# constraint has no business retiring a preference.
_INTERCHANGEABLE = frozenset({Kind.tooling, Kind.infra})

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

# A sentence states more than one thing. "prefers strict formatting, specifically
# the removal of em dashes" reports a removal of punctuation and a preference
# about formatting, and only the first is a claim that anything is gone. Reading
# the signal against its own clause is what keeps the removal about what it
# names rather than about every word standing near it.
_CLAUSE = re.compile(r"[,;:]")

# Retiring a whole class of facts at once is a real thing to say, so the cap
# yields to a fact that says it: the quantifier has to sit in the same clause as
# the removal, or "all" anywhere in a sentence would reopen the floodgate.
_QUANTIFIER = re.compile(r"(?i)\b(?:all|every|everything|any|none|entire|whole)\b")


def _comparable(a: Kind, b: Kind) -> bool:
    return a is b or {a, b} <= _INTERCHANGEABLE


def _revokes_a_class(fact: str) -> bool:
    return any(_QUANTIFIER.search(clause) for clause in _clauses(fact, _REMOVAL))


def _clauses(text: str, signal: re.Pattern[str]) -> list[str]:
    """The parts of ``text`` that carry ``signal`` - where its claim is made."""
    return [part for part in _CLAUSE.split(text) if signal.search(part)]


def _shared_subject(a: str, b: str) -> str | None:
    """The subject both texts name, or ``None`` if they only share a frame.

    One shared word is enough when it is distinctive; anything shorter has to be
    corroborated, because a short word is common enough to be coincidence.
    """
    shared = dedup.subject_tokens(a) & dedup.subject_tokens(b)
    if not shared:
        return None
    longest = max(shared, key=len)
    if len(longest) >= dedup.MIN_ANCHOR_LEN or len(shared) > 1:
        return longest
    return None


def contradicts(
    new_fact: str,
    old_fact: str,
    *,
    judge: Callable[[str, str], str | None] | None = None,
) -> str | None:
    """Why ``new_fact`` retires ``old_fact``, or ``None`` if they can coexist.

    ``judge`` is consulted only once every lexical rule has declined. Those rules
    require a shared subject, so a pair that disagrees in meaning while sharing
    almost no wording reaches here as a false negative; the model is the only
    thing that can see it. Absent or unreachable, the lexical answer stands.
    """
    verdict = dedup.compare(new_fact, old_fact)
    if verdict == "duplicate":
        return None
    if verdict == "conflict":
        return "the same subject carries a different value"
    for clause in _clauses(new_fact, _REMOVAL):
        # The whole old fact is fair game here: one that merely *mentions* a
        # thing reported gone is already wrong, wherever it mentions it.
        subject = _shared_subject(clause, old_fact)
        if subject is not None:
            return f"{subject!r} is reported gone, but this fact still asserts it"
    for clause in _clauses(new_fact, _EXCLUSIVE):
        for held in _clauses(old_fact, _EXCLUSIVE):
            subject = _shared_subject(clause, held)
            if subject is not None:
                return f"both claim an exclusive role for {subject!r}"
    return semantic.consult(new_fact, old_fact, judge=judge)


@dataclass(frozen=True)
class Verdict:
    """What a fact would retire, and whether it is allowed to.

    ``anomaly`` is set when the finding is too large to be believed. It is never
    a partial result: over the cap, nothing is retired at all, because the one
    thing a sweep of that size reliably indicates is a matching rule gone wrong.
    """

    victims: tuple[tuple[str, str], ...] = ()
    anomaly: str = ""

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(memory_id for memory_id, _ in self.victims)


def assess(
    candidate: Memory,
    promoted: list[Memory],
    *,
    judge: Callable[[str, str], str | None] | None = None,
) -> Verdict:
    """Which promoted facts ``candidate`` contradicts, and why.

    Pure: it reads facts and returns a finding. Deciding what may be written on
    the strength of it belongs to :func:`propose` and :func:`apply`.
    """
    found = [
        (existing.id, reason)
        for existing in promoted
        if existing.id != candidate.id
        and _comparable(candidate.kind, existing.kind)
        and (reason := contradicts(candidate.fact, existing.fact, judge=judge)) is not None
    ]
    if len(found) > MAX_SUPERSEDES and not _revokes_a_class(candidate.fact):
        return Verdict(
            anomaly=(
                f"{candidate.id} matches {len(found)} promoted facts, over the cap of "
                f"{MAX_SUPERSEDES}; none retired"
            )
        )
    return Verdict(victims=tuple(found))


def propose(
    store: Store,
    candidate: Memory,
    *,
    promoted: list[Memory] | None = None,
    judge: Callable[[str, str], str | None] | None = None,
) -> Verdict:
    """File what ``candidate`` would retire, as a claim on ``candidate`` itself.

    Capture cannot retire anything: a fact nobody has reviewed carries no
    authority over one a human approved, and a harvest that finds a duplicate of
    a preference must not be able to empty recall of preferences. So the finding
    is written where the decision will be made - the newcomer's own review
    envelope - and the reviewed facts are not touched at all.

    A candidate carrying such an envelope is also, deliberately, one the bridge
    will leave alone: a fact claiming to retire reviewed knowledge is exactly
    the kind that should wait for a human rather than ride an automated sweep.
    """
    root = getattr(store, "root", None)
    lock = store_lock(root) if root is not None else contextlib.nullcontext()
    with lock:
        pool = promoted if promoted is not None else store.list(status=Status.promoted)
        verdict = assess(candidate, pool, judge=judge)
        if verdict.anomaly:
            store.enqueue(candidate, dest="memory.md", reason=f"held back: {verdict.anomaly}")
        elif verdict.victims:
            store.enqueue(
                candidate,
                dest="memory.md",
                reason=f"if promoted, would supersede {', '.join(verdict.ids)}",
            )
    return verdict


def apply(
    store: Store,
    candidate: Memory,
    *,
    promoted: list[Memory] | None = None,
    judge: Callable[[str, str], str | None] | None = None,
    today: dt.date | None = None,
) -> Verdict:
    """Retire every promoted fact ``candidate`` contradicts; report which.

    A retired fact becomes ``superseded``, which drops it out of recall
    immediately, and is filed for review so a human decides whether it is
    re-verified or rejected. Nothing is deleted, and the retirement records who
    did it, so ``engram restore`` can undo the whole sweep from the registry.

    Only a promoted candidate may reach here - see :func:`propose` - which is
    why promotion is the moment this runs.

    Retiring is two writes - drop the fact out of recall, then file it for
    review - and the second failing is the dangerous half. A fact left
    ``superseded`` with no queue entry is gone from recall *and* absent from the
    review set, so nobody is ever asked about it again. The registry write is
    therefore undone if the queue write fails, mirroring the promotion bridge.
    """
    if candidate.status is not Status.promoted:
        raise ValueError(f"{candidate.id or 'candidate'} is {candidate.status.value}, not promoted")
    today = today or dt.date.today()
    root = getattr(store, "root", None)
    lock = store_lock(root) if root is not None else contextlib.nullcontext()
    with lock:
        pool = promoted if promoted is not None else store.list(status=Status.promoted)
        verdict = assess(candidate, pool, judge=judge)
        for memory_id, reason in verdict.victims:
            existing = store.get(memory_id)
            if existing is None:  # pragma: no cover - the lock rules this out
                continue
            retired = existing.model_copy(
                update={
                    "status": Status.superseded,
                    "risk_tier": tiers.TIER_CURATED,
                    "superseded_by": candidate.id,
                    "superseded_at": today,
                    "superseded_reason": reason,
                }
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
    return verdict
