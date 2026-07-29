"""Lifecycle: a staged fact must be reachable by the verbs that resolve it.

A memory staged by ``remember`` lands pending. The review verbs (``show``,
``promote``, ``reject``) must accept it there, and the bridge must be able to
act on a subset instead of the whole pending backlog.
"""

from __future__ import annotations

import datetime as dt

from typer.testing import CliRunner

from engram.bridge import promote as bridge
from engram.bridge import review
from engram.capture.active import remember
from engram.cli.main import app
from engram.core.schema import Kind, Memory, Status
from engram.core.store import MarkdownStore

runner = CliRunner()


def _store(tmp_path, *mems: Memory) -> MarkdownStore:
    store = MarkdownStore(tmp_path)
    for mem in mems:
        store.add(mem)
    return store


# ---------------------------------------------------------------------------
# A remembered fact must be promotable
# ---------------------------------------------------------------------------


def test_promote_accepts_a_pending_memory(tmp_path):
    store = MarkdownStore(tmp_path)
    mem = remember(store, "deploys with a self-hosted runner", kind=Kind.infra)
    assert store.get(mem.id).status == Status.pending

    result = review.approve(store, mem.id, confirm=True)

    assert result["ok"], result
    assert store.get(mem.id).status == Status.promoted


def test_promote_pending_records_last_verified(tmp_path):
    store = MarkdownStore(tmp_path)
    mem = remember(store, "deploys with a self-hosted runner", kind=Kind.infra)

    review.approve(store, mem.id, confirm=True, today=dt.date(2026, 7, 29))

    assert store.get(mem.id).last_verified == dt.date(2026, 7, 29)


def test_promote_pending_still_requires_confirm(tmp_path):
    store = MarkdownStore(tmp_path)
    mem = remember(store, "deploys with a self-hosted runner", kind=Kind.infra)

    result = review.approve(store, mem.id, confirm=False)

    assert not result["ok"]
    assert "confirm" in result["error"]
    assert store.get(mem.id).status == Status.pending


def test_promote_refuses_an_already_promoted_memory(tmp_path):
    store = _store(tmp_path, Memory(fact="prefers pnpm", status=Status.promoted))
    mem_id = store.list()[0].id

    result = review.approve(store, mem_id, confirm=True)

    assert not result["ok"]
    assert "already promoted" in result["error"]


def test_promote_refuses_a_rejected_memory(tmp_path):
    store = _store(tmp_path, Memory(fact="prefers pnpm", status=Status.rejected))
    mem_id = store.list()[0].id

    result = review.approve(store, mem_id, confirm=True)

    assert not result["ok"]
    assert "rejected" in result["error"]


def test_promote_reports_unknown_id_without_mentioning_the_queue(tmp_path):
    store = MarkdownStore(tmp_path)

    result = review.approve(store, "mem-9999", confirm=True)

    assert not result["ok"]
    assert "mem-9999" in result["error"]


def test_reject_accepts_a_pending_memory(tmp_path):
    store = MarkdownStore(tmp_path)
    mem = remember(store, "deploys with a self-hosted runner", kind=Kind.infra)

    result = review.reject(store, mem.id, reason="not durable")

    assert result["ok"], result
    assert store.get(mem.id).status == Status.rejected


def test_queued_promotion_still_works(tmp_path):
    """The queue path is unchanged: dest and reason still come from the envelope."""
    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="lives in Cyprus", kind=Kind.location))
    store.enqueue(mem, dest="memory.md", reason="location needs review")

    result = review.approve(store, mem.id, confirm=True)

    assert result["ok"], result
    assert store.get(mem.id).status == Status.promoted
    assert store.queue_get(mem.id) is None


# ---------------------------------------------------------------------------
# CLI: the documented repro must work end to end
# ---------------------------------------------------------------------------


