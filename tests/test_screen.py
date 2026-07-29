"""Capture screening: what must never reach the store.

Two classes of noise: facts anyone could derive from a file path, and facts that
map where the user's credentials live. Both are cheap to produce at harvest time
and expensive to have sitting in recall.
"""

from __future__ import annotations

import json

import pytest

from engram.capture.active import CaptureRefused, remember, stage
from engram.capture.sessions import harvest_session
from engram.core import screen
from engram.core.schema import Kind, Memory, Status
from engram.core.store import MarkdownStore
from engram.core.tiers import TIER_CURATED

# Verbatim from the store, where all five sat staged.
TRIVIAL_FACTS = [
    "User uses macOS (inferred from launchd jobs and file paths).",
    "The user runs on macOS.",
    "The user's home directory username is `alice`.",
    "The user's home directory is /Users/alice.",
    "The user works on Linux",
]

CREDENTIAL_FACTS = [
    "The user's GitHub Personal Access Token is named `xantorres-pat`.",
    "The user's GitHub service name in the keychain is `acme-gpr`.",
    "The API key is stored in the ENGRAM_EXTRACTOR_KEY environment variable.",
    "Secrets live in the login keychain under the service `github.com-pat`.",
    "Current authentication tokens are stored in localStorage under the key "
    "defined by LOCAL_STORAGE_KEY_AUTH.",
    "User has QA test credentials or business_admin_pwa credentials stored in their keychain",
]

# Drawn from the real store. Every one of these tripped an earlier, looser
# version of the credential screen; none of them maps a secret.
CREDENTIAL_LOOKALIKES = [
    "The user's project uses Tailwind CSS with `@theme inline` for token resolution.",
    "The user's application uses a `text-base` design token that resolves to 13.33px on mobile.",
    "User collaborates with a stakeholder named Paul on design token alignment.",
    "The user uses the environment variable `MF_HOT_TYPES=1` to control type refreshing.",
    "User's Google OAuth client is in 'Testing' status, causing 7-day token expiration",
    # Reporting a credential's *state* is not disclosing it.
    "User's Gmail refresh token was expired/revoked (invalid_grant)",
    "User's Google OAuth refresh token was revoked around mid-May 2026",
    "The user's API key is missing from the deployment environment",
    "The user's UAT environment is currently deployed with broken URLs due to a `$web` "
    "path handling issue in the `.env` generation step.",
]

KEEPERS = [
    "The user prefers pnpm over npm for package installs",
    "The user reviews every migration before it is applied to production",
    "The user's deployment target is a self-hosted runner on their own hardware",
    "The user prefers token-based auth over session cookies in their APIs",
]


class Stub:
    def __init__(self, *facts: str):
        self.facts = facts

    def complete(self, system: str, user: str) -> str:
        return json.dumps(
            {
                "candidates": [
                    {"fact": f, "kind": "preference", "confidence": 0.9} for f in self.facts
                ]
            }
        )


def _session(tmp_path):
    fixture = tmp_path / "s.jsonl"
    fixture.write_text(
        json.dumps({"message": {"role": "user", "content": "chatter"}}), encoding="utf-8"
    )
    return fixture


# ---------------------------------------------------------------------------
# Triviality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fact", TRIVIAL_FACTS)
def test_derivable_facts_are_trivial(fact):
    assert screen.triviality(fact) is not None


@pytest.mark.parametrize("fact", KEEPERS)
def test_durable_facts_are_not_trivial(fact):
    assert screen.triviality(fact) is None


def test_harvest_skips_trivial_candidates(tmp_path):
    store = MarkdownStore(tmp_path / "store")

    result = harvest_session(
        store, _session(tmp_path), harness="claude-code", extractor=Stub(*TRIVIAL_FACTS)
    )

    assert result["staged"] == 0
    assert result["skipped_trivial"] == len(TRIVIAL_FACTS)


# ---------------------------------------------------------------------------
# Credential-adjacent facts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fact", CREDENTIAL_FACTS)
def test_credential_locations_are_sensitive(fact):
    assert screen.sensitivity(fact) is not None


@pytest.mark.parametrize("fact", KEEPERS)
def test_ordinary_facts_are_not_sensitive(fact):
    assert screen.sensitivity(fact) is None


# A transcript where the user pasted a credential is exactly what harvest reads.
# A fact carrying the secret itself is worse than one naming where it lives, so
# these must never depend on the fact also using a locator phrase.
SECRET_VALUES = [
    "The user's password is hunter2",
    "The user's GitHub token is ghp_aBcD1234567890abcdefghijklmnop",
    "The API key is sk-ant-api03-xyzabc123",
    "The user's SSH passphrase: correcthorsebatterystaple",
    "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "The user's signing key is -----BEGIN OPENSSH PRIVATE KEY-----",
    "Database credentials: host=prod user=admin",
]


