import datetime as dt
import json
from unittest.mock import patch

import pytest

from engram.bridge import promote as bridge
from engram.bridge import review
from engram.core.schema import Kind, Memory, Status
from engram.core.store import MarkdownStore


def _store_with(tmp_path, *mems):
    store = MarkdownStore(tmp_path)
    for mem in mems:
        store.add(mem)
    return store


def test_plan_routes_by_kind(tmp_path):
    store = _store_with(
        tmp_path,
        Memory(fact="prefers pnpm", kind=Kind.tooling),
        Memory(fact="VAT is 12345678X", kind=Kind.fiscal),
    )
    actions = {r.memory.fact: r.action for r in bridge.plan(store).routes}
    assert actions["prefers pnpm"] == "append"
    assert actions["VAT is 12345678X"] == "queue"


def test_plan_allowlist_ignores_curated_kinds(tmp_path):
    store = _store_with(tmp_path, Memory(fact="VAT is 12345678X", kind=Kind.fiscal))
    result = bridge.plan(store, kind_allowlist=["fiscal"])
    assert result.routes[0].action == "queue"


def test_plan_queues_capture_flagged_candidate(tmp_path):
    store = _store_with(tmp_path, Memory(fact="something odd", kind=Kind.preference, risk_tier=3))
    result = bridge.plan(store)
    assert result.routes[0].action == "queue"
    assert result.routes[0].reason == "flagged for review at capture"


def test_plan_skips_cross_project_conflict(tmp_path):
    store = _store_with(
        tmp_path,
        Memory(
            fact="My VAT number is 11111111A",
            kind=Kind.tooling,
            status=Status.promoted,
            project="proj-a",
        ),
        Memory(
            fact="My VAT number is 22222222B",
            kind=Kind.tooling,
            status=Status.pending,
            project="proj-b",
        ),
    )
    routes = {r.memory.fact: r for r in bridge.plan(store).routes}
    assert routes["My VAT number is 22222222B"].action == "append"


def test_plan_flags_same_project_conflict(tmp_path):
    store = _store_with(
        tmp_path,
        Memory(
            fact="My VAT number is 11111111A",
            kind=Kind.tooling,
            status=Status.promoted,
            project="proj-a",
        ),
        Memory(
            fact="My VAT number is 22222222B",
            kind=Kind.tooling,
            status=Status.pending,
            project="proj-a",
        ),
    )
    routes = {r.memory.fact: r for r in bridge.plan(store).routes}
    assert routes["My VAT number is 22222222B"].action == "queue"


def test_plan_skips_duplicates(tmp_path):
    store = MarkdownStore(tmp_path)
    store.add(Memory(fact="prefers pnpm over npm", kind=Kind.tooling, status=Status.promoted))
    store.add(Memory(fact="Prefers pnpm over npm for installs", kind=Kind.tooling))
    assert bridge.plan(store).routes[0].action == "skip"


def test_plan_skips_envelope_whose_memory_lacks_id(tmp_path):
    """A ``memory`` dict present but missing ``id`` must not blow up already_filed."""
    store = _store_with(tmp_path, Memory(fact="prefers pnpm", kind=Kind.tooling))
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(exist_ok=True)
    (queue_dir / "malformed.json").write_text(json.dumps({"memory": {"fact": "no id here"}}))

    result = bridge.plan(store)

    assert [r.memory.fact for r in result.routes] == ["prefers pnpm"]


def test_dry_run_changes_nothing(tmp_path):
    store = _store_with(tmp_path, Memory(fact="prefers pnpm", kind=Kind.tooling))
    bridge.apply(store, bridge.plan(store), autopromote=False)
    assert store.list(status=Status.promoted) == []
    assert not (tmp_path / "memory-log.md").exists()


def test_apply_appends_low_risk(tmp_path):
    store = _store_with(tmp_path, Memory(fact="prefers pnpm", kind=Kind.tooling))
    bridge.apply(store, bridge.plan(store), autopromote=True, today=dt.date(2026, 6, 9))
    promoted = store.list(status=Status.promoted)
    assert len(promoted) == 1
    assert promoted[0].dest == "memory-log.md"
    assert (tmp_path / "memory-log.md").exists()


def test_append_rolls_back_log_when_registry_update_fails(tmp_path):
    store = _store_with(tmp_path, Memory(fact="prefers pnpm", kind=Kind.tooling))
    result = bridge.plan(store)
    with patch.object(store, "update", side_effect=RuntimeError("registry boom")):
        with pytest.raises(RuntimeError):
            bridge.apply(store, result, autopromote=True, today=dt.date(2026, 6, 9))
    log = tmp_path / "memory-log.md"
    assert not (log.exists() and "prefers pnpm" in log.read_text())


