"""Harvest memories from a harness session transcript.

Maps a harness name to its transcript reader, flattens the conversation to text,
extracts candidate facts with the configured model, pre-filters trivial or
near-duplicate candidates, and stages the survivors.
"""

from __future__ import annotations

import re
from pathlib import Path

from engram.capture.readers import base, claude_code, codex, opencode
from engram.core import dedup
from engram.core.schema import Memory, Status
from engram.core.store import Store
from engram.extract.harvest import SupportsComplete, harvest

_READERS = {
    "claude-code": claude_code.read_session,
    "codex": codex.read_session,
    "opencode": opencode.read_session,
}

# ---------------------------------------------------------------------------
# Triviality heuristics — tunable constants with rationale comments
# ---------------------------------------------------------------------------

# Fewer than this many characters → fact carries no durable signal
_TRIVIAL_MIN_CHARS = 20

# Bare filesystem paths (home-dir rooted) that echo where the agent ran
_TRIVIAL_PATH_RE = re.compile(r"^(?:/Users/|/home/|~/)[\w/.\-]+$")

# "User identifier is <X>" / "Username is <X>" patterns extracted by naive prompts.
# Two filter branches:
#   1. Explicit identity prefix (identifier/identity/id/username/login/name) → filter any token.
#   2. Bare "user is <token>" → filter only when token contains at least one digit, @, or .
#      (unambiguous username signal).  All-alpha tokens — including hyphenated role names like
#      "the-project-lead" — survive as legitimate role-description facts.
_TRIVIAL_USER_ID_RE = re.compile(
    r"(?i)^(?:"
    # branch 1: explicit identity keyword in prefix → filter any single token
    r"user\s+(?:identifier|identity|id|username|login|name)\s+is\s+\S+"
    r"|"
    # branch 2: bare "user is <token>" — filter only when token has digit, @ or .
    r"user\s+is\s+[^\s]*(?:\d|@|\.)[^\s]*"
    r"|"
    # branch 3: bare "username is <anything>"
    r"username\s+is\s+\S+"
    r")$"
)

# Generic OS/platform declarations that add no actionable preference
_TRIVIAL_PLATFORM_RE = re.compile(r"(?i)^uses?\s+(macOS|Mac|Windows|Linux|Ubuntu)\s*$")


def _is_trivial(fact: str) -> bool:
    f = fact.strip()
    return (
        len(f) < _TRIVIAL_MIN_CHARS
        or bool(_TRIVIAL_PATH_RE.match(f))
        or bool(_TRIVIAL_USER_ID_RE.match(f))
        or bool(_TRIVIAL_PLATFORM_RE.match(f))
    )


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
) -> dict:
    """Harvest and stage facts from a single transcript session.

    Returns a dict with keys: memories, staged, skipped_dupe, skipped_trivial.
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

    # Dedup against pending and promoted facts only; rejected facts must not
    # block re-ingestion — a forgotten fact can be re-learned on the next harvest.
    existing_facts = [
        m.fact for m in store.list() if m.status != Status.rejected
    ]

    staged: list[Memory] = []
    skipped_dupe = 0
    skipped_trivial = 0

    for candidate in candidates:
        if _is_trivial(candidate.fact):
            skipped_trivial += 1
            continue

        # Check against existing store facts
        is_dup = any(
            dedup.compare(candidate.fact, ef) == "duplicate" for ef in existing_facts
        )
        if not is_dup:
            # Check against facts already accepted in this batch
            is_dup = any(
                dedup.compare(candidate.fact, s.fact) == "duplicate" for s in staged
            )

        if is_dup:
            skipped_dupe += 1
            continue

        mem = store.add(candidate)
        staged.append(mem)
        existing_facts.append(mem.fact)

    return {
        "memories": staged,
        "staged": len(staged),
        "skipped_dupe": skipped_dupe,
        "skipped_trivial": skipped_trivial,
    }
