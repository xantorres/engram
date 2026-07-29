from engram.core import dedup


def test_duplicate_paraphrase():
    result = dedup.compare("I prefer pnpm over npm", "Prefers pnpm over npm for installs")
    assert result == "duplicate"


def test_conflict_same_field_different_value():
    assert dedup.compare("My VAT number is 12345678A", "My VAT number is 99999999X") == "conflict"


def test_distinct_unrelated_facts():
    assert dedup.compare("I prefer pnpm", "I live in Cyprus") == "distinct"


def test_different_fields_same_token_class_are_distinct():
    # A company id and a personal id are different facts, not a conflict.
    assert dedup.compare("Company TIC is 12345678A", "Personal TIC is 87654321B") == "distinct"


def test_precision_tokens_extracted():
    tokens = dedup.precision_tokens("VAT 12345678A on 2026-06-09")
    assert "12345678A" in tokens
    assert "2026-06-09" in tokens


# ---------------------------------------------------------------------------
# Compound identifiers
#
# Facts name the same thing with different punctuation ("code-graph" vs
# "codegraph"), so a compound must also match its joined spelling. It must do
# that WITHOUT surrendering its parts: collapsing "a-b-c" to one token leaves
# only boilerplate to compare on, and every fact sharing a sentence template
# then reads as a duplicate of every other. All the cases below are drawn from
# the real store.
# ---------------------------------------------------------------------------


def test_hyphenation_variants_of_one_name_are_duplicates():
    assert (
        dedup.compare(
            "The user's code-graph layer is codebase-memory",
            "The user's codegraph layer is codebasememory",
        )
        == "duplicate"
    )


def test_the_same_repo_spelled_two_ways_is_a_duplicate():
    assert (
        dedup.compare(
            "User works on the acme/acme-react-ui-library project",
            "The user works on the Acme-Org/acme-react-ui-library repository.",
        )
        == "duplicate"
    )


def test_two_project_slugs_in_one_template_stay_distinct():
    assert (
        dedup.compare(
            "User has a project named g2i-vm-isolation",
            "User has a project named engram-memory-fabric",
        )
        == "distinct"
    )


def test_similar_but_different_package_managers_stay_distinct():
    """npm and pnpm are different answers; merging them loses a real preference."""
    assert (
        dedup.compare(
            "The user uses npm for package management.",
            "The project uses `pnpm` for package management.",
        )
        == "distinct"
    )


def test_two_includes_of_different_files_stay_distinct():
    assert (
        dedup.compare(
            "The user's CLAUDE.md includes minimal-change.md",
            "The user's CLAUDE.md includes core-principles.md",
        )
        == "distinct"
    )


def test_two_different_libraries_stay_distinct():
    assert (
        dedup.compare(
            "The user uses the react-country-flag library.",
            "The user uses the libphonenumber-js library.",
        )
        == "distinct"
    )


def test_unrelated_versioned_facts_are_not_a_conflict():
    assert (
        dedup.compare(
            "The project uses Ajv (8.20.0) for validation.",
            "User's project uses react-router",
        )
        == "distinct"
    )


def test_short_facts_differing_only_in_the_key_noun_stay_distinct():
    """Boilerplate alone must never carry a duplicate verdict.

    "runs on X" and "runs on Y" share everything but the one word that is the
    entire fact. Calling those duplicates drops the second at capture.
    """
    assert dedup.compare("The user runs on OpenBSD.", "The user runs on macOS.") == "distinct"
    assert dedup.compare("The user's editor is helix", "The user's editor is kakoune") == "distinct"


def test_a_real_paraphrase_still_survives_the_overlap_floor():
    assert (
        dedup.compare("I prefer pnpm over npm", "Prefers pnpm over npm for installs") == "duplicate"
    )


def test_a_compound_keeps_its_parts_as_tokens():
    tokens = dedup.salient_tokens("the code-graph layer")
    assert {"code", "graph", "codegraph"} <= tokens


def test_a_substituted_value_is_not_a_duplicate():
    # The incident this guards: a replacement tool read as a restatement of the
    # tool it replaced, so the correction was dropped and the stale fact stayed
    # in recall. The two facts share only the sentence frame.
    assert (
        dedup.compare(
            "The user currently uses codebase-memory as their primary tool",
            "The user currently uses codegraph as their primary tool",
        )
        == "conflict"
    )


def test_sharing_a_distinctive_token_still_reads_as_one_fact():
    # Both name the same library, so the extra qualifiers are detail, not a
    # different answer -- this must not become a conflict.
    assert (
        dedup.compare(
            "The user works on the Acme-Org/acme-react-ui-library repository.",
            "User works on the acme/acme-react-ui-library project",
        )
        == "duplicate"
    )


def test_a_frame_word_cannot_anchor_two_facts_together():
    assert dedup.shared_anchor("uses codegraph currently", "uses codebasememory currently") is None


def test_a_distinctive_shared_token_anchors():
    assert dedup.shared_anchor("the codegraph layer", "codegraph is installed") == "codegraph"
