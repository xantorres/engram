"""The promotion bridge: gather -> dedup -> classify -> route.

Reads pending candidates, drops duplicates, escalates conflicts and sensitive
kinds to the review queue, and auto-appends the safe remainder. Dry-run by
default: :func:`apply` writes nothing unless ``autopromote`` is explicitly on.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from engram.core import dedup, tiers
from engram.core.schema import Memory, Status
from engram.core.store import Store


@dataclass
class Route:
    memory: Memory
    action: str  # "append" | "queue" | "skip"
    reason: str = ""


@dataclass
class PromotionResult:
    routes: list[Route] = field(default_factory=list)
    applied: bool = False

    @property
    def appended(self) -> list[Route]:
        return [r for r in self.routes if r.action == "append"]

    @property
    def queued(self) -> list[Route]:
        return [r for r in self.routes if r.action == "queue"]

    @property
    def skipped(self) -> list[Route]:
        return [r for r in self.routes if r.action == "skip"]


def plan(store: Store, *, kind_allowlist: list[str] | None = None) -> PromotionResult:
    """Route pending candidates to append, queue, or skip.

    kind_allowlist: when provided, any candidate whose kind is in the list is
    appended directly (bypassing tier classification) unless a conflict exists.
    None falls back to the standard AUTO_KINDS tier logic.
    """
    promoted = store.list(status=Status.promoted)
    result = PromotionResult()
    for candidate in store.list(status=Status.pending):
        verdict, against = _dedup_against(candidate, promoted)
        if verdict == "duplicate":
            result.routes.append(Route(candidate, "skip", f"already known ({against})"))
            continue
        conflict = verdict == "conflict"
        if kind_allowlist is not None and candidate.kind.value in kind_allowlist and not conflict:
            result.routes.append(Route(candidate, "append", "kind in allowlist"))
        else:
            tier = tiers.classify(candidate.kind, conflict=conflict)
            if tiers.requires_confirm(tier):
                reason = (
                    "conflict with existing memory"
                    if conflict
                    else f"{candidate.kind.value} needs review"
                )
                result.routes.append(Route(candidate, "queue", reason))
            else:
                result.routes.append(Route(candidate, "append", "low-risk"))
    return result


def apply(
    store: Store,
    result: PromotionResult,
    *,
    autopromote: bool,
    today: dt.date | None = None,
) -> PromotionResult:
    if not autopromote:
        return result  # dry-run: report routes, change nothing
    today = today or dt.date.today()
    for route in result.routes:
        candidate = route.memory
        if route.action == "append":
            store.append_log(candidate)
            store.update(
                candidate.model_copy(
                    update={
                        "status": Status.promoted,
                        "last_verified": today,
                        "dest": "memory-log.md",
                    }
                )
            )
        elif route.action == "queue":
            escalated = candidate.model_copy(update={"risk_tier": tiers.TIER_CURATED})
            store.update(escalated)
            store.enqueue(escalated, dest="memory.md", reason=route.reason)
        elif route.action == "skip":
            store.update(candidate.model_copy(update={"status": Status.rejected}))
    result.applied = True
    return result


def run(
    store: Store,
    *,
    autopromote: bool,
    today: dt.date | None = None,
    kind_allowlist: list[str] | None = None,
) -> PromotionResult:
    return apply(
        store, plan(store, kind_allowlist=kind_allowlist), autopromote=autopromote, today=today
    )


def _dedup_against(candidate: Memory, promoted: list[Memory]) -> tuple[str, str | None]:
    for existing in promoted:
        verdict = dedup.compare(candidate.fact, existing.fact)
        if verdict in ("duplicate", "conflict"):
            return verdict, existing.id
    return "distinct", None
