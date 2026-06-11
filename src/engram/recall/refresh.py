"""Materialize recall into the user's context files.

Agents that aren't MCP-wired read a *file block* in CLAUDE.md / AGENTS.md at
session start rather than calling ``memory://recall`` live. Auto-refresh rewrites
that block the moment promoted state changes, so the materialized view never lags
behind the store the way a once-a-day snapshot does.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from engram.core.store import Store
from engram.recall.context import render_block, upsert_block


def atomic_replace(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via a temp file + rename so an interrupted run
    can never leave a half-written context file. The user's file is theirs: its mode
    is preserved and no store audit/.bak machinery is written beside it.
    """
    mode = path.stat().st_mode if path.exists() else None
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        if mode is not None:
            os.chmod(tmp, mode & 0o777)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def refresh_targets(
    store: Store, targets: list[str], *, limit: int = 30, project: str | None = None
) -> list[Path]:
    """Rewrite the engram block in each existing target; return the paths updated.

    Missing targets are skipped — refresh never creates a file the user didn't make.
    """
    block = render_block(store.list(), limit=limit, project=project)
    written: list[Path] = []
    for raw in targets:
        path = Path(raw).expanduser()
        if not path.exists():
            continue
        atomic_replace(path, upsert_block(path.read_text(encoding="utf-8"), block))
        written.append(path)
    return written