def test_cli_remember_then_show_then_promote(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_STORE", str(tmp_path / "store"))

    staged = runner.invoke(app, ["remember", "runs a self-hosted runner", "-k", "infra"])
    assert staged.exit_code == 0, staged.stdout
    mem_id = staged.stdout.split()[1].rstrip(":")

    shown = runner.invoke(app, ["show", mem_id])
    assert shown.exit_code == 0, shown.stdout
    assert mem_id in shown.stdout

    promoted = runner.invoke(app, ["promote", mem_id, "--confirm"])
    assert promoted.exit_code == 0, promoted.stdout
    assert f"promoted {mem_id}" in promoted.stdout

    listed = runner.invoke(app, ["list", "--status", "promoted"])
    assert mem_id in listed.stdout


def test_cli_show_reports_pending_status(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_STORE", str(tmp_path / "store"))
    store = MarkdownStore(tmp_path / "store")
    mem = remember(store, "runs a self-hosted runner", kind=Kind.infra)

    shown = runner.invoke(app, ["show", mem.id])

    assert shown.exit_code == 0, shown.stdout
    assert "pending" in shown.stdout


# ---------------------------------------------------------------------------
# The bridge must act on a subset
# ---------------------------------------------------------------------------


def test_plan_filters_by_id(tmp_path):
    store = _store(
        tmp_path,
        Memory(fact="prefers pnpm over npm", kind=Kind.tooling),
        Memory(fact="runs a self-hosted runner", kind=Kind.infra),
        Memory(fact="keeps notes in Obsidian", kind=Kind.tooling),
    )
    wanted = store.list()[1].id

    result = bridge.plan(store, ids=[wanted])

    assert [r.memory.id for r in result.routes] == [wanted]


def test_plan_filters_by_kind(tmp_path):
    store = _store(
        tmp_path,
        Memory(fact="prefers pnpm over npm", kind=Kind.tooling),
        Memory(fact="runs a self-hosted runner", kind=Kind.infra),
    )

    result = bridge.plan(store, kinds=["infra"])

    assert [r.memory.kind for r in result.routes] == [Kind.infra]


def test_plan_limits_the_batch(tmp_path):
    store = _store(
        tmp_path,
        *(Memory(fact=f"uses tool number {n} for builds", kind=Kind.tooling) for n in range(5)),
    )

    result = bridge.plan(store, limit=2)

    assert len(result.routes) == 2


def test_plan_limit_zero_selects_nothing(tmp_path):
    store = _store(tmp_path, Memory(fact="prefers pnpm over npm", kind=Kind.tooling))

    assert bridge.plan(store, limit=0).routes == []


def test_plan_limit_counts_candidates_it_can_act_on(tmp_path):
    """A limit must fill from unfiled candidates, not be spent on filed ones.

    Queued candidates stay pending, so they reappear in the pending list on every
    run. If the limit is applied before they are excluded, a backlog with a large
    queue hands back a full slice of already-filed facts and routes none of them,
    making every bounded run a silent no-op.
    """
    store = _store(
        tmp_path,
        *(Memory(fact=f"uses tool number {n} for builds", kind=Kind.tooling) for n in range(6)),
    )
    filed = store.list(status=Status.pending)[:4]
    for mem in filed:
        store.enqueue(mem, dest="memory.md", reason="flagged for review at capture")

    result = bridge.plan(store, limit=2)

    assert len(result.routes) == 2
    routed = {route.memory.id for route in result.routes}
    assert routed.isdisjoint({mem.id for mem in filed})


def test_plan_still_dedups_against_the_whole_promoted_set(tmp_path):
    """Filtering narrows the candidates, never the facts they are compared to."""
    store = _store(
        tmp_path,
        Memory(fact="prefers pnpm over npm for installs", status=Status.promoted),
        Memory(fact="I prefer pnpm over npm", kind=Kind.tooling),
    )
    candidate = store.list()[1].id

    result = bridge.plan(store, ids=[candidate])

    assert [r.action for r in result.routes] == ["skip"]


def test_plan_unknown_id_selects_nothing(tmp_path):
    store = _store(tmp_path, Memory(fact="prefers pnpm over npm", kind=Kind.tooling))

    assert bridge.plan(store, ids=["mem-9999"]).routes == []


def test_cli_sync_id_flag_reports_only_that_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_STORE", str(tmp_path / "store"))
    store = MarkdownStore(tmp_path / "store")
    keep = store.add(Memory(fact="runs a self-hosted runner", kind=Kind.infra))
    other = store.add(Memory(fact="keeps notes in Obsidian", kind=Kind.tooling))

    result = runner.invoke(app, ["sync", "--id", keep.id])

    assert result.exit_code == 0, result.stdout
    assert keep.id in result.stdout
    assert other.id not in result.stdout


def test_cli_sync_limit_flag_caps_the_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_STORE", str(tmp_path / "store"))
    store = MarkdownStore(tmp_path / "store")
    for n in range(4):
        store.add(Memory(fact=f"uses tool number {n} for builds", kind=Kind.tooling))

    result = runner.invoke(app, ["sync", "--limit", "2"])

    assert result.exit_code == 0, result.stdout
    assert "append=2" in result.stdout
