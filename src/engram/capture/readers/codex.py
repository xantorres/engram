"""Read Codex CLI session transcripts (``~/.codex/sessions/**/*.jsonl``).

Codex rollout files are JSONL. Until the exact shape is pinned per version, this
delegates to the tolerant generic reader, which handles the common
``role``/``content`` layouts.
"""

from __future__ import annotations

from pathlib import Path

from engram.capture.readers.base import Turn, generic_jsonl_turns


def read_session(path: str | Path) -> list[Turn]:
    return generic_jsonl_turns(path)