def test_queue_rolls_back_registry_when_enqueue_fails(tmp_path):
    store = _store_with(tmp_path, Memory(fact="VAT is 12345678X", kind=Kind.fiscal))
    result = bridge.plan(store)
    with patch.object(store, "enqueue", side_effect=RuntimeError("queue boom")):
        with pytest.raises(RuntimeError):
            bridge.apply(store, result, autopromote=True, today=dt.date(2026, 6, 9))
    # Registry reverted: not left escalated-but-unqueued (invisible to review).
    mem = store.list()[0]
    assert mem.status == Status.pending
    assert mem.risk_tier == 1
    assert store.queue_get(mem.id) is None


def test_apply_queues_curated(tmp_path):
    store = _store_with(tmp_path, Memory(fact="VAT is 12345678X", kind=Kind.fiscal))
    bridge.apply(store, bridge.plan(store), autopromote=True)
    assert store.list(status=Status.pending)
    assert len(store.queue_list()) == 1


def test_review_approve_requires_confirm(tmp_path):
    store = _store_with(tmp_path, Memory(fact="VAT is 12345678X", kind=Kind.fiscal))
    bridge.apply(store, bridge.plan(store), autopromote=True)
    mid = store.list(status=Status.pending)[0].id

    assert review.approve(store, mid, confirm=False)["ok"] is False
    assert store.get(mid).status == Status.pending

    assert review.approve(store, mid, confirm=True, today=dt.date(2026, 6, 9))["ok"]
    assert store.get(mid).status == Status.promoted
    assert store.queue_get(mid) is None


def test_approve_does_not_append_to_log(tmp_path):
    store = _store_with(tmp_path, Memory(fact="VAT is 99999999X", kind=Kind.fiscal))
    bridge.apply(store, bridge.plan(store), autopromote=True)
    mid = store.list(status=Status.pending)[0].id

    assert review.approve(store, mid, confirm=True, today=dt.date(2026, 6, 9))["ok"]

    log = tmp_path / "memory-log.md"
    assert not (log.exists() and "VAT is 99999999X" in log.read_text())
    assert "VAT is 99999999X" in (tmp_path / "memory.md").read_text()
    assert store.queue_get(mid) is None

    endpoints = [
        json.loads(line)["endpoint"]
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    ]
    assert "review/approve" in endpoints


def test_approve_rolls_back_registry_when_resolve_fails(tmp_path):
    store = _store_with(tmp_path, Memory(fact="VAT is 12345678X", kind=Kind.fiscal))
    bridge.apply(store, bridge.plan(store), autopromote=True)
    mid = store.list(status=Status.pending)[0].id
    with patch.object(store, "resolve_queue", side_effect=RuntimeError("resolve boom")):
        with pytest.raises(RuntimeError):
            review.approve(store, mid, confirm=True, today=dt.date(2026, 6, 9))
    # Registry reverted, queue item still active — no promoted-yet-still-queued split.
    assert store.get(mid).status == Status.pending
    assert store.queue_get(mid) is not None


def test_review_reject(tmp_path):
    store = _store_with(tmp_path, Memory(fact="VAT is 12345678X", kind=Kind.fiscal))
    bridge.apply(store, bridge.plan(store), autopromote=True)
    mid = store.list(status=Status.pending)[0].id
    review.reject(store, mid, reason="wrong")
    assert store.get(mid).status == Status.rejected
    assert store.queue_get(mid) is None


def test_pending_reviews_includes_unenveloped_awaiting(tmp_path):
    store = _store_with(
        tmp_path,
        Memory(fact="prefers pnpm", kind=Kind.tooling),
        Memory(fact="prefers uv", kind=Kind.tooling),
    )
    enqueued = store.add(Memory(fact="VAT is 12345678X", kind=Kind.fiscal))
    store.enqueue(enqueued, dest="memory.md", reason="needs review")

    items = review.pending_reviews(store)

    assert len(items) == 3
    by_id = {item["memory"]["id"]: item for item in items}
    assert by_id[enqueued.id]["envelope"] is True
    assert by_id[enqueued.id]["dest"] == "memory.md"
    assert by_id[enqueued.id]["reason"] == "needs review"
    plain_ids = [mid for mid in by_id if mid != enqueued.id]
    assert len(plain_ids) == 2
    for mid in plain_ids:
        assert by_id[mid]["envelope"] is False
        assert by_id[mid]["reason"] == "pending"


def test_pending_reviews_surfaces_a_live_envelope_on_a_promoted_fact(tmp_path):
    # An envelope filed against a promoted fact - a dispute a human wants to
    # look at - must surface it. This is the dispute case, not an orphan.
    store = MarkdownStore(tmp_path)
    old = store.add(
        Memory(
            fact="The user prefers TypeScript for all new backend services.",
            status=Status.promoted,
            last_verified=dt.date.today(),
        )
    )
    store.enqueue(old, dest="memory.md", reason="disputed by mem-9999: reported gone")

    envelope = store.queue_get(old.id)
    by_id = {item["memory"]["id"]: item for item in review.pending_reviews(store)}

    assert store.get(old.id).status == Status.promoted
    assert old.id in by_id
    assert by_id[old.id]["envelope"] is True
    assert by_id[old.id]["reason"] == envelope["reason"]


