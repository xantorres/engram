import datetime as dt

from engram.core.schema import LearnedBy, Memory, Status
from engram.health import doctor


def _promoted(fact, mem_id="mem-0001", **kwargs):
    return Memory(id=mem_id, fact=fact, status=Status.promoted, **kwargs)


def test_doctor_flags_stale():
    old = dt.date(2020, 1, 1)
    m = _promoted("x", decay="30d", learned_at=old, last_verified=old)
    assert "mem-0001" in doctor([m], today=dt.date(2026, 6, 9))["stale"]


def test_doctor_flags_low_confidence():
    m = _promoted("x", confidence=0.3, last_verified=dt.date(2026, 6, 1))
    assert "mem-0001" in doctor([m], today=dt.date(2026, 6, 9))["low_confidence"]


def test_doctor_flags_unverified_auto_capture():
    m = _promoted("x", learned_by=LearnedBy.harvest, learned_at=dt.date(2026, 6, 1))
    assert "mem-0001" in doctor([m], today=dt.date(2026, 6, 9))["unverified"]


def test_doctor_flags_conflicts():
    seen = dt.date(2026, 6, 1)
    a = _promoted("My VAT number is 11111111A", mem_id="mem-0001", last_verified=seen)
    b = _promoted("My VAT number is 22222222B", mem_id="mem-0002", last_verified=seen)
    report = doctor([a, b], today=dt.date(2026, 6, 9))
    assert ("mem-0001", "mem-0002") in report["conflicts"]
