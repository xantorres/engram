import datetime as dt

import pytest

from engram.core.freshness import is_stale, parse_decay
from engram.core.schema import Memory


def test_parse_decay_units():
    assert parse_decay("180d") == dt.timedelta(days=180)
    assert parse_decay("2w") == dt.timedelta(days=14)
    assert parse_decay("1y") == dt.timedelta(days=365)


def test_parse_decay_invalid():
    with pytest.raises(ValueError):
        parse_decay("soon")


def test_is_stale_uses_last_verified():
    m = Memory(
        fact="x",
        decay="180d",
        learned_at=dt.date(2020, 1, 1),
        last_verified=dt.date(2026, 6, 1),
    )
    assert is_stale(m, today=dt.date(2026, 6, 2)) is False
    assert is_stale(m, today=dt.date(2027, 1, 1)) is True


def test_is_stale_falls_back_to_learned_at():
    m = Memory(fact="x", decay="30d", learned_at=dt.date(2026, 1, 1))
    assert is_stale(m, today=dt.date(2026, 6, 1)) is True
