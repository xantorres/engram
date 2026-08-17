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


def test_gc_audit_keep_days_zero_discards_same_pass_rotation(tmp_path):
    """keep_days=0 means rotate-then-discard immediately, including this pass's own archive.

    Mirrors _prune_bak's "keep_days=0 keeps nothing" for .bak snapshots: a zero
    retention window has no grace period, not even for the rotation this same
    sweep just performed.
    """
    store = MarkdownStore(tmp_path)
    audit = store.root / "audit.jsonl"
    audit.write_text('{"ts": "now"}\n' * 20, encoding="utf-8")

    gc = GarbageCollector(store, GcConfig(audit_max_bytes=50, audit_archive_keep_days=0))
    report = gc.run(GcOptions(audit=True), apply=True)

    assert report["audit"]["rotated"] is True
    assert list(store.root.glob("audit.jsonl.*")) == []
    # _rotate_audit only renames the live file aside; nothing in this sweep
    # recreates it, so it stays absent until the next atomic_write.
    assert not audit.exists()


def test_gc_audit_dry_run_survives_an_unstattable_archive(tmp_path):
    """The read-only path must be at least as forgiving as the one that deletes."""
    store = MarkdownStore(tmp_path)
    (store.root / "audit.jsonl").write_text('{"ts": "now"}\n', encoding="utf-8")
    (store.root / "audit.jsonl.20250101T000000").symlink_to(store.root / "gone")

    report = GarbageCollector(store, GcConfig(audit_archive_keep_days=14)).run(
        GcOptions(audit=True), apply=False
    )
    assert report["audit"]["archives_pruned"] == 0


def test_gc_bak_dry_run_survives_an_unstattable_snapshot(tmp_path):
    """Same forgiveness on the .bak side: a dangling symlink must not crash the preview."""
    store = MarkdownStore(tmp_path)
    bak_dir = store.root / ".bak"
    bak_dir.mkdir(parents=True, exist_ok=True)
    (bak_dir / "aaaaaaaaaaaa.bak").symlink_to(bak_dir / "gone")

    report = GarbageCollector(store, GcConfig(bak_keep_days=14)).run(
        GcOptions(bak=True), apply=False
    )
    assert report["bak"]["pruned"] == 0


# ---------------------------------------------------------------------------
# The queue is derived from the registry
# ---------------------------------------------------------------------------


def test_gc_resolves_an_envelope_the_registry_has_moved_past(tmp_path):
    """A restored fact must not keep re-listing with the reason that retired it."""
    from engram.core.schema import Memory, Status

    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="prefers pnpm", status=Status.superseded))
    store.enqueue(mem, dest="memory.md", reason="superseded by mem-9999: reported gone")
    store.update(mem.model_copy(update={"status": Status.promoted}))

    report = GarbageCollector(store, GcConfig()).run(GcOptions(queue=True), apply=True)

    assert report["queue"]["resolved"] == [mem.id]
    assert store.queue_get(mem.id) is None


def test_gc_keeps_an_envelope_that_still_matches_the_registry(tmp_path):
    from engram.core.schema import Memory, Status

    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="prefers pnpm", status=Status.pending))
    store.enqueue(mem, dest="memory.md", reason="preference needs review")

    GarbageCollector(store, GcConfig()).run(GcOptions(queue=True), apply=True)

    assert store.queue_get(mem.id) is not None


def test_gc_recovers_a_supersede_link_from_its_envelope(tmp_path):
    """Facts retired before the registry recorded a superseder are still undoable."""
    from engram.core.schema import Memory, Status

    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="uses codegraph", status=Status.superseded))
    store.enqueue(mem, dest="memory.md", reason="superseded by mem-9999: reported gone")

    report = GarbageCollector(store, GcConfig()).run(GcOptions(migrate=True), apply=True)

    assert report["supersede_links"]["updated"] == [mem.id]
    recovered = store.get(mem.id)
    assert recovered.superseded_by == "mem-9999"
    assert recovered.superseded_reason == "reported gone"
    assert recovered.superseded_at is not None
