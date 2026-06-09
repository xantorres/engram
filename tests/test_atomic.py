import json

from engram.core.atomic import atomic_write, restore_from_bak


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
    assert restore_from_bak("nope", root=tmp_path)["ok"] is False
