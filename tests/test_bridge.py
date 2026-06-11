import datetime as dt
import json

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


def test_plan_skips_duplicates(tmp_path):
    store = MarkdownStore(tmp_path)
    store.add(Memory(fact="prefers pnpm over npm", kind=Kind.tooling, status=Status.promoted))
    store.add(Memory(fact="Prefers pnpm over npm for installs", kind=Kind.tooling))
    assert bridge.plan(store).routes[0].action == "skip"


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


def test_review_reject(tmp_path):
    store = _store_with(tmp_path, Memory(fact="VAT is 12345678X", kind=Kind.fiscal))
    bridge.apply(store, bridge.plan(store), autopromote=True)
    mid = store.list(status=Status.pending)[0].id
    review.reject(store, mid, reason="wrong")
    assert store.get(mid).status == Status.rejected
    assert store.queue_get(mid) is None
