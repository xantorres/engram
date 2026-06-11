"""Persistent stores for memories.

:class:`Store` is the interface the rest of engram depends on; :class:`MarkdownStore`
is the default, keeping everything as plain Markdown + YAML the user owns:

* ``memory.md`` - the ``memory.v1`` registry (YAML frontmatter) plus a generated
  human-readable body.
* ``memory-log.md`` - append-only, newest-first log of low-risk auto-captures.
* ``queue/`` - one JSON envelope per memory awaiting human review.
"""

from __future__ import annotations

import abc
import datetime as dt
import json
import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from engram.core import atomic
from engram.core.freshness import is_stale, parse_decay
from engram.core.locking import store_lock
from engram.core.schema import SCHEMA_VERSION, Memory, Status
from engram.core.text import render_safe

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_LOG_HEADER = "# Memory log\n\nNewest first. Auto-captured, low-risk facts.\n\n"
_MEM_ID_RE = re.compile(r"^mem-\d+$")


def _valid_id(memory_id: str) -> bool:
    """Only generated ``mem-<digits>`` ids may build a queue path - no traversal."""
    return bool(_MEM_ID_RE.match(memory_id))


class StoreFormatError(RuntimeError):
    """The registry exists but is unreadable; engram refuses to read or overwrite it."""


class Store(abc.ABC):
    @abc.abstractmethod
    def add(self, memory: Memory) -> Memory: ...

    @abc.abstractmethod
    def get(self, memory_id: str) -> Memory | None: ...

    @abc.abstractmethod
    def list(self, *, status: Status | None = None) -> list[Memory]: ...

    @abc.abstractmethod
    def update(self, memory: Memory) -> Memory: ...

    @abc.abstractmethod
    def append_log(self, memory: Memory) -> dict: ...

    @abc.abstractmethod
    def enqueue(
        self, memory: Memory, *, dest: str | None = None, diff: str = "", reason: str = ""
    ) -> dict: ...

    @abc.abstractmethod
    def queue_list(self) -> list[dict]: ...

    @abc.abstractmethod
    def queue_get(self, memory_id: str) -> dict | None: ...

    @abc.abstractmethod
    def resolve_queue(self, memory_id: str) -> None: ...


