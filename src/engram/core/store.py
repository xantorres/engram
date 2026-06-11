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
import re
from pathlib import Path

import yaml

from engram.core import atomic
from engram.core.schema import SCHEMA_VERSION, Memory, Status

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_LOG_HEADER = "# Memory log\n\nNewest first. Auto-captured, low-risk facts.\n\n"


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
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry = self.root / "memory.md"
        self.log = self.root / "memory-log.md"
        self.queue_dir = self.root / "queue"

    def _load(self) -> list[Memory]:
        if not self.registry.exists():
            return []
        match = _FRONTMATTER.match(self.registry.read_text(encoding="utf-8"))
        if not match:
            return []
        data = yaml.safe_load(match.group(1)) or {}
        return [Memory.from_item(item) for item in data.get("items", [])]

    def _save(self, memories: list[Memory]) -> dict:
        front = {
            "schema": SCHEMA_VERSION,
            "generated": dt.date.today().isoformat(),
            "items": [m.as_item() for m in memories],
        }
        content = (
            "---\n"
            + yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
            + "---\n\n"
            + _render_body(memories)
        )
        return atomic.atomic_write(self.registry, content, root=self.root, endpoint="store/save")

    @staticmethod
    def _next_id(memories: list[Memory]) -> str:
        nums = [
            int(m.id.split("-")[1])
            for m in memories
            if m.id.startswith("mem-") and m.id.split("-")[1].isdigit()
        ]
        return f"mem-{(max(nums) + 1) if nums else 1:04d}"

    def add(self, memory: Memory) -> Memory:
        memories = self._load()
        if not memory.id:
            memory = memory.model_copy(update={"id": self._next_id(memories)})
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
        memories = self._load()
        for i, existing in enumerate(memories):
            if existing.id == memory.id:
                memories[i] = memory
                write_result = self._save(memories)
                return memory, write_result
        raise KeyError(f"unknown memory id: {memory.id}")

    def append_log(self, memory: Memory) -> dict:
        stamp = f"{dt.datetime.now(dt.UTC):%Y-%m-%dT%H:%M:%SZ}"
        line = (
            f"- {stamp} · [{memory.kind.value}] {memory.fact} "
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
        )

    def enqueue(
        self, memory: Memory, *, dest: str | None = None, diff: str = "", reason: str = ""
    ) -> dict:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        payload = {"memory": memory.as_item(), "dest": dest, "diff": diff, "reason": reason}
        return atomic.atomic_write(
            self.queue_dir / f"{memory.id}.json",
            json.dumps(payload, indent=2),
            root=self.root,
            endpoint="queue/enqueue",
            entity_id=memory.id,
        )

    def queue_list(self) -> list[dict]:
        if not self.queue_dir.exists():
            return []
        items: list[dict] = []
        for path in sorted(self.queue_dir.glob("*.json")):
            try:
                items.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return items

    def queue_get(self, memory_id: str) -> dict | None:
        path = self.queue_dir / f"{memory_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def resolve_queue(self, memory_id: str) -> None:
        src = self.queue_dir / f"{memory_id}.json"
        if not src.exists():
            return
        done = self.queue_dir / "_done"
        done.mkdir(parents=True, exist_ok=True)
        src.rename(done / src.name)


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
        lines.extend(f"- {m.fact}" for m in by_kind[kind])
    return "\n".join(lines) + "\n"
