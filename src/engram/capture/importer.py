"""Import existing memories from a directory of frontmatter-markdown files.

Each file's YAML `description` (or first body line) becomes the fact; an explicit
`kind`/`type` plus sensitive-keyword detection decides the kind. Everything lands
as a pending candidate, so an import never silently writes into recall.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from engram.core.schema import Kind, LearnedBy, Memory
from engram.core.store import Store
from engram.core.text import clean_fact

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_SKIP = {"memory.md", "index.md", "readme.md"}

_TYPE_MAP = {
    "feedback": Kind.preference,
    "user": Kind.preference,
    "project": Kind.project,
    "reference": Kind.infra,
    "tooling": Kind.tooling,
}

# Checked before the type hint so sensitive facts are never mislabeled as a
# low-risk preference (which would let them auto-append instead of being reviewed).
_SENSITIVE = [
    (
        re.compile(r"\b(vat|tax|fiscal|iban|invoice|tic|nhr|non-dom|autonom|dividend)\b", re.I),
        Kind.fiscal,
    ),
    (re.compile(r"\b(passport|dni|nie|born|birth|nationality)\b", re.I), Kind.identity),
    (
        re.compile(r"\b(wife|husband|partner|girlfriend|boyfriend|brother|sister|parent)\b", re.I),
        Kind.people,
    ),
]


def infer_kind(fact: str, hint: str | None = None) -> Kind:
    for pattern, kind in _SENSITIVE:
        if pattern.search(fact):
            return kind
    if hint:
        token = str(hint).strip().lower()
        try:
            return Kind(token)
        except ValueError:
            return _TYPE_MAP.get(token, Kind.preference)
    return Kind.preference


def import_markdown_dir(
    store: Store, directory: str | Path, *, confidence: float = 0.7
) -> list[Memory]:
    staged: list[Memory] = []
    for path in sorted(Path(directory).glob("*.md")):
        if path.name.lower() in _SKIP:
            continue
        front, body = _split(path.read_text(encoding="utf-8"))
        fact = clean_fact(front.get("description") or _first_line(body))
        if not fact:
            continue
        hint = front.get("kind") or front.get("type")
        staged.append(
            store.add(
                Memory(
                    fact=fact,
                    kind=infer_kind(fact, hint),
                    confidence=confidence,
                    learned_by=LearnedBy.imported,
                    source=f"import:{path.name}",
                )
            )
        )
    return staged


def _split(text: str) -> tuple[dict, str]:
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    front = yaml.safe_load(match.group(1)) or {}
    return (front if isinstance(front, dict) else {}), match.group(2)


def _first_line(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""
