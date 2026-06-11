"""Advisory cross-process lock serialising writes to one store root.

A single OS-level ``flock`` guards every mutation so two engram processes (an MCP
server and a CLI run, say) can't interleave a read-modify-write on the same
Markdown store. The lock is advisory: only code that takes :func:`store_lock`
is serialised - which is every mutator in :mod:`engram.core.store`.

Reentrancy is process-wide: a second ``flock(LOCK_EX)`` on a different fd for a
file this process already holds self-deadlocks on macOS/BSD, so the OS lock is
refcounted per resolved root and only touched at depth 0.
"""

from __future__ import annotations

import fcntl
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_LOCK_MODE = 0o600
_guard = threading.Lock()
_held: dict[str, tuple[int, int]] = {}  # resolved root -> (fd, depth)


@contextmanager
def store_lock(root: str | os.PathLike) -> Iterator[None]:
    """Hold an exclusive advisory lock on ``root`` for the duration of the block."""
    key = str(Path(root).resolve())
    with _guard:
        entry = _held.get(key)
        if entry is None:
            lock_path = Path(root) / ".lock"
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, _LOCK_MODE)
            os.chmod(lock_path, _LOCK_MODE)
            fcntl.flock(fd, fcntl.LOCK_EX)
            _held[key] = (fd, 1)
        else:
            fd, depth = entry
            _held[key] = (fd, depth + 1)
    try:
        yield
    finally:
        with _guard:
            fd, depth = _held[key]
            if depth > 1:
                _held[key] = (fd, depth - 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
                del _held[key]
