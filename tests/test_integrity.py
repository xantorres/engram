"""Integrity of the multi-write paths.

Every verb here mutates two places - the registry and the review queue - and a
fact that lands in one but not the other is worse than a fact that never moved:
it disappears from recall *and* from review, so nobody is ever asked about it
again. These tests kill the second write and assert the first one is undone.
"""

from __future__ import annotations

import datetime as dt

import pytest

from engram.bridge import review
from engram.capture.active import stage
from engram.core import supersede
from engram.core.schema import Kind, Memory, Status
from engram.core.store import MarkdownStore

OLD_PRIMARY = "The user currently uses codegraph (colbymchenry) as their primary tool."
NEW_PRIMARY = "codebase-memory is now the sole code-graph layer, replacing codegraph."


def _boom(*_a, **_k):
    raise OSError("simulated write failure")


# ---------------------------------------------------------------------------
# Retiring a contradicted fact
# ---------------------------------------------------------------------------


def test_supersede_rolls_back_when_the_queue_write_fails(tmp_path, monkeypatch):
    """A retired fact must never be dropped from recall without reaching review."""
    store = MarkdownStore(tmp_path)
    old = store.add(
        Memory(
            fact=OLD_PRIMARY,
            kind=Kind.tooling,
            status=Status.promoted,
            last_verified=dt.date.today(),
        )
    )
    new = store.add(Memory(fact=NEW_PRIMARY, kind=Kind.tooling))

    monkeypatch.setattr(store, "enqueue", _boom)
    with pytest.raises(OSError):
        supersede.flag_contradicted(store, new)

    # Registry unchanged: still promoted, still recallable, still reviewable later.
    assert store.get(old.id).status == Status.promoted


def test_supersede_leaves_no_orphan_when_capture_fails(tmp_path, monkeypatch):
    """The same guarantee through the capture entry point agents actually call."""
    store = MarkdownStore(tmp_path)
    old = store.add(
        Memory(
            fact=OLD_PRIMARY,
            kind=Kind.tooling,
            status=Status.promoted,
            last_verified=dt.date.today(),
        )
    )

    monkeypatch.setattr(store, "enqueue", _boom)
    with pytest.raises(OSError):
        stage(store, NEW_PRIMARY, kind=Kind.tooling)

    retired = store.get(old.id)
    assert retired.status == Status.promoted
    assert store.queue_get(old.id) is None


# ---------------------------------------------------------------------------
# The registry is the source of truth, not the queue envelope
# ---------------------------------------------------------------------------


def test_approve_promotes_the_registry_text_not_a_stale_envelope(tmp_path):
    """The README invites hand-editing frontmatter; approving must not revert it."""
    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="lives in Cyrpus", kind=Kind.location))
    store.enqueue(mem, dest="memory.md", reason="location needs review")

    # The user fixes the typo in memory.md while the item sits in the queue.
    store.update(mem.model_copy(update={"fact": "lives in Cyprus"}))

    assert review.approve(store, mem.id, confirm=True)["ok"]
    assert store.get(mem.id).fact == "lives in Cyprus"


def test_approve_refuses_a_rejected_memory_even_with_a_live_envelope(tmp_path):
    """A stale envelope must not resurrect a fact the user already threw out."""
    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="lives in Cyprus", kind=Kind.location))
    store.enqueue(mem, dest="memory.md", reason="location needs review")
    store.update(mem.model_copy(update={"status": Status.rejected}))

    result = review.approve(store, mem.id, confirm=True)

    assert not result["ok"]
    assert store.get(mem.id).status == Status.rejected


def test_approve_still_takes_the_reason_and_dest_from_the_envelope(tmp_path):
    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="lives in Cyprus", kind=Kind.location))
    store.enqueue(mem, dest="curated/identity.md", reason="location needs review")

    assert review.approve(store, mem.id, confirm=True)["ok"]
    assert store.get(mem.id).dest == "curated/identity.md"


# ---------------------------------------------------------------------------
# Rejecting
# ---------------------------------------------------------------------------


def test_reject_rolls_back_when_the_queue_resolve_fails(tmp_path, monkeypatch):
    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="lives in Cyprus", kind=Kind.location))
    store.enqueue(mem, dest="memory.md", reason="location needs review")

    monkeypatch.setattr(store, "resolve_queue", _boom)
    with pytest.raises(OSError):
        review.reject(store, mem.id)

    # Still pending and still queued - the user's decision did not half-land.
    assert store.get(mem.id).status == Status.pending
    assert store.queue_get(mem.id) is not None


def test_reject_on_a_promoted_fact_returns_an_undo_token(tmp_path):
    """Same transition as forget(), so it must carry the same way back."""
    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="prefers pnpm", status=Status.promoted))

    result = review.reject(store, mem.id, reason="no longer true")

    assert result["ok"]
    assert result["undo_token"]
    assert store.get(mem.id).status == Status.rejected


# ---------------------------------------------------------------------------
# The queue envelope must not outlive the decision it records
# ---------------------------------------------------------------------------


def test_a_decided_fact_never_keeps_a_live_queue_entry(tmp_path):
    """A rejected fact with a surviving envelope stays promotable. It must not."""
    from engram.bridge import promote as bridge

    store = MarkdownStore(tmp_path)
    known = store.add(Memory(fact="prefers pnpm over npm for installs", status=Status.promoted))
    dup = store.add(Memory(fact="I prefer pnpm over npm", kind=Kind.tooling))

    bridge.run(store, autopromote=True)

    assert store.get(dup.id).status == Status.rejected
    assert store.queue_get(dup.id) is None, "a rejected fact kept its envelope"
    assert store.get(known.id).status == Status.promoted


def test_an_already_filed_candidate_is_left_for_the_human(tmp_path):
    """Sync must not quietly overturn a decision it already handed to the user."""
    from engram.bridge import promote as bridge

    store = MarkdownStore(tmp_path)
    store.add(Memory(fact="prefers pnpm over npm for installs", status=Status.promoted))
    dup = store.add(Memory(fact="I prefer pnpm over npm", kind=Kind.tooling))
    store.enqueue(dup, dest="memory.md", reason="a human is already looking at this")

    bridge.run(store, autopromote=True)

    assert store.get(dup.id).status == Status.pending
    assert store.queue_get(dup.id)["reason"] == "a human is already looking at this"


def test_a_second_sync_does_not_overwrite_the_escalation_reason(tmp_path):
    """The reason is the envelope's only unique payload; re-running must not blank it."""
    from engram.bridge import promote as bridge

    store = MarkdownStore(tmp_path)
    store.add(Memory(fact="The user's VAT number is 12345678A", status=Status.promoted))
    store.add(Memory(fact="The user's VAT number is 99999999X", kind=Kind.fiscal))

    bridge.run(store, autopromote=True)
    queued_id = store.queue_list()[0]["memory"]["id"]
    first_reason = store.queue_get(queued_id)["reason"]

    bridge.run(store, autopromote=True)

    assert store.queue_get(queued_id)["reason"] == first_reason
    assert "conflict" in first_reason


def test_the_refusal_message_names_a_command_that_exists(tmp_path):
    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="prefers pnpm", status=Status.rejected))

    error = review.approve(store, mem.id, confirm=True)["error"]

    assert "re-stage" not in error
    assert "engram remember" in error
