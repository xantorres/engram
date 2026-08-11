"""Supersession: a newer fact must retire the older one it contradicts.

The failure this guards against is a promoted fact that stays recall-able after
the world moved on, so every future session is served a confident lie.
"""

from __future__ import annotations

import datetime as dt

from engram.capture.active import remember
from engram.core import supersede
from engram.core.freshness import effective_confidence
from engram.core.schema import Kind, Memory, Status
from engram.core.store import MarkdownStore
from engram.health import doctor
from engram.recall.rank import rank

# The real pair that went undetected: a tool named as primary, then replaced.
OLD_PRIMARY = "The user currently uses codegraph (colbymchenry) as their primary tool."
NEW_PRIMARY = (
    "Code-graph tooling: codebase-memory is the sole code-graph layer, "
    "replacing codegraph as of 2026-07-28."
)


def _promoted(fact: str, **kw) -> Memory:
    return Memory(fact=fact, status=Status.promoted, last_verified=dt.date(2026, 6, 26), **kw)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_two_exclusive_claims_about_the_same_thing_contradict():
    assert supersede.contradicts(NEW_PRIMARY, OLD_PRIMARY) is not None


def test_value_divergence_still_counts_as_a_contradiction():
    assert supersede.contradicts("My VAT number is 99999999X", "My VAT number is 12345678A")


def test_a_restatement_is_not_a_contradiction():
    assert (
        supersede.contradicts("I prefer pnpm over npm", "Prefers pnpm over npm for installs")
        is None
    )


def test_unrelated_facts_do_not_contradict():
    assert (
        supersede.contradicts("The user's primary editor is neovim", "The user lives in Cyprus")
        is None
    )


def test_an_exclusive_claim_needs_a_shared_subject_to_contradict():
    """Two 'primary' claims about different things coexist happily."""
    assert (
        supersede.contradicts(
            "The user's primary editor is neovim",
            "The user's primary package manager is pnpm",
        )
        is None
    )


def test_a_removal_retires_a_fact_asserting_the_thing_is_present():
    """The other real miss: nothing claims a role, but one says the tool is gone."""
    assert (
        supersede.contradicts(
            "Both graphify and codegraph (colbymchenry) were removed on 2026-07-28.",
            "The user's system includes a codegraph.",
        )
        is not None
    )


def test_a_removal_retires_a_list_that_still_names_the_removed_tool():
    assert (
        supersede.contradicts(
            "Both graphify and codegraph (colbymchenry) were removed on 2026-07-28.",
            "User has developed or uses custom tools named Repokernel, engram, "
            "CodeGraph, RTK, and caveman",
        )
        is not None
    )


def test_a_removal_needs_a_shared_subject_too():
    assert (
        supersede.contradicts(
            "The user uninstalled codegraph this morning",
            "The user's package manager is pnpm",
        )
        is None
    )


def test_a_shared_subject_without_exclusivity_does_not_contradict():
    assert (
        supersede.contradicts(
            "The user installed codegraph last Tuesday",
            "The user uninstalled codegraph this morning",
        )
        is None
    )


# ---------------------------------------------------------------------------
# Effect at capture time
# ---------------------------------------------------------------------------


def test_capture_marks_the_contradicted_fact_superseded(tmp_path):
    store = MarkdownStore(tmp_path)
    old = store.add(_promoted(OLD_PRIMARY, kind=Kind.tooling))

    remember(store, NEW_PRIMARY, kind=Kind.tooling)

    assert store.get(old.id).status == Status.superseded


def test_a_superseded_fact_stops_being_recalled(tmp_path):
    store = MarkdownStore(tmp_path)
    old = store.add(_promoted(OLD_PRIMARY, kind=Kind.tooling))
    assert old.id in {m.id for m in rank(store.list(), today=dt.date(2026, 7, 29))}

    remember(store, NEW_PRIMARY, kind=Kind.tooling)

    assert old.id not in {m.id for m in rank(store.list(), today=dt.date(2026, 7, 29))}


def test_a_superseded_fact_is_queued_for_review(tmp_path):
    store = MarkdownStore(tmp_path)
    old = store.add(_promoted(OLD_PRIMARY, kind=Kind.tooling))

    new = remember(store, NEW_PRIMARY, kind=Kind.tooling)

    item = store.queue_get(old.id)
    assert item is not None
    assert new.id in item["reason"]


def test_capture_leaves_uncontradicted_facts_promoted(tmp_path):
    store = MarkdownStore(tmp_path)
    keeper = store.add(_promoted("The user lives in Cyprus", kind=Kind.location))

    remember(store, NEW_PRIMARY, kind=Kind.tooling)

    assert store.get(keeper.id).status == Status.promoted


def test_capture_reports_which_facts_it_superseded(tmp_path):
    store = MarkdownStore(tmp_path)
    old = store.add(_promoted(OLD_PRIMARY, kind=Kind.tooling))

    from engram.capture.active import stage

    result = stage(store, NEW_PRIMARY, kind=Kind.tooling)

    assert result.superseded == (old.id,)


