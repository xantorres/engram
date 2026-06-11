"""Advisory lock serialising writes to one store root - across threads and processes.

Every mutation in :mod:`engram.core.store` takes :func:`store_lock` so two writers
can't interleave a read-modify-write on the same Markdown store. Two layers:

* a per-root ``threading.RLock`` serialises threads within the process (the MCP
  server dispatches sync tools off its event loop, so concurrent ``remember``
  calls land on different threads) while staying reentrant for one thread;
* an ``flock`` on a per-root ``.lock`` file serialises separate processes.

``flock`` is tied to the open file description, not the process: a second
``flock(LOCK_EX)`` on a different fd self-deadlocks within one process. So the OS
lock is taken once, on the outermost entry of the owning thread, and released
when that thread fully unwinds.
"""

from __future__ import annotations

import fcntl
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_LOCK_MODE = 0o600
_registry_guard = threading.Lock()


class _RootLock:
    def __init__(self) -> None:
        self.tlock = threading.RLock()  # serialises threads, reentrant per thread
        self.fd: int | None = None
        self.depth = 0  # OS-lock depth for the thread currently holding tlock


_locks: dict[str, _RootLock] = {}


def _root_lock(key: str) -> _RootLock:
    with _registry_guard:
        rl = _locks.get(key)
        if rl is None:
            rl = _RootLock()
            _locks[key] = rl
        return rl


@contextmanager
def store_lock(root: str | os.PathLike) -> Iterator[None]:
    """Hold an exclusive lock on ``root`` for the duration of the block."""
    root = Path(root)
    rl = _root_lock(str(root.resolve()))
    rl.tlock.acquire()
    try:
        if rl.depth == 0:
            lock_path = root / ".lock"
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, _LOCK_MODE)
            os.chmod(lock_path, _LOCK_MODE)
            fcntl.flock(fd, fcntl.LOCK_EX)
            rl.fd = fd
        rl.depth += 1
        yield
    finally:
        rl.depth -= 1
        if rl.depth == 0 and rl.fd is not None:
            fcntl.flock(rl.fd, fcntl.LOCK_UN)
            os.close(rl.fd)
            rl.fd = None
        rl.tlock.release()
