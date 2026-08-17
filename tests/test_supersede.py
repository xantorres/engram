"""Supersession: a newer fact must retire the older one it contradicts.

The failure this guards against is a promoted fact that stays recall-able after
the world moved on, so every future session is served a confident lie.
"""

from __future__ import annotations

import datetime as dt

from engram.capture.active import remember, stage
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
# The harvested duplicate that retired 151 reviewed facts: it reports a removal,
# but the thing removed is punctuation, not any of the preferences it retired.
DASH_PREFERENCE = (
    "The user prefers strict formatting consistency, specifically the removal "
    "of em/en dashes from documentation."
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


def test_a_removal_is_about_the_thing_it_names():
    """The removal clause is the claim; the rest of the sentence is context.

    A preference that mentions removing punctuation from documents says nothing
    about how the user likes their documents formatted, and must not retire the
    fact that says so.
    """
    assert (
        supersede.contradicts(
            DASH_PREFERENCE,
            "The user prefers strict formatting in every document they write.",
        )
        is None
    )


def test_a_removal_does_not_retire_everything_phrased_the_same_way():
    """The failure that emptied recall: one word of shared frame read as a subject."""
    for victim in (
        "The user prefers a clean git history with no merge commits.",
        "The user prefers reviewers to be strict about test coverage.",
        "The user prefers terse, fragment-style prose in conversation.",
    ):
        assert supersede.contradicts(DASH_PREFERENCE, victim) is None, victim


def test_two_preferences_need_a_shared_subject_to_claim_one_role():
    """Both say "prefers" and both name a default. Neither is about the other."""
    assert (
        supersede.contradicts(
            "User prefers autopromote to be explicitly controlled (currently false).",
            "User prefers the xhigh effort level as the default setting.",
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


def test_a_superseded_fact_stops_being_recalled(tmp_path):
    from engram.bridge import review

    store = MarkdownStore(tmp_path)
    old = store.add(_promoted(OLD_PRIMARY, kind=Kind.tooling))
    assert old.id in {m.id for m in rank(store.list(), today=dt.date(2026, 7, 29))}

    new = store.add(Memory(fact=NEW_PRIMARY, kind=Kind.tooling))
    review.approve(store, new.id, confirm=True)

    assert old.id not in {m.id for m in rank(store.list(), today=dt.date(2026, 7, 29))}


def test_a_superseded_fact_is_queued_for_review(tmp_path):
    from engram.bridge import review

    store = MarkdownStore(tmp_path)
    old = store.add(_promoted(OLD_PRIMARY, kind=Kind.tooling))

    new = store.add(Memory(fact=NEW_PRIMARY, kind=Kind.tooling))
    review.approve(store, new.id, confirm=True)

    item = store.queue_get(old.id)
    assert item is not None
    assert new.id in item["reason"]


def test_capture_leaves_uncontradicted_facts_promoted(tmp_path):
    store = MarkdownStore(tmp_path)
    keeper = store.add(_promoted("The user lives in Cyprus", kind=Kind.location))

    remember(store, NEW_PRIMARY, kind=Kind.tooling)

    assert store.get(keeper.id).status == Status.promoted


def test_harvest_proposes_too(tmp_path):
    """The path that produced the real damage must propose, never retire."""
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

    assert store.get(old.id).status == Status.promoted
    assert old.id in result["disputed"]


# ---------------------------------------------------------------------------
# Authority: an unreviewed fact proposes, it does not retire
# ---------------------------------------------------------------------------


def test_an_unreviewed_fact_retires_nothing(tmp_path):
    """The whole incident in one test.

    A freshly harvested candidate is not knowledge yet. Letting one retire a
    fact a human approved inverts the trust the rest of engram is built on.
    """
    store = MarkdownStore(tmp_path)
    old = store.add(_promoted("The user's documentation uses em dashes throughout."))
    new = store.add(Memory(fact=DASH_PREFERENCE))

    verdict = supersede.propose(store, new)

    assert verdict.ids == (old.id,)
    assert store.get(old.id).status == Status.promoted
    assert store.queue_get(old.id) is None, "a reviewed fact was written to by a pending one"


def test_the_proposal_rides_the_new_fact(tmp_path):
    """The reviewer decides on the newcomer, so that is where its claim belongs."""
    store = MarkdownStore(tmp_path)
    old = store.add(_promoted("The user's documentation uses em dashes throughout."))
    new = store.add(Memory(fact=DASH_PREFERENCE))

    supersede.propose(store, new)

    envelope = store.queue_get(new.id)
    assert envelope is not None
    assert "would supersede" in envelope["reason"]
    assert old.id in envelope["reason"]


def test_capture_only_proposes(tmp_path):
    store = MarkdownStore(tmp_path)
    old = store.add(_promoted(OLD_PRIMARY, kind=Kind.tooling))

    result = stage(store, NEW_PRIMARY, kind=Kind.tooling)

    assert result.disputed == (old.id,)
    assert store.get(old.id).status == Status.promoted


def test_promotion_is_what_applies_a_supersession(tmp_path):
    from engram.bridge import review

    store = MarkdownStore(tmp_path)
    old = store.add(_promoted(OLD_PRIMARY, kind=Kind.tooling))
    keeper = store.add(_promoted("The user lives in Cyprus", kind=Kind.location))
    new = store.add(Memory(fact=NEW_PRIMARY, kind=Kind.tooling))

    result = review.approve(store, new.id, confirm=True, today=dt.date(2026, 7, 29))

    assert result["superseded"] == (old.id,)
    retired = store.get(old.id)
    assert retired.status == Status.superseded
    assert retired.superseded_by == new.id
    assert retired.superseded_at == dt.date(2026, 7, 29)
    assert store.get(keeper.id).status == Status.promoted


def test_a_supersession_records_why_it_happened(tmp_path):
    from engram.bridge import review

    store = MarkdownStore(tmp_path)
    old = store.add(_promoted(OLD_PRIMARY, kind=Kind.tooling))
    new = store.add(Memory(fact=NEW_PRIMARY, kind=Kind.tooling))

    review.approve(store, new.id, confirm=True)

    assert "codegraph" in store.get(old.id).superseded_reason


def test_a_fact_only_retires_facts_of_a_kind_it_could_be_about(tmp_path):
    """A note about a deleted worktree is not a verdict on how the user works."""
    store = MarkdownStore(tmp_path)
    old = store.add(_promoted("The user prefers worktrees for parallel sessions."))
    new = store.add(
        Memory(
            fact="The user's worktree was deleted mid-session by an external process.",
            kind=Kind.constraint,
            status=Status.promoted,
        )
    )

    assert supersede.apply(store, new).ids == ()
    assert store.get(old.id).status == Status.promoted


# ---------------------------------------------------------------------------
# The cap: one fact retiring many is a bug, not a discovery
# ---------------------------------------------------------------------------

_SWEEP = "The user uninstalled codegraph graphify ripgrep and fdfind this morning."
_SWEPT = (
    "The user's system includes a codegraph.",
    "graphify renders the dependency graph.",
    "The user greps with ripgrep.",
    "fdfind is installed on this machine.",
)


def _sweeper(store: MarkdownStore, fact: str) -> Memory:
    for victim in _SWEPT:
        store.add(_promoted(victim, kind=Kind.tooling))
    return store.add(Memory(fact=fact, kind=Kind.tooling, status=Status.promoted))


def test_a_fact_that_would_retire_the_store_retires_nothing(tmp_path):
    store = MarkdownStore(tmp_path)
    sweeper = _sweeper(store, _SWEEP)

    verdict = supersede.apply(store, sweeper)

    assert verdict.ids == ()
    assert verdict.anomaly
    assert {m.status for m in store.list() if m.id != sweeper.id} == {Status.promoted}


def test_an_explicit_bulk_revocation_may_pass_the_cap(tmp_path):
    """Retiring a whole class is a real thing to say; it just has to be said."""
    store = MarkdownStore(tmp_path)
    sweeper = _sweeper(
        store, "All of codegraph graphify ripgrep and fdfind were removed this morning."
    )

    verdict = supersede.apply(store, sweeper)

    assert len(verdict.ids) == len(_SWEPT)
    assert not verdict.anomaly


def test_an_over_cap_proposal_says_so_in_the_queue(tmp_path):
    store = MarkdownStore(tmp_path)
    for victim in _SWEPT:
        store.add(_promoted(victim, kind=Kind.tooling))
    candidate = store.add(Memory(fact=_SWEEP, kind=Kind.tooling))

    verdict = supersede.propose(store, candidate)

    assert verdict.anomaly
    assert "cap" in store.queue_get(candidate.id)["reason"]


# ---------------------------------------------------------------------------
# Recovering a superseded fact
# ---------------------------------------------------------------------------


def test_restore_puts_a_retired_fact_back_in_recall(tmp_path):
    from engram.bridge import review

    store = MarkdownStore(tmp_path)
    old = store.add(_promoted(OLD_PRIMARY, kind=Kind.tooling))
    new = store.add(Memory(fact=NEW_PRIMARY, kind=Kind.tooling))
    review.approve(store, new.id, confirm=True)

    result = review.restore(store, old.id)

    assert result["ok"], result
    restored = store.get(old.id)
    assert restored.status == Status.promoted
    assert restored.superseded_by is None
    assert restored.superseded_at is None
    assert restored.superseded_reason is None


def test_restore_drops_the_envelope_that_explained_the_retirement(tmp_path):
    """A surviving envelope would keep re-listing a fact that is back in recall."""
    from engram.bridge import review

    store = MarkdownStore(tmp_path)
    old = store.add(_promoted(OLD_PRIMARY, kind=Kind.tooling))
    new = store.add(Memory(fact=NEW_PRIMARY, kind=Kind.tooling))
    review.approve(store, new.id, confirm=True)
    assert store.queue_get(old.id) is not None

    review.restore(store, old.id)

    assert store.queue_get(old.id) is None


def test_restore_by_superseder_undoes_the_whole_sweep(tmp_path):
    from engram.bridge import review

    store = MarkdownStore(tmp_path)
    sweeper = _sweeper(store, "All of codegraph graphify ripgrep and fdfind were removed.")
    retired = supersede.apply(store, sweeper).ids
    assert len(retired) == len(_SWEPT)

    result = review.restore_by(store, sweeper.id)

    assert sorted(result["restored"]) == sorted(retired)
    assert all(store.get(mid).status == Status.promoted for mid in retired)


def test_restore_refuses_a_fact_nothing_retired(tmp_path):
    from engram.bridge import review

    store = MarkdownStore(tmp_path)
    mem = store.add(_promoted("prefers pnpm"))

    result = review.restore(store, mem.id)

    assert not result["ok"]
    assert "not superseded" in result["error"]


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


def test_doctor_reports_one_fact_that_retired_many():
    """The shape of the incident, read straight off the registry."""
    superseder = Memory(id="mem-0100", fact="dashes were removed", status=Status.promoted)
    swept = [
        Memory(
            id=f"mem-000{n}",
            fact=f"preference {n}",
            status=Status.superseded,
            superseded_by="mem-0100",
        )
        for n in range(1, supersede.MAX_SUPERSEDES + 2)
    ]

    report = doctor([superseder, *swept])

    assert report["mass_supersede"] == [("mem-0100", len(swept))]


def test_doctor_reports_a_retirement_no_reviewed_fact_authorised():
    """Every victim of the incident carries this: retired by a pending fact."""
    unreviewed = Memory(id="mem-0100", fact="dashes were removed", status=Status.pending)
    victim = Memory(
        id="mem-0001", fact="prefers em dashes", status=Status.superseded, superseded_by="mem-0100"
    )

    report = doctor([unreviewed, victim])

    assert report["unauthorized_supersede"] == [("mem-0001", "mem-0100")]


def test_doctor_accepts_a_retirement_a_promoted_fact_authorised():
    superseder = Memory(id="mem-0100", fact="dashes were removed", status=Status.promoted)
    victim = Memory(
        id="mem-0001", fact="prefers em dashes", status=Status.superseded, superseded_by="mem-0100"
    )

    report = doctor([superseder, victim])

    assert report["unauthorized_supersede"] == []
    assert report["mass_supersede"] == []


def test_doctor_does_not_call_two_different_applications_a_conflict():
    """Different subjects, one of them dated. Not two answers to one question."""
    first = Memory(
        id="mem-0001",
        fact="User has a pending application (app #77) with micro1 due 2026-06-23.",
        status=Status.promoted,
    )
    second = Memory(
        id="mem-0002",
        fact="User has a pending application (app #95) with AlphaSights.",
        status=Status.promoted,
    )

    assert doctor([first, second])["conflicts"] == []


def test_the_model_can_retire_a_pair_the_lexical_rules_cleared(tmp_path):
    """"Madrid" and "Barcelona" share only "lives" -- no rule here can see it.

    The judge is the only thing that can, so this is the whole reason it exists.
    """
    store = MarkdownStore(tmp_path)
    old = store.add(
        Memory(fact="The user lives in Madrid", kind=Kind.location, status=Status.promoted)
    )
    new = store.add(
        Memory(fact="The user lives in Barcelona", kind=Kind.location, status=Status.promoted)
    )

    assert supersede.contradicts(new.fact, old.fact) is None  # lexical rules decline

    verdict = supersede.apply(
        store, new, judge=lambda a, b: "a local model reads these as mutually exclusive"
    )
    assert verdict.ids == (old.id,)
    assert store.get(old.id).status == Status.superseded