def test_pending_reviews_skips_envelope_missing_memory_field(tmp_path):
    """An envelope with no ``memory`` field at all - distinct from corrupt JSON,
    which raises StoreFormatError and is covered separately in test_store.py."""
    store = _store_with(tmp_path, Memory(fact="prefers pnpm", kind=Kind.tooling))
    normal = store.list()[0]

    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(exist_ok=True)
    (queue_dir / "malformed.json").write_text(json.dumps({"reason": "junk"}))

    items = review.pending_reviews(store)

    assert [item["memory"]["id"] for item in items] == [normal.id]


def test_pending_reviews_keeps_an_envelope_whose_fact_left_the_registry(tmp_path):
    """An envelope with no matching registry entry still surfaces for review
    instead of vanishing silently - e.g. a memory hand-removed from memory.md.

    Archival itself (archive_rejected, mark_and_archive_stale, dedup_promoted)
    now resolves the envelope of every memory it moves out of the registry, so
    it can no longer manufacture this state; this exercises the id-mismatch
    case directly instead.
    """
    store = MarkdownStore(tmp_path)
    orphaned = Memory(
        id="mem-0001", fact="VAT is 12345678X", kind=Kind.fiscal, status=Status.rejected
    )
    store.enqueue(orphaned, dest="memory.md", reason="curated kind needs review")

    assert store.get(orphaned.id) is None
    assert store.queue_get(orphaned.id) is not None
    items = review.pending_reviews(store)
    assert [item["memory"]["id"] for item in items] == [orphaned.id]
    assert items[0]["orphan"] is True


def test_enveloped_row_shows_the_registry_fact_not_the_queued_snapshot(tmp_path):
    """The registry is the source of truth the user hand-edits; the listing must match it."""
    store = MarkdownStore(tmp_path)
    memory = store.add(Memory(fact="prefers pnpm", kind=Kind.tooling))
    store.enqueue(memory, dest="memory.md", reason="needs review")
    store.update(memory.model_copy(update={"fact": "prefers bun"}))

    items = review.pending_reviews(store)
    assert [item["memory"]["fact"] for item in items] == ["prefers bun"]


def test_pending_reviews_preserves_registry_order(tmp_path):
    """Emitted order must track the registry, not group by envelope or reverse it."""
    store = MarkdownStore(tmp_path)
    a = store.add(Memory(fact="a pending fact", kind=Kind.tooling))
    b = store.add(Memory(fact="b pending fact", kind=Kind.fiscal))
    store.enqueue(b, dest="memory.md", reason="needs review")
    c = store.add(Memory(fact="c pending fact", kind=Kind.tooling))
    d = store.add(Memory(fact="d pending fact", kind=Kind.fiscal))
    store.enqueue(d, dest="memory.md", reason="needs review")

    items = review.pending_reviews(store)

    assert [item["memory"]["id"] for item in items] == [a.id, b.id, c.id, d.id]


def test_pending_reviews_skips_envelope_whose_memory_lacks_id(tmp_path):
    """A ``memory`` dict present but missing ``id`` must be skipped, not raise."""
    store = _store_with(tmp_path, Memory(fact="prefers pnpm", kind=Kind.tooling))
    normal = store.list()[0]

    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(exist_ok=True)
    (queue_dir / "malformed.json").write_text(
        json.dumps({"memory": {"fact": "no id here"}, "reason": "junk"})
    )

    items = review.pending_reviews(store)

    assert [item["memory"]["id"] for item in items] == [normal.id]


def test_pending_reviews_holds_the_lock_across_both_reads(tmp_path, monkeypatch):
    """queue_list() and store.list() must be read under one lock hold - a writer
    that completes between the two reads must not be able to produce a stale join.

    A full in-process approve() between the reads would exercise the race
    directly, but re-enters store_lock's RLock on the same thread and cannot
    prove the *absence* of a matching flock the way it would across processes.
    Instead this asserts structurally that the lock is held (depth == 1, this
    thread's reentrancy counter for the store's root) at the moment of each
    read - the observable that changes from 0 to 1 when the fix wraps both
    reads in a single ``with store_lock(store.root):`` block.
    """
    from engram.core import locking

    store = MarkdownStore(tmp_path)
    store.add(Memory(fact="prefers pnpm", kind=Kind.tooling))
    root_lock = locking._root_lock(str(store.root.resolve()))

    depths_during_read: list[int] = []
    orig_queue_list = store.queue_list
    orig_list = store.list

    def spy_queue_list(*args, **kwargs):
        depths_during_read.append(root_lock.depth)
        return orig_queue_list(*args, **kwargs)

    def spy_list(*args, **kwargs):
        depths_during_read.append(root_lock.depth)
        return orig_list(*args, **kwargs)

    monkeypatch.setattr(store, "queue_list", spy_queue_list)
    monkeypatch.setattr(store, "list", spy_list)

    review.pending_reviews(store)

    assert depths_during_read == [1, 1]
