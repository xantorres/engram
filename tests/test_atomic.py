import json
import stat

from engram.core.atomic import atomic_write, restore_from_bak


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
