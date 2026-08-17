"""The memory schema (``memory.v1``).

A :class:`Memory` is one atomic assertion about the user, plus the provenance
and lifecycle metadata the rest of the system reasons about.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field

SCHEMA_VERSION = "memory.v1"


class Kind(StrEnum):
    preference = "preference"
    identity = "identity"
    fiscal = "fiscal"
    people = "people"
    project = "project"
    constraint = "constraint"
    infra = "infra"
    tooling = "tooling"
    health = "health"
    location = "location"


class Status(StrEnum):
    pending = "pending"
    promoted = "promoted"
    rejected = "rejected"
    # Went unconfirmed past its decay horizon. Time did this; re-verifying is routine.
    stale = "stale"
    # Actively contradicted by a newer fact, and out of recall until a human rules.
    # Distinct from `stale` on purpose: a sweep that retires the merely forgotten
    # must not also retire the disputed.
    superseded = "superseded"


class LearnedBy(StrEnum):
    harvest = "harvest"
    remember = "remember"
    manual = "manual"
    imported = "import"


def _today() -> dt.date:
    return dt.date.today()


class Memory(BaseModel):
    id: str = ""
    fact: str
    kind: Kind = Kind.preference
    source: str = "manual"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    learned_by: LearnedBy = LearnedBy.manual
    learned_at: dt.date = Field(default_factory=_today)
    last_verified: dt.date | None = None
    decay: str = "180d"
    status: Status = Status.pending
    risk_tier: int = Field(default=1, ge=1, le=3)
    dest: str | None = None
    project: str | None = None
    # Who retired this fact, when, and why. Written together with the
    # ``superseded`` status so the registry alone can answer "why did this leave
    # recall" - and so ``restore`` can find every fact one superseder took.
    superseded_by: str | None = None
    superseded_at: dt.date | None = None
    superseded_reason: str | None = None

    def as_item(self) -> dict:
        """A JSON-safe dict for the registry frontmatter / JSONL buffers.

        Supersession keys are omitted while unset: they apply to a handful of
        facts, and writing three empty lines onto every other one buys nothing
        but a longer file to read and hand-edit.
        """
        item = self.model_dump(mode="json")
        return {k: v for k, v in item.items() if not (k.startswith("superseded_") and v is None)}

    @classmethod
    def from_item(cls, item: dict) -> Memory:
        return cls.model_validate(item)
