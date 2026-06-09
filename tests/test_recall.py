import datetime as dt

from engram.core.schema import Kind, Memory, Status
from engram.recall.context import BLOCK_BEGIN, BLOCK_END, render_block, upsert_block
from engram.recall.rank import rank, recallable


def _promoted(fact, **kwargs):
    return Memory(fact=fact, status=Status.promoted, **kwargs)


def test_recallable_excludes_pending_and_stale():
    promoted = _promoted("a")
    pending = Memory(fact="b", status=Status.pending)
    stale = _promoted("c", decay="30d", learned_at=dt.date(2020, 1, 1))
    out = recallable([promoted, pending, stale], today=dt.date(2026, 6, 9))
    assert {m.fact for m in out} == {"a"}


def test_rank_orders_by_confidence():
    ranked = rank([_promoted("low", confidence=0.3), _promoted("high", confidence=0.9)])
    assert [m.fact for m in ranked] == ["high", "low"]


def test_rank_query_filters_and_scores():
    a = _promoted("prefers pnpm for node projects", confidence=0.5)
    b = _promoted("lives in Cyprus", confidence=0.9)
    assert [m.fact for m in rank([a, b], query="pnpm node")] == ["prefers pnpm for node projects"]


def test_render_block_groups_and_marks():
    block = render_block([_promoted("prefers pnpm", kind=Kind.tooling)])
    assert BLOCK_BEGIN in block and BLOCK_END in block
    assert "prefers pnpm" in block


def test_render_block_empty():
    assert "No memories yet" in render_block([])


def test_upsert_replaces_existing_block():
    old_block = render_block([_promoted("old fact", kind=Kind.tooling)])
    doc = "# My AGENTS.md\n\n" + old_block + "\n## Other\n"
    merged = upsert_block(doc, render_block([_promoted("new fact", kind=Kind.tooling)]))
    assert "new fact" in merged and "old fact" not in merged
    assert "# My AGENTS.md" in merged and "## Other" in merged
    assert merged.count(BLOCK_BEGIN) == 1