def test_harvest_supersedes_too(tmp_path):
    """The path that produced the real stale fact must close the loop as well."""
    import json

    from engram.capture.sessions import harvest_session

    class Stub:
        def complete(self, system: str, user: str) -> str:
            return json.dumps(
                {"candidates": [{"fact": NEW_PRIMARY, "kind": "tooling", "confidence": 0.9}]}
            )

    store = MarkdownStore(tmp_path / "store")
    old = store.add(_promoted(OLD_PRIMARY, kind=Kind.tooling))
    fixture = tmp_path / "s.jsonl"
    fixture.write_text(
        json.dumps({"message": {"role": "user", "content": "swapped my code graph tool"}}),
        encoding="utf-8",
    )

    result = harvest_session(store, fixture, harness="claude-code", extractor=Stub())

    assert store.get(old.id).status == Status.superseded
    assert old.id in result["superseded"]


# ---------------------------------------------------------------------------
# Recovering a superseded fact
# ---------------------------------------------------------------------------


def test_a_superseded_fact_can_be_re_verified(tmp_path):
    from engram.bridge import review

    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="prefers pnpm", status=Status.superseded))

    result = review.approve(store, mem.id, confirm=True, today=dt.date(2026, 7, 29))

    assert result["ok"], result
    refreshed = store.get(mem.id)
    assert refreshed.status == Status.promoted
    assert refreshed.last_verified == dt.date(2026, 7, 29)


def test_a_decayed_fact_can_also_be_re_verified(tmp_path):
    """`stale` stays promotable too - both are awaiting the same human call."""
    from engram.bridge import review

    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="prefers pnpm", status=Status.stale))

    assert review.approve(store, mem.id, confirm=True, today=dt.date(2026, 7, 29))["ok"]
    assert store.get(mem.id).status == Status.promoted


# ---------------------------------------------------------------------------
# Confidence decay
# ---------------------------------------------------------------------------


def test_effective_confidence_is_full_when_just_verified():
    mem = Memory(fact="x", confidence=0.9, decay="180d", last_verified=dt.date(2026, 7, 29))
    assert effective_confidence(mem, today=dt.date(2026, 7, 29)) == 0.9


def test_effective_confidence_halves_at_the_midpoint_of_the_horizon():
    mem = Memory(fact="x", confidence=0.8, decay="100d", last_verified=dt.date(2026, 1, 1))
    midpoint = dt.date(2026, 1, 1) + dt.timedelta(days=50)
    assert effective_confidence(mem, today=midpoint) == 0.4


def test_effective_confidence_bottoms_out_at_the_horizon():
    mem = Memory(fact="x", confidence=0.9, decay="10d", last_verified=dt.date(2026, 1, 1))
    assert effective_confidence(mem, today=dt.date(2026, 6, 1)) == 0.0


def test_recall_ranks_a_freshly_verified_fact_above_a_decayed_one(tmp_path):
    old = Memory(
        fact="uses tmux for terminal multiplexing",
        confidence=0.95,
        decay="180d",
        status=Status.promoted,
        last_verified=dt.date(2026, 2, 1),
    )
    fresh = Memory(
        fact="uses zellij for terminal multiplexing",
        confidence=0.7,
        decay="180d",
        status=Status.promoted,
        last_verified=dt.date(2026, 7, 28),
    )

    ranked = rank([old, fresh], today=dt.date(2026, 7, 29))

    assert [m.fact for m in ranked][0] == fresh.fact


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_doctor_reports_superseded_memories():
    mem = Memory(
        id="mem-0001", fact="uses codegraph as their primary tool", status=Status.superseded
    )
    assert doctor([mem])["superseded"] == ["mem-0001"]


def test_decayed_and_contradicted_are_different_states():
    """Two unrelated ways a fact stops being true; conflating them loses one.

    A fact past its decay horizon has merely gone unconfirmed - time did that,
    and re-verifying it is routine. A superseded fact was actively contradicted
    by newer evidence. Sharing one status would let a sweep that retires the
    forgotten also silently retire the disputed.
    """
    decayed = Memory(
        id="mem-0001",
        fact="uses tmux for terminal multiplexing",
        status=Status.promoted,
        decay="30d",
        last_verified=dt.date(2026, 1, 1),
    )
    contradicted = Memory(id="mem-0002", fact="uses codegraph", status=Status.superseded)

    report = doctor([decayed, contradicted], today=dt.date(2026, 7, 29))

    assert report["stale"] == ["mem-0001"]
    assert report["superseded"] == ["mem-0002"]


def test_a_superseded_fact_is_not_reported_as_merely_decayed():
    contradicted = Memory(id="mem-0002", fact="uses codegraph", status=Status.superseded)
    assert doctor([contradicted])["stale"] == []


def test_the_model_can_retire_a_pair_the_lexical_rules_cleared(tmp_path):
    """"Madrid" and "Barcelona" share only "lives" -- no rule here can see it.

    The judge is the only thing that can, so this is the whole reason it exists.
    """
    from engram.core.schema import Kind, Memory, Status
    from engram.core.store import MarkdownStore

    store = MarkdownStore(tmp_path)
    old = store.add(
        Memory(fact="The user lives in Madrid", kind=Kind.location, status=Status.promoted)
    )
    new = store.add(Memory(fact="The user lives in Barcelona", kind=Kind.location))

    assert supersede.contradicts(new.fact, old.fact) is None  # lexical rules decline

    flagged = supersede.flag_contradicted(
        store, new, judge=lambda a, b: "a local model reads these as mutually exclusive"
    )
    assert flagged == (old.id,)
    assert store.get(old.id).status == Status.superseded
