"""The promotion bridge: gather -> dedup -> classify -> route.

Reads pending candidates, drops duplicates, escalates conflicts and sensitive
kinds to the review queue, and auto-appends the safe remainder. Dry-run by
default: :func:`apply` writes nothing unless ``autopromote`` is explicitly on.
"""

from __future__ import annotations

import contextlib
import datetime as dt
from dataclasses import dataclass, field

from engram.core import atomic, dedup, tiers
from engram.core.locking import store_lock
from engram.core.schema import Memory, Status
from engram.core.store import MarkdownStore, Store


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


def _select(
    candidates: list[Memory],
    *,
    ids: list[str] | None,
    kinds: list[str] | None,
    limit: int | None,
) -> list[Memory]:
    if ids is not None:
        wanted = set(ids)
        candidates = [c for c in candidates if c.id in wanted]
    if kinds is not None:
        wanted_kinds = set(kinds)
        candidates = [c for c in candidates if c.kind.value in wanted_kinds]
    if limit is not None:
        candidates = candidates[: max(limit, 0)]
    return candidates


def plan(
    store: Store,
    *,
    kind_allowlist: list[str] | None = None,
    ids: list[str] | None = None,
    kinds: list[str] | None = None,
    limit: int | None = None,
) -> PromotionResult:
    """Route pending candidates to append, queue, or skip.

    kind_allowlist: when provided, a candidate whose non-curated kind is in the
    list is appended directly (bypassing tier classification) unless a conflict
    exists. Curated kinds always fall through to classification and queue for
    review, regardless of the allowlist. None falls back to standard tier logic.

    ids / kinds / limit narrow which pending candidates are considered, so a
    large backlog can be worked through a few facts at a time instead of in one
    irreversible pass. They narrow the candidates only - every candidate is
    still compared against the whole promoted set, so a filtered run can never
    miss a duplicate or a conflict that an unfiltered run would have caught.
    """
    promoted = store.list(status=Status.promoted)
    result = PromotionResult()
    selected = _select(store.list(status=Status.pending), ids=ids, kinds=kinds, limit=limit)
    for candidate in selected:
        verdict, against = _dedup_against(candidate, promoted)
        if verdict == "duplicate":
            result.routes.append(Route(candidate, "skip", f"already known ({against})"))
            continue
        conflict = verdict == "conflict"
        if candidate.risk_tier >= tiers.TIER_CURATED:
            result.routes.append(Route(candidate, "queue", "flagged for review at capture"))
        elif (
            kind_allowlist is not None
            and candidate.kind.value in kind_allowlist
            and candidate.kind not in tiers.CURATED_KINDS
            and candidate.risk_tier < tiers.TIER_CURATED
            and not conflict
        ):
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
    root = getattr(store, "root", None)
    lock = store_lock(root) if root is not None else contextlib.nullcontext()
    with lock:
        for route in result.routes:
            candidate = route.memory
            if route.action == "append":
                # Log first, then flip the registry. If the registry write fails,
                # undo the log append so recall and the log can't diverge.
                log_result = store.append_log(candidate)
                try:
                    store.update(
                        candidate.model_copy(
                            update={
                                "status": Status.promoted,
                                "last_verified": today,
                                "dest": "memory-log.md",
                            }
                        )
                    )
                except Exception:
                    if root is not None:
                        atomic.restore_from_bak(log_result["undo_token"], root=root)
                    raise
            elif route.action == "queue":
                # Escalate the registry, then file the review item. If the queue
                # write fails, undo the escalation so a sensitive fact is never
                # left pending-but-invisible to review.
                escalated = candidate.model_copy(update={"risk_tier": tiers.TIER_CURATED})
                if isinstance(store, MarkdownStore):
                    _, write_result = store.update_with_token(escalated)
                    undo_token = write_result["undo_token"]
                else:
                    store.update(escalated)
                    undo_token = None
                try:
                    store.enqueue(escalated, dest="memory.md", reason=route.reason)
                except Exception:
                    if undo_token is not None and root is not None:
                        atomic.restore_from_bak(undo_token, root=root)
                    raise
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
    ids: list[str] | None = None,
    kinds: list[str] | None = None,
    limit: int | None = None,
) -> PromotionResult:
    return apply(
        store,
        plan(store, kind_allowlist=kind_allowlist, ids=ids, kinds=kinds, limit=limit),
        autopromote=autopromote,
        today=today,
    )


def _dedup_against(candidate: Memory, promoted: list[Memory]) -> tuple[str, str | None]:
    for existing in promoted:
        verdict = dedup.compare(candidate.fact, existing.fact)
        if verdict in ("duplicate", "conflict"):
            return verdict, existing.id
    return "distinct", None
