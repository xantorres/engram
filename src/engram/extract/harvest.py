"""Mine durable facts about the user from raw conversation text.

The extractor is asked for strict JSON; this module is defensive about the messy
reality of small local models (code fences, stray prose) and only keeps
well-formed, confident candidates.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from engram.core.schema import Kind, LearnedBy, Memory

_SYSTEM = """You extract durable facts about the USER from a transcript.
Return STRICT JSON: {"candidates":[{"fact": string, "kind": string, "confidence": number}]}
Rules:
- One atomic assertion per fact.
- kind must be one of: preference, identity, fiscal, people, project,
  constraint, infra, tooling, health, location.
- Keep only durable facts about the user (preferences, identifiers, people,
  constraints), not task chatter.
- NEVER invent values. confidence is 0..1.
- If nothing durable is present, return {"candidates":[]}."""

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class SupportsComplete(Protocol):
    def complete(self, system: str, user: str) -> str: ...


def harvest(
    text: str,
    extractor: SupportsComplete,
    *,
    source: str = "harvest",
    min_confidence: float = 0.0,
) -> list[Memory]:
    candidates = _parse(extractor.complete(_SYSTEM, text))
    out: list[Memory] = []
    for candidate in candidates:
        fact = str(candidate.get("fact", "")).strip()
        confidence = _clamp(candidate.get("confidence", 0.5))
        if not fact or confidence < min_confidence:
            continue
        out.append(
            Memory(
                fact=fact,
                kind=_coerce_kind(candidate.get("kind", "preference")),
                confidence=confidence,
                learned_by=LearnedBy.harvest,
                source=source,
            )
        )
    return out


def _coerce_kind(value: str) -> Kind:
    try:
        return Kind(str(value).strip().lower())
    except ValueError:
        return Kind.preference


def _clamp(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5


def _parse(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
    match = _JSON_BLOCK.search(raw)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    candidates = data.get("candidates", []) if isinstance(data, dict) else []
    return [c for c in candidates if isinstance(c, dict)]