@pytest.mark.parametrize("fact", SECRET_VALUES)
def test_a_fact_carrying_the_secret_itself_is_sensitive(fact):
    assert screen.sensitivity(fact) is not None


def test_a_recognisable_secret_is_caught_with_no_credential_word_at_all():
    """A bare token pasted into a transcript still must not be stored."""
    assert screen.sensitivity("Use ghp_aBcD1234567890abcdefghijklmnop when pushing") is not None


@pytest.mark.parametrize("fact", CREDENTIAL_LOOKALIKES)
def test_credential_vocabulary_alone_is_not_sensitive(fact):
    """'design token', 'environment variable', a backticked literal - all innocent.

    The screen must fire on a credential being *located*, not on the words the
    rest of software engineering happens to share with security.
    """
    assert screen.sensitivity(fact) is None


def test_harvest_never_stages_a_credential_map(tmp_path):
    store = MarkdownStore(tmp_path / "store")

    result = harvest_session(
        store, _session(tmp_path), harness="claude-code", extractor=Stub(*CREDENTIAL_FACTS)
    )

    assert result["staged"] == 0
    assert result["skipped_sensitive"] == len(CREDENTIAL_FACTS)
    assert store.list() == []


def test_remember_refuses_a_credential_map(tmp_path):
    store = MarkdownStore(tmp_path)

    with pytest.raises(CaptureRefused) as excinfo:
        remember(store, CREDENTIAL_FACTS[0])

    assert "credential" in str(excinfo.value)
    assert store.list() == []


def test_forced_credential_capture_can_never_auto_promote(tmp_path):
    """The escape hatch stays deliberate: forced sensitive facts land tier 3."""
    store = MarkdownStore(tmp_path)

    mem = remember(store, CREDENTIAL_FACTS[0], force=True)

    assert mem.risk_tier == TIER_CURATED
    assert mem.status == Status.pending


# ---------------------------------------------------------------------------
# Dedup at capture
# ---------------------------------------------------------------------------


# A genuine restatement: same subject, same claim, different wording. Kept
# symmetric so the pair does not depend on which side says "the user".
KNOWN = "The user prefers pnpm over npm for package installs"
RESTATED = "The user prefers pnpm over npm for installing packages"


def test_the_restated_pair_really_is_a_duplicate():
    """Guards the fixture below: if this stops holding, those tests go vacuous."""
    from engram.core import dedup

    assert dedup.compare(RESTATED, KNOWN) == "duplicate"


def test_remember_refuses_a_fact_already_in_the_store(tmp_path):
    store = MarkdownStore(tmp_path)
    first = remember(store, KNOWN)

    result = stage(store, RESTATED)

    assert not result.admitted
    assert result.duplicate_of == first.id
    assert len(store.list()) == 1


def test_remember_force_overrides_the_duplicate_check(tmp_path):
    store = MarkdownStore(tmp_path)
    remember(store, KNOWN)

    remember(store, RESTATED, force=True)

    assert len(store.list()) == 2


def test_rejected_facts_do_not_block_relearning(tmp_path):
    store = MarkdownStore(tmp_path)
    store.add(Memory(fact=KNOWN, status=Status.rejected))

    mem = remember(store, RESTATED)

    assert mem.status == Status.pending


def test_harvest_dedups_hyphenation_variants(tmp_path):
    store = MarkdownStore(tmp_path / "store")
    store.add(Memory(fact="The user's code-graph layer is codebase-memory", kind=Kind.tooling))

    result = harvest_session(
        store,
        _session(tmp_path),
        harness="claude-code",
        extractor=Stub("The user's codegraph layer is codebasememory"),
    )

    assert result["staged"] == 0
    assert result["skipped_dupe"] == 1


def test_import_screens_the_same_way(tmp_path):
    from engram.capture.importer import import_markdown_dir

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text(f"---\ndescription: {CREDENTIAL_FACTS[0]}\n---\n", encoding="utf-8")
    (docs / "b.md").write_text(f"---\ndescription: {TRIVIAL_FACTS[1]}\n---\n", encoding="utf-8")
    (docs / "c.md").write_text(f"---\ndescription: {KEEPERS[0]}\n---\n", encoding="utf-8")

    staged = import_markdown_dir(MarkdownStore(tmp_path / "store"), docs)

    assert [m.fact for m in staged] == [KEEPERS[0]]
