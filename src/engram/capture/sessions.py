"""Harvest memories from a harness session transcript.

Maps a harness name to its transcript reader, flattens the conversation to text,
extracts candidate facts with the configured model, pre-filters trivial or
near-duplicate candidates, and stages the survivors.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from engram.capture.readers import base, claude_code, codex, opencode
from engram.core import screen, supersede
from engram.core.schema import Memory
from engram.core.store import Store
from engram.extract.harvest import SupportsComplete, harvest

_READERS = {
    "claude-code": claude_code.read_session,
    "codex": codex.read_session,
    "opencode": opencode.read_session,
}


def _project_from_path(path: Path) -> str | None:
    """Extract the project slug from a transcript path.

    Claude Code stores sessions under ~/.claude/projects/<slug>/<id>.jsonl.
    The slug is the URL-encoded absolute project dir (e.g. -Users-alice-myapp).

    Anchors on the '.claude/projects' segment pair so that a path like
    ~/projects/personal/.claude/projects/<slug>/x.jsonl yields the correct
    slug rather than 'personal'. Returns None when no such anchor exists.
    """
    parts = path.resolve().parts
    for i, part in enumerate(parts):
        if part == ".claude" and i + 2 < len(parts) and parts[i + 1] == "projects":
            return parts[i + 2]
    return None


def supported_harnesses() -> list[str]:
    return sorted(_READERS)


def harvest_session(
    store: Store,
    path: str | Path,
    *,
    harness: str,
    extractor: SupportsComplete,
    min_confidence: float = 0.5,
    max_chars: int = 12000,
    judge: Callable[[str, str], str | None] | None = None,
) -> dict:
    """Harvest and stage facts from a single transcript session.

    Returns a dict with keys: memories, staged, skipped_dupe, skipped_trivial,
    skipped_sensitive, disputed.
    """
    path = Path(path)
    reader = _READERS.get(harness)
    if reader is None:
        raise ValueError(f"unknown harness {harness!r}; expected one of {supported_harnesses()}")

    project = _project_from_path(path)
    source = f"harness:{harness}:{project}" if project else f"harness:{harness}"

    text = base.turns_to_text(reader(path))
    if len(text) > max_chars:
        text = text[-max_chars:]  # recent turns carry the most durable signal

    candidates = harvest(text, extractor, source=source, min_confidence=min_confidence)

    # Compared against the live store plus whatever this batch already accepted,
    # so a transcript that states the same fact twice stages it once.
    existing = store.list()

    staged: list[Memory] = []
    disputed: list[str] = []
    skipped = {"duplicate": 0, "trivial": 0, "sensitive": 0}

    for candidate in candidates:
        verdict = screen.assess(candidate.fact, existing=existing)
        if not verdict.admitted:
            skipped[verdict.category] += 1
            continue
        mem = store.add(candidate.model_copy(update={"project": project}))
        staged.append(mem)
        existing.append(mem)
        disputed.extend(supersede.propose(store, mem, judge=judge).ids)

    return {
        "memories": staged,
        "staged": len(staged),
        "skipped_dupe": skipped["duplicate"],
        "skipped_trivial": skipped["trivial"],
        "skipped_sensitive": skipped["sensitive"],
        "disputed": tuple(disputed),
    }
