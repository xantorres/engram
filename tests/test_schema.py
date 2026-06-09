import datetime as dt

import pytest

from engram.core.schema import Kind, LearnedBy, Memory, Status


def test_memory_defaults():
    m = Memory(fact="x")
    assert m.kind == Kind.preference
    assert m.status == Status.pending
    assert 0.0 <= m.confidence <= 1.0
    assert isinstance(m.learned_at, dt.date)


def test_roundtrip_item():
    m = Memory(
        id="mem-0001",
        fact="prefers pnpm",
        kind=Kind.tooling,
        confidence=0.8,
        learned_by=LearnedBy.harvest,
        learned_at=dt.date(2026, 6, 9),
    )
    item = m.as_item()
    assert item["kind"] == "tooling"
    assert item["learned_by"] == "harvest"
    assert item["learned_at"] == "2026-06-09"
    assert Memory.from_item(item) == m


def test_confidence_bounds():
    with pytest.raises(ValueError):
        Memory(fact="x", confidence=1.5)
