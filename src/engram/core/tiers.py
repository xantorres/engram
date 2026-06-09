"""Risk-tier classification - the entire write-safety model, kept deliberately small.

Three tiers govern how a memory may be written:

* **tier 1** auto-append to the memory log (low risk)
* **tier 2** mutate the registry (promotion bookkeeping)
* **tier 3** edit a curated note - requires explicit confirmation

A tier-3 write can never happen without a human saying yes.
"""

from __future__ import annotations

from engram.core.schema import Kind

TIER_AUTO_APPEND = 1
TIER_REGISTRY = 2
TIER_CURATED = 3

AUTO_KINDS = frozenset({Kind.preference, Kind.tooling, Kind.project, Kind.infra})
CURATED_KINDS = frozenset(
    {Kind.identity, Kind.fiscal, Kind.people, Kind.constraint, Kind.location, Kind.health}
)


def classify(kind: Kind, *, conflict: bool = False, edits_curated_node: bool = False) -> int:
    """Return the risk tier that governs writing a memory of ``kind``.

    Conflicts and edits that land in a curated note are always escalated to the
    review queue, regardless of how harmless the kind looks.
    """
    if conflict or edits_curated_node or kind in CURATED_KINDS:
        return TIER_CURATED
    return TIER_AUTO_APPEND


def requires_confirm(tier: int) -> bool:
    return tier >= TIER_CURATED
