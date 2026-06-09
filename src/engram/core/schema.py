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
    stale = "stale"


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

    def as_item(self) -> dict:
        """A JSON-safe dict for the registry frontmatter / JSONL buffers."""
        return self.model_dump(mode="json")

    @classmethod
    def from_item(cls, item: dict) -> Memory:
        return cls.model_validate(item)