class MarkdownStore(Store):
    def __init__(
        self,
        root: str | Path,
        *,
        bak_keep_days: int | None = None,
        audit_max_bytes: int | None = None,
    ):
        self.root = Path(root)
        atomic.secure_dir(self.root)
        self.registry = self.root / "memory.md"
        self.archive = self.root / "archive.md"
        self.log = self.root / "memory-log.md"
        self.queue_dir = self.root / "queue"
        self._retention = atomic.RetentionPolicy(
            bak_keep_days=bak_keep_days, audit_max_bytes=audit_max_bytes
        )
        self._chmod_existing()

    def _chmod_existing(self) -> None:
        """Tighten any pre-existing store artifacts to 0700 dirs / 0600 files.

        Idempotent and cheap; runs on every init so a store created before this
        hardening (or touched by another tool) is migrated in place.
        """
        for dirpath, _dirs, filenames in os.walk(self.root):
            os.chmod(dirpath, atomic.DIR_MODE)
            for name in filenames:
                try:
                    os.chmod(Path(dirpath) / name, atomic.FILE_MODE)
                except OSError:
                    pass

    def _load_registry(self, path: Path) -> list[Memory]:
        if not path.exists():
            return []
        match = _FRONTMATTER.match(path.read_text(encoding="utf-8"))
        if not match:
            raise StoreFormatError(f"{path} is missing YAML frontmatter")
        try:
            data = yaml.safe_load(match.group(1)) or {}
            if not isinstance(data, dict):
                raise StoreFormatError(f"{path} frontmatter is not a mapping")
            return [Memory.from_item(item) for item in (data.get("items") or [])]
        except (yaml.YAMLError, ValidationError) as e:
            raise StoreFormatError(f"{path} is malformed: {e}") from e

    def _save_registry(
        self, path: Path, memories: list[Memory], *, endpoint: str, body: str
    ) -> dict:
        front = {
            "schema": SCHEMA_VERSION,
            "generated": dt.date.today().isoformat(),
            "items": [m.as_item() for m in memories],
        }
        content = (
            "---\n"
            + yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
            + "---\n\n"
            + body
        )
        return atomic.atomic_write(
            path, content, root=self.root, endpoint=endpoint, retention=self._retention
        )

    def _load(self) -> list[Memory]:
        return self._load_registry(self.registry)

    def _save(self, memories: list[Memory]) -> dict:
        return self._save_registry(
            self.registry, memories, endpoint="store/save", body=_render_body(memories)
        )

    def list_archived(self) -> list[Memory]:
        return self._load_registry(self.archive)

    @staticmethod
    def _next_id(memories: list[Memory]) -> str:
        nums = [
            int(m.id.split("-")[1])
            for m in memories
            if m.id.startswith("mem-") and m.id.split("-")[1].isdigit()
        ]
        return f"mem-{(max(nums) + 1) if nums else 1:04d}"

    def add(self, memory: Memory) -> Memory:
        with store_lock(self.root):
            memories = self._load()
            if not memory.id:
                memory = memory.model_copy(update={"id": self._next_id(memories)})
            elif not _valid_id(memory.id):
                raise ValueError(f"refusing to persist non-generated memory id: {memory.id!r}")
            memories.append(memory)
            self._save(memories)
            return memory

    def get(self, memory_id: str) -> Memory | None:
        return next((m for m in self._load() if m.id == memory_id), None)

    def list(self, *, status: Status | None = None) -> list[Memory]:
        memories = self._load()
        if status is not None:
            memories = [m for m in memories if m.status == status]
        return memories

    def update(self, memory: Memory) -> Memory:
        with store_lock(self.root):
            memories = self._load()
            for i, existing in enumerate(memories):
                if existing.id == memory.id:
                    memories[i] = memory
                    self._save(memories)
                    return memory
            raise KeyError(f"unknown memory id: {memory.id}")

    def update_with_token(self, memory: Memory) -> tuple[Memory, dict]:
        """Update a memory and return (memory, atomic_write_result).

        The atomic_write_result dict contains the undo_token for the memory.md
        write — callers that need to surface undo capability use this instead of
        update() so they receive the token from the actual file mutation.
        """
        with store_lock(self.root):
            memories = self._load()
            for i, existing in enumerate(memories):
                if existing.id == memory.id:
                    memories[i] = memory
                    write_result = self._save(memories)
                    return memory, write_result
            raise KeyError(f"unknown memory id: {memory.id}")

    def append_log(self, memory: Memory) -> dict:
        with store_lock(self.root):
            stamp = f"{dt.datetime.now(dt.UTC):%Y-%m-%dT%H:%M:%SZ}"
            line = (
                f"- {stamp} · [{memory.kind.value}] {render_safe(memory.fact)} "
                f"(conf {memory.confidence:.2f}, src {memory.source}, id {memory.id})\n"
            )
            entries = ""
            if self.log.exists():
                text = self.log.read_text(encoding="utf-8")
                entries = text[len(_LOG_HEADER) :] if text.startswith(_LOG_HEADER) else text
            return atomic.atomic_write(
                self.log,
                _LOG_HEADER + line + entries,
                root=self.root,
                endpoint="memory/append",
                entity_id=memory.id,
                retention=self._retention,
            )

    def enqueue(
        self, memory: Memory, *, dest: str | None = None, diff: str = "", reason: str = ""
    ) -> dict:
        with store_lock(self.root):
            if not _valid_id(memory.id):
                raise ValueError(f"refusing to enqueue invalid memory id: {memory.id!r}")
            atomic.secure_dir(self.queue_dir)
            payload = {"memory": memory.as_item(), "dest": dest, "diff": diff, "reason": reason}
            return atomic.atomic_write(
                self.queue_dir / f"{memory.id}.json",
                json.dumps(payload, indent=2),
                root=self.root,
                endpoint="queue/enqueue",
                entity_id=memory.id,
                retention=self._retention,
            )

    def queue_list(self) -> list[dict]:
        if not self.queue_dir.exists():
            return []
        items: list[dict] = []
        for path in sorted(self.queue_dir.glob("*.json")):
            try:
                items.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError as e:
                raise StoreFormatError(f"{path} is malformed: {e}") from e
        return items

    def queue_get(self, memory_id: str) -> dict | None:
        if not _valid_id(memory_id):
            return None
        path = self.queue_dir / f"{memory_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise StoreFormatError(f"{path} is malformed: {e}") from e

    def resolve_queue(self, memory_id: str) -> None:
        with store_lock(self.root):
            if not _valid_id(memory_id):
                return
            src = self.queue_dir / f"{memory_id}.json"
            if not src.exists():
                return
            done = atomic.secure_dir(self.queue_dir / "_done")
            src.rename(done / src.name)

    def _move_to_archive(self, keep: list[Memory], move: list[Memory]) -> None:
        """Append ``move`` to archive.md, then rewrite the registry as ``keep``.

        Archive first so the facts always survive; if the registry rewrite fails,
        roll the archive append back so a fact can't end up in both files.
        """
        archived = self._load_registry(self.archive)
        arch_result = self._save_registry(
            self.archive,
            archived + move,
            endpoint="store/archive",
            body=_render_archive_body(archived + move),
        )
        try:
            self._save(keep)
        except Exception:
            atomic.restore_from_bak(arch_result["undo_token"], root=self.root)
            raise

    def archive_rejected(self, *, dry_run: bool = False) -> dict:
        """Move ``rejected`` facts out of the live registry into archive.md."""
        with store_lock(self.root):
            memories = self._load()
            rejected = [m for m in memories if m.status == Status.rejected]
            if rejected and not dry_run:
                kept = [m for m in memories if m.status != Status.rejected]
                self._move_to_archive(kept, rejected)
            return {"archived": [m.id for m in rejected], "count": len(rejected)}

    def mark_and_archive_stale(
        self, *, today: dt.date | None = None, grace_days: int, dry_run: bool = False
    ) -> dict:
        """Write ``stale`` status to promoted facts past decay, then archive the long-dead.

        A fact stale beyond its decay horizon plus ``grace_days`` is moved to the
        archive; one that just turned stale only gets its status written so it
        drops out of recall but stays recoverable in the live registry.
        """
        today = today or dt.date.today()
        with store_lock(self.root):
            memories = self._load()
            marked_stale: list[str] = []
            keep: list[Memory] = []
            move: list[Memory] = []
            for memory in memories:
                fresh_stale = memory.status == Status.promoted and is_stale(memory, today=today)
                if fresh_stale:
                    memory = memory.model_copy(update={"status": Status.stale})
                    marked_stale.append(memory.id)
                if memory.status != Status.stale:
                    keep.append(memory)
                    continue
                base = memory.last_verified or memory.learned_at
                horizon = parse_decay(memory.decay) + dt.timedelta(days=grace_days)
                if (today - base) > horizon:
                    move.append(memory)
                else:
                    keep.append(memory)
            archived = [m.id for m in move]
            if (marked_stale or move) and not dry_run:
                self._move_to_archive(keep, move)
            return {"marked_stale": marked_stale, "archived": archived, "count": len(archived)}

    def backfill_projects(self, *, dry_run: bool = False) -> dict:
        """Populate the ``project`` field from the ``source`` string where empty.

        Idempotent: only facts with no project and a parseable ``harness:tool:project``
        source are touched, so repeated runs converge. Archives nothing.
        """
        with store_lock(self.root):
            memories = self._load()
            updated: list[str] = []
            new: list[Memory] = []
            for memory in memories:
                if not memory.project:
                    project = _project_from_source(memory.source)
                    if project:
                        memory = memory.model_copy(update={"project": project})
                        updated.append(memory.id)
                new.append(memory)
            if updated and not dry_run:
                self._save(new)
            return {"updated": updated, "count": len(updated)}

    def purge_queue_done(self, keep_days: int, *, dry_run: bool = False) -> dict:
        """Hard-delete resolved queue envelopes older than ``keep_days`` (pure cruft)."""
        with store_lock(self.root):
            done = self.queue_dir / "_done"
            if not done.exists():
                return {"purged": [], "count": 0}
            cutoff = dt.datetime.now(dt.UTC).timestamp() - keep_days * 86400
            purged: list[str] = []
            for path in sorted(done.glob("*.json")):
                try:
                    if path.stat().st_mtime < cutoff:
                        if not dry_run:
                            path.unlink()
                        purged.append(path.name)
                except OSError:
                    pass
            return {"purged": purged, "count": len(purged)}


