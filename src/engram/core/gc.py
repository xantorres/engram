"""Garbage collection orchestration.

A thin coordinator over the store's own compaction and the atomic layer's cruft
retention — it decides *which* hygiene steps to run and reports counts, but holds
no policy of its own. Dry-run by default, like the promotion bridge.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from engram.config import GcConfig
from engram.core import atomic
from engram.core.store import MarkdownStore


@dataclass
class GcOptions:
    bak: bool = False
    audit: bool = False
    rejected: bool = False
    stale: bool = False
    dedup: bool = False
    queue: bool = False
    migrate: bool = False

    def any(self) -> bool:
        return any(
            (self.bak, self.audit, self.rejected, self.stale, self.dedup, self.queue, self.migrate)
        )

    def resolved(self) -> GcOptions:
        """No explicit step selected means a full sweep."""
        if self.any():
            return self
        return GcOptions(
            bak=True, audit=True, rejected=True, stale=True, dedup=True, queue=True, migrate=True
        )


class GarbageCollector:
    def __init__(self, store: MarkdownStore, config: GcConfig):
        self.store = store
        self.config = config

    def run(self, options: GcOptions, *, apply: bool) -> dict:
        """Run the selected steps; ``apply=False`` reports counts without mutating."""
        dry = not apply
        report: dict[str, dict] = {}
        # Backfill first so project scoping informs dedup and conflict checks.
        if options.migrate:
            report["migrate"] = self.store.backfill_projects(dry_run=dry)
        if options.rejected:
            report["rejected"] = self.store.archive_rejected(dry_run=dry)
        if options.stale:
            report["stale"] = self.store.mark_and_archive_stale(
                grace_days=self.config.stale_grace_days, dry_run=dry
            )
        if options.dedup:
            report["dedup"] = self.store.dedup_promoted(dry_run=dry)
        if options.queue:
            report["queue"] = self.store.purge_queue_done(
                self.config.queue_done_keep_days, dry_run=dry
            )
        if options.bak:
            report["bak"] = self._bak(apply)
        if options.audit:
            report["audit"] = self._audit(apply)
        return report

    def _bak(self, apply: bool) -> dict:
        if apply:
            return {"pruned": atomic._prune_bak(self.store.root, self.config.bak_keep_days)}
        bak_dir = self.store.root / ".bak"
        if not bak_dir.exists():
            return {"pruned": 0}
        cutoff = dt.datetime.now(dt.UTC).timestamp() - self.config.bak_keep_days * 86400
        count = sum(1 for p in bak_dir.glob("*.bak") if p.stat().st_mtime < cutoff)
        return {"pruned": count}

    def _audit(self, apply: bool) -> dict:
        audit = self.store.root / "audit.jsonl"
        over = audit.exists() and audit.stat().st_size > self.config.audit_max_bytes
        if apply and over:
            atomic._rotate_audit(self.store.root, self.config.audit_max_bytes)
        return {"rotated": over}


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def collect_stats(store: MarkdownStore) -> dict:
    """Read-only snapshot of store health: counts, sizes, and fact age range."""
    memories = store.list()
    dates = [m.learned_at for m in memories]
    root = store.root
    audit = root / "audit.jsonl"
    return {
        "total": len(memories),
        "archived": len(store.list_archived()),
        "by_status": dict(Counter(m.status.value for m in memories)),
        "by_kind": dict(Counter(m.kind.value for m in memories)),
        "by_project": dict(Counter(m.project or "—" for m in memories)),
        "store_bytes": _dir_size(root),
        "bak_bytes": _dir_size(root / ".bak"),
        "audit_bytes": audit.stat().st_size if audit.exists() else 0,
        "oldest": min(dates).isoformat() if dates else None,
        "newest": max(dates).isoformat() if dates else None,
    }
