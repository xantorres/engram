from engram.core import tiers
from engram.core.schema import Kind


def test_auto_kinds_are_tier_one():
    for kind in (Kind.preference, Kind.tooling, Kind.project, Kind.infra):
        assert tiers.classify(kind) == tiers.TIER_AUTO_APPEND


def test_curated_kinds_are_tier_three():
    curated = (
        Kind.identity,
        Kind.fiscal,
        Kind.people,
        Kind.constraint,
        Kind.location,
        Kind.health,
    )
    for kind in curated:
        assert tiers.classify(kind) == tiers.TIER_CURATED


def test_conflict_forces_curated():
    assert tiers.classify(Kind.preference, conflict=True) == tiers.TIER_CURATED


def test_curated_node_edit_forces_curated():
    assert tiers.classify(Kind.tooling, edits_curated_node=True) == tiers.TIER_CURATED


def test_requires_confirm_only_tier_three():
    assert tiers.requires_confirm(tiers.TIER_CURATED)
    assert not tiers.requires_confirm(tiers.TIER_REGISTRY)
    assert not tiers.requires_confirm(tiers.TIER_AUTO_APPEND)