def _project_from_source(source: str) -> str | None:
    """Recover the project slug from a ``harness:<tool>:<project>`` capture source."""
    parts = source.split(":")
    if len(parts) >= 3 and parts[0] == "harness":
        return ":".join(parts[2:]) or None
    return None


def _render_archive_body(memories: list[Memory]) -> str:
    """Human-readable listing of archived facts; the recoverable data is the frontmatter."""
    if not memories:
        return "# Archive\n\n_Empty._\n"
    by_status: dict[str, list[Memory]] = {}
    for m in memories:
        by_status.setdefault(m.status.value, []).append(m)
    lines = ["# Archive", "", "Compacted out of the live registry. Recoverable from frontmatter."]
    for status in sorted(by_status):
        lines.append(f"\n## {status}\n")
        lines.extend(f"- {render_safe(m.fact)} ({m.id})" for m in by_status[status])
    return "\n".join(lines) + "\n"


def _render_body(memories: list[Memory]) -> str:
    promoted = [m for m in memories if m.status == Status.promoted]
    if not promoted:
        return "# Memory\n\n_No promoted memories yet._\n"
    by_kind: dict[str, list[Memory]] = {}
    for m in promoted:
        by_kind.setdefault(m.kind.value, []).append(m)
    lines = ["# Memory"]
    for kind in sorted(by_kind):
        lines.append(f"\n## {kind}\n")
        lines.extend(f"- {render_safe(m.fact)}" for m in by_kind[kind])
    return "\n".join(lines) + "\n"
