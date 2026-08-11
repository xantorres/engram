import gzip
import json
import os
import stat
import time

from engram.core.atomic import (
    RetentionPolicy,
    _bak_dir,
    _prune_bak,
    _rotate_audit,
    atomic_write,
    restore_from_bak,
    secure_dir,
)


def test_atomic_write_sets_0600_on_target_bak_and_audit(tmp_path):
    res = atomic_write(tmp_path / "f.md", "x", root=tmp_path, endpoint="t", entity_id="m")
    target = tmp_path / "f.md"
    bak = tmp_path / ".bak" / f"{res['undo_token']}.bak"
    audit = tmp_path / "audit.jsonl"
    for path in (target, bak, audit):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_then_undo_deletes_created_file(tmp_path):
    target = tmp_path / "f.md"
    res = atomic_write(target, "hello", root=tmp_path)
    assert res["ok"] and target.read_text() == "hello"

    restore_from_bak(res["undo_token"], root=tmp_path)
    assert not target.exists()


def test_write_then_undo_restores_previous(tmp_path):
    target = tmp_path / "f.md"
    target.write_text("original")
    res = atomic_write(target, "changed", root=tmp_path)
    assert target.read_text() == "changed"

    restore_from_bak(res["undo_token"], root=tmp_path)
    assert target.read_text() == "original"


def test_audit_record_written(tmp_path):
    atomic_write(tmp_path / "a.md", "x", root=tmp_path, endpoint="t/test", entity_id="mem-1")
    record = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[-1])
    assert record["endpoint"] == "t/test"
    assert record["entity_id"] == "mem-1"
    assert record["created"] is True


def test_unknown_undo_token_is_safe(tmp_path):
    assert restore_from_bak("0123456789ab", root=tmp_path)["ok"] is False


def test_restore_rejects_malformed_token(tmp_path):
    assert restore_from_bak("../../etc/passwd", root=tmp_path)["ok"] is False


def test_prune_bak_removes_old_keeps_recent(tmp_path):
    bak = secure_dir(_bak_dir(tmp_path))
    old = bak / "aaaaaaaaaaaa.bak"
    recent = bak / "bbbbbbbbbbbb.bak"
    old.write_text("{}")
    recent.write_text("{}")
    twenty_days_ago = time.time() - 20 * 86400
    os.utime(old, (twenty_days_ago, twenty_days_ago))

    removed = _prune_bak(tmp_path, 14)

    assert removed == 1
    assert not old.exists()
    assert recent.exists()


def test_prune_bak_none_is_noop(tmp_path):
    bak = secure_dir(_bak_dir(tmp_path))
    (bak / "aaaaaaaaaaaa.bak").write_text("{}")
    assert _prune_bak(tmp_path, None) == 0
    assert (bak / "aaaaaaaaaaaa.bak").exists()


def test_atomic_write_prunes_old_bak(tmp_path):
    bak = secure_dir(_bak_dir(tmp_path))
    stale = bak / "cccccccccccc.bak"
    stale.write_text("{}")
    long_ago = time.time() - 30 * 86400
    os.utime(stale, (long_ago, long_ago))

    atomic_write(
        tmp_path / "f.md", "x", root=tmp_path, retention=RetentionPolicy(bak_keep_days=14)
    )

    assert not stale.exists()


def test_rotate_audit_renames_past_cap(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text("x" * 100)
    audit.chmod(0o600)

    _rotate_audit(tmp_path, 50)

    assert not audit.exists()
    rotated = list(tmp_path.glob("audit.jsonl.*"))
    assert len(rotated) == 1
    assert stat.S_IMODE(rotated[0].stat().st_mode) == 0o600


def test_rotate_audit_under_cap_is_noop(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text("x" * 10)
    _rotate_audit(tmp_path, 50)
    assert audit.exists()
    assert not list(tmp_path.glob("audit.jsonl.*"))


def test_atomic_write_rotates_audit_then_appends_fresh(tmp_path):
    # Seed an over-cap audit log, then a retained write rotates it and starts fresh.
    audit = tmp_path / "audit.jsonl"
    audit.write_text("x" * 200)
    audit.chmod(0o600)

    atomic_write(
        tmp_path / "f.md", "x", root=tmp_path, retention=RetentionPolicy(audit_max_bytes=50)
    )

    rotated = list(tmp_path.glob("audit.jsonl.*"))
    assert len(rotated) == 1
    record = json.loads(audit.read_text().splitlines()[-1])
    assert record["path"].endswith("f.md")


def test_restore_refuses_path_outside_root(tmp_path):
    from engram.core import atomic

    atomic.secure_dir(atomic._bak_dir(tmp_path))
    token = "abcdef012345"
    outside = tmp_path.parent / "engram_escape.txt"
    (atomic._bak_dir(tmp_path) / f"{token}.bak").write_text(
        json.dumps({"path": str(outside), "content": "ESCAPED"}), encoding="utf-8"
    )
    res = restore_from_bak(token, root=tmp_path)
    assert res["ok"] is False
    assert not outside.exists()


def test_bak_snapshot_is_gzipped_and_smaller(tmp_path):
    target = tmp_path / "f.md"
    prior = "line one of many\n" * 5000
    target.write_text(prior)

    res = atomic_write(target, "new content", root=tmp_path)

    bak = tmp_path / ".bak" / f"{res['undo_token']}.bak"
    raw = bak.read_bytes()
    assert raw[:2] == b"\x1f\x8b"
    assert len(raw) < len(prior) // 2


def test_legacy_plain_json_bak_still_restores(tmp_path):
    secure_dir(_bak_dir(tmp_path))
    target = tmp_path / "f.md"
    token = "abcdef012345"
    (_bak_dir(tmp_path) / f"{token}.bak").write_text(
        json.dumps({"path": str(target), "content": "old text"}), encoding="utf-8"
    )

    res = restore_from_bak(token, root=tmp_path)

    assert res["ok"] is True
    assert target.read_text() == "old text"


def test_undo_token_survives_zero_day_bak_retention(tmp_path):
    """Retention must never collect the snapshot the current write depends on."""
    target = tmp_path / "memory.md"
    target.write_text("original\n", encoding="utf-8")
    res = atomic_write(
        target, "replaced\n", root=tmp_path, retention=RetentionPolicy(bak_keep_days=0)
    )
    assert restore_from_bak(res["undo_token"], root=tmp_path)["ok"] is True
    assert target.read_text(encoding="utf-8") == "original\n"


def test_restore_reports_an_unreadable_snapshot_instead_of_raising(tmp_path):
    """Every other failure returns an error dict; a corrupt snapshot must too."""
    bak_dir = secure_dir(_bak_dir(tmp_path))
    (bak_dir / "aaaaaaaaaaaa.bak").write_bytes(b"")
    (bak_dir / "bbbbbbbbbbbb.bak").write_bytes(gzip.compress(b'{"path": "x"}')[:10])
    (bak_dir / "cccccccccccc.bak").write_bytes(gzip.compress(b'{"no_path": 1}'))
    for token in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"):
        assert restore_from_bak(token, root=tmp_path) == {
            "ok": False,
            "error": "unreadable snapshot",
        }
