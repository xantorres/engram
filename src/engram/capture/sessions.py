"""Harvest memories from a harness session transcript.

Maps a harness name to its transcript reader, flattens the conversation to text,
extracts candidate facts with the configured model, and stages them.
"""

from __future__ import annotations

from pathlib import Path

from engram.capture.readers import base, claude_code, codex, opencode
from engram.core.schema import Memory
from engram.core.store import Store
from engram.extract.harvest import SupportsComplete, harvest

_READERS = {
    "claude-code": claude_code.read_session,
    "codex": codex.read_session,
    "opencode": opencode.read_session,
}


def supported_harnesses() -> list[str]:
    return sorted(_READERS)


def harvest_session(
    store: Store,
    path: str | Path,
    *,
    harness: str,
    extractor: SupportsComplete,
    min_confidence: float = 0.5,
) -> list[Memory]:
    reader = _READERS.get(harness)
    if reader is None:
        raise ValueError(f"unknown harness {harness!r}; expected one of {supported_harnesses()}")
    text = base.turns_to_text(reader(path))
    candidates = harvest(
        text, extractor, source=f"harness:{harness}", min_confidence=min_confidence
    )
    return [store.add(candidate) for candidate in candidates]
