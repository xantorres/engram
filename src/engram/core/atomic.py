"""Atomic file writes with single-step undo and an append-only audit trail.

Every mutation in engram goes through :func:`atomic_write`, which snapshots the
prior content before replacing the file in one ``os.replace`` step. The returned
token reverts exactly that write (deleting the file if the write created it).
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import re
import tempfile
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path

from engram.core.locking import store_lock

_TOKEN_RE = re.compile(r"^[0-9a-f]{12}$")

DIR_MODE = 0o700
FILE_MODE = 0o600

# Real snapshots are prior file contents, so low-MB scale; 64 MiB is generous
# headroom without leaving the cap effectively unbounded.
_MAX_SNAPSHOT_BYTES = 64 * 1024**2
_GUNZIP_STEP = 1024 * 1024


@dataclass(frozen=True)
class RetentionPolicy:
    """Opportunistic cleanup thresholds applied on each retained write.

    ``None`` disables a knob, so the default policy is a no-op and callers opt in
    by passing one built from :class:`engram.config.GcConfig`.
    """

    bak_keep_days: int | None = None
    audit_max_bytes: int | None = None


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


def _prune_bak(root: str | Path, keep_days: int | None) -> int:
    """Delete ``.bak`` snapshots older than ``keep_days`` by mtime; return the count.

    ``keep_days=0`` keeps nothing; ``None`` is a no-op. Cheap and opportunistic —
    callers run it inside ``store_lock``.
    """
    if keep_days is None:
        return 0
    bak_dir = _bak_dir(Path(root))
    if not bak_dir.exists():
        return 0
    cutoff = dt.datetime.now(dt.UTC).timestamp() - keep_days * 86400
    removed = 0
    for snapshot in bak_dir.glob("*.bak"):
        try:
            if snapshot.stat().st_mtime < cutoff:
                snapshot.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def _rotate_audit(root: str | Path, max_bytes: int | None) -> Path | None:
    """Rename ``audit.jsonl`` aside once it exceeds ``max_bytes``; return the new path.

    The rotated file keeps its 0600 mode (rename preserves it) and a unique
    timestamp suffix so concurrent rotations never clobber an earlier archive.
    """
    if max_bytes is None:
        return None
    audit = _audit_path(Path(root))
    if not audit.exists() or audit.stat().st_size <= max_bytes:
        return None
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")
    target = audit.with_name(f"audit.jsonl.{stamp}")
    suffix = 0
    while target.exists():
        suffix += 1
        target = audit.with_name(f"audit.jsonl.{stamp}.{suffix}")
    audit.rename(target)
    # Rename preserves mtime (= last append time), which can already be older
    # than a retention cutoff; stamp "now" so a positive keep-days window
    # doesn't treat the archive as eligible in the very sweep that just
    # created it. keep_days=0 has no such window - its cutoff is "now" too,
    # so the fresh stamp does not save it, matching _prune_bak's "0 keeps
    # nothing" for .bak snapshots.
    os.utime(target, None)
    return target


def _append_audit(root: Path, record: dict, *, max_bytes: int | None = None) -> None:
    _rotate_audit(root, max_bytes)
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
    retention: RetentionPolicy | None = None,
) -> dict:
    """Write ``content`` to ``path`` atomically, snapshotting any prior content.

    Returns ``{"ok", "undo_token", "path"}``. When ``retention`` is given, the
    audit log is rotated past its cap and stale ``.bak`` snapshots are pruned.
    """
    path = Path(path)
    root = Path(root) if root is not None else path.parent
    retention = retention or RetentionPolicy()
    secure_dir(_bak_dir(root))
    secure_dir(path.parent)
    # Prune before snapshotting. Run afterwards, a zero-day window collects the
    # snapshot this very write just took and the returned token restores nothing.
    _prune_bak(root, retention.bak_keep_days)

    token = uuid.uuid4().hex[:12]
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    bak = _bak_dir(root) / f"{token}.bak"
    payload = json.dumps({"path": str(path), "content": previous}).encode("utf-8")
    bak.write_bytes(gzip.compress(payload))
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
        max_bytes=retention.audit_max_bytes,
    )
    return {"ok": True, "undo_token": token, "path": str(path)}


def _bounded_gunzip(raw: bytes, limit: int) -> bytes | None:
    """Inflate a gzip payload, refusing to let the output grow past ``limit``.

    gzip.decompress() has no size cap, so a small malicious or corrupted
    snapshot can force an allocation orders of magnitude larger than any real
    prior file content. Feeding a streaming decompressor with a capped
    max_length keeps peak memory near ``limit`` regardless of the compression
    ratio, and this returns ``None`` instead of materializing the rest once
    that cap is crossed.
    """
    decompressor = zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)
    out = bytearray()
    for start in range(0, len(raw), _GUNZIP_STEP):
        pending = raw[start : start + _GUNZIP_STEP]
        while pending:
            out += decompressor.decompress(pending, _GUNZIP_STEP)
            if len(out) > limit:
                return None
            pending = decompressor.unconsumed_tail
    out += decompressor.flush()
    if len(out) > limit:
        return None
    if not decompressor.eof:
        raise EOFError("compressed snapshot ended before the end-of-stream marker was reached")
    return bytes(out)


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
        raw = bak.read_bytes()
        # Gzip magic bytes distinguish current snapshots from legacy plain-JSON
        # ones written before compression; both must keep restoring.
        is_gzip = raw[:2] == b"\x1f\x8b"
        try:
            if is_gzip:
                inflated = _bounded_gunzip(raw, _MAX_SNAPSHOT_BYTES)
                if inflated is None:
                    return {"ok": False, "error": "snapshot exceeds size cap"}
                text = inflated.decode("utf-8")
            else:
                text = raw.decode("utf-8")
            record = json.loads(text)
            recorded_path, content = record["path"], record["content"]
        except (OSError, EOFError, UnicodeDecodeError, ValueError, zlib.error, TypeError, KeyError):
            # A snapshot truncated by a full disk or a killed process is a failed
            # undo, not a crash; every other failure here returns an error dict.
            return {"ok": False, "error": "unreadable snapshot"}
        target = Path(recorded_path).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return {"ok": False, "error": "refusing to restore outside store root"}
        if content is None:
            if target.exists():
                target.unlink()
        else:
            target.write_text(content, encoding="utf-8")
        secure_file(target)
        return {"ok": True, "path": str(target)}
