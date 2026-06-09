"""Atomic file writes with single-step undo and an append-only audit trail.

Every mutation in engram goes through :func:`atomic_write`, which snapshots the
prior content before replacing the file in one ``os.replace`` step. The returned
token reverts exactly that write (deleting the file if the write created it).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import uuid
from pathlib import Path


def _bak_dir(root: Path) -> Path:
    return root / ".bak"


def _audit_path(root: Path) -> Path:
    return root / "audit.jsonl"


def _append_audit(root: Path, record: dict) -> None:
    with _audit_path(root).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def atomic_write(
    path: str | Path,
    content: str,
    *,
    root: str | Path | None = None,
    endpoint: str = "",
    entity_id: str = "",
) -> dict:
    """Write ``content`` to ``path`` atomically, snapshotting any prior content.

    Returns ``{"ok", "undo_token", "path"}``.
    """
    path = Path(path)
    root = Path(root) if root is not None else path.parent
    _bak_dir(root).mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)

    token = uuid.uuid4().hex[:12]
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    (_bak_dir(root) / f"{token}.bak").write_text(
        json.dumps({"path": str(path), "content": previous}), encoding="utf-8"
    )

    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    _append_audit(
        root,
        {
            "ts": dt.datetime.now(dt.UTC).isoformat(),
            "endpoint": endpoint,
            "entity_id": entity_id,
            "path": str(path),
            "undo_token": token,
            "created": previous is None,
        },
    )
    return {"ok": True, "undo_token": token, "path": str(path)}


def restore_from_bak(token: str, *, root: str | Path) -> dict:
    """Undo a write by token, deleting the file if the write had created it."""
    bak = _bak_dir(Path(root)) / f"{token}.bak"
    if not bak.exists():
        return {"ok": False, "error": "unknown undo token"}
    record = json.loads(bak.read_text(encoding="utf-8"))
    target = Path(record["path"])
    if record["content"] is None:
        if target.exists():
            target.unlink()
    else:
        target.write_text(record["content"], encoding="utf-8")
    return {"ok": True, "path": str(target)}
