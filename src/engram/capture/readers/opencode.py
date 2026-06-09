"""Read opencode session transcripts.

opencode stores sessions as JSON/JSONL under its data directory. This delegates
to the tolerant generic reader for now; a format-specific reader can replace it
once the on-disk shape is pinned.
"""

from __future__ import annotations

from pathlib import Path

from engram.capture.readers.base import Turn, generic_jsonl_turns


def read_session(path: str | Path) -> list[Turn]:
    return generic_jsonl_turns(path)
