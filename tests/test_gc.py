import os
import time

from engram.config import GcConfig
from engram.core.gc import GarbageCollector, GcOptions
from engram.core.store import MarkdownStore


def _backdate(path, days):
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def _seed_archives(root):
    """Two archives past a 14-day keep window, one inside it, plus the live file."""
    (root / "audit.jsonl").write_text('{"ts": "now"}\n', encoding="utf-8")
    old1 = root / "audit.jsonl.20250101T000000"
    old2 = root / "audit.jsonl.20250101T000000.1"
    recent = root / "audit.jsonl.20260801T000000"
    for path in (old1, old2, recent):
        path.write_text("x", encoding="utf-8")
    _backdate(old1, 100)
    _backdate(old2, 100)
    _backdate(recent, 5)
    return old1, old2, recent


def test_gc_audit_prunes_old_archives(tmp_path):
    store = MarkdownStore(tmp_path)
    old1, old2, recent = _seed_archives(store.root)

    gc = GarbageCollector(store, GcConfig(audit_archive_keep_days=14))
    report = gc.run(GcOptions(audit=True), apply=True)

    assert not old1.exists()
    assert not old2.exists()
    assert recent.exists()
    assert (store.root / "audit.jsonl").exists()
    assert report["audit"]["archives_pruned"] == 2


def test_gc_audit_dry_run_counts_without_deleting(tmp_path):
    store = MarkdownStore(tmp_path)
    old1, old2, recent = _seed_archives(store.root)

    gc = GarbageCollector(store, GcConfig(audit_archive_keep_days=14))
    report = gc.run(GcOptions(audit=True), apply=False)

    assert report["audit"]["archives_pruned"] == 2
    assert old1.exists()
    assert old2.exists()
    assert recent.exists()


def test_gc_audit_never_touches_live_audit_file(tmp_path):
    store = MarkdownStore(tmp_path)
    audit = store.root / "audit.jsonl"
    audit.write_text('{"ts": "now"}\n', encoding="utf-8")
    _backdate(audit, 100)

    gc = GarbageCollector(store, GcConfig(audit_archive_keep_days=14))
    report = gc.run(GcOptions(audit=True), apply=True)

    assert report["audit"]["archives_pruned"] == 0
    assert audit.exists()


def test_gc_audit_same_pass_rotation_survives_prune(tmp_path):
    """A rotation triggered in this same sweep must not be pruned right after.

    audit.jsonl's mtime reflects its last append, which can be older than the
    retention window on a quiet store. Rotating it must not hand the fresh
    archive a stale mtime that the very same prune step then deletes.
    """
    store = MarkdownStore(tmp_path)
    audit = store.root / "audit.jsonl"
    original_content = '{"ts": "now"}\n' * 5
    audit.write_text(original_content, encoding="utf-8")
    _backdate(audit, 100)

    gc = GarbageCollector(store, GcConfig(audit_max_bytes=50, audit_archive_keep_days=14))
    report = gc.run(GcOptions(audit=True), apply=True)

    archives = list(store.root.glob("audit.jsonl.*"))
    assert len(archives) == 1
    assert archives[0].read_text(encoding="utf-8") == original_content
    assert report["audit"]["rotated"] is True
    assert report["audit"]["archives_pruned"] == 0


def test_gc_audit_dry_run_survives_an_unstattable_archive(tmp_path):
    """The read-only path must be at least as forgiving as the one that deletes."""
    store = MarkdownStore(tmp_path)
    (store.root / "audit.jsonl").write_text('{"ts": "now"}\n', encoding="utf-8")
    (store.root / "audit.jsonl.20250101T000000").symlink_to(store.root / "gone")

    report = GarbageCollector(store, GcConfig(audit_archive_keep_days=14)).run(
        GcOptions(audit=True), apply=False
    )
    assert report["audit"]["archives_pruned"] == 0
