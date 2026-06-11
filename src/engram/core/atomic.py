"""Atomic file writes with single-step undo and an append-only audit trail.

Every mutation in engram goes through :func:`atomic_write`, which snapshots the
prior content before replacing the file in one ``os.replace`` step. The returned
token reverts exactly that write (deleting the file if the write created it).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

from engram.core.locking import store_lock

_TOKEN_RE = re.compile(r"^[0-9a-f]{12}$")

DIR_MODE = 0o700
FILE_MODE = 0o600


def secure_dir(path: str | Path) -> Path:
    """Create ``path`` if needed and tighten it to owner-only (0700)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(DIR_MODE)
    return path


def secure_file(path: str | Path) -> None:
    """Tighten an existing file to owner-only (0600); no-op if it is absent."""
    path = Path(path)
    if path.exists():
        path.chmod(FILE_MODE)


def _bak_dir(root: Path) -> Path:
    return root / ".bak"


def _audit_path(root: Path) -> Path:
    return root / "audit.jsonl"


def _append_audit(root: Path, record: dict) -> None:
    fd = os.open(_audit_path(root), os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
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
    secure_dir(_bak_dir(root))
    secure_dir(path.parent)

    token = uuid.uuid4().hex[:12]
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    bak = _bak_dir(root) / f"{token}.bak"
    bak.write_text(
        json.dumps({"path": str(path), "content": previous}), encoding="utf-8"
    )
    bak.chmod(FILE_MODE)

    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        os.chmod(path, FILE_MODE)
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
    """Undo a write by token, deleting the file if the write had created it.

    The token must be a literal 12-hex handle and the recorded target must resolve
    inside ``root``; a tampered backup can't redirect the write to an arbitrary path.
    """
    if not _TOKEN_RE.match(token):
        return {"ok": False, "error": "invalid undo token"}
    root = Path(root)
    with store_lock(root):
        bak = _bak_dir(root) / f"{token}.bak"
        if not bak.exists():
            return {"ok": False, "error": "unknown undo token"}
        record = json.loads(bak.read_text(encoding="utf-8"))
        target = Path(record["path"]).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return {"ok": False, "error": "refusing to restore outside store root"}
        if record["content"] is None:
            if target.exists():
                target.unlink()
        else:
            target.write_text(record["content"], encoding="utf-8")
        secure_file(target)
        return {"ok": True, "path": str(target)}
