"""Failing tests for the 4 harvest/promotion pipeline bugs.

Written before any implementation changes — all should fail on the baseline.
"""

from __future__ import annotations

import json

from engram.core.schema import Kind, Memory, Status
from engram.core.store import MarkdownStore

# ---------------------------------------------------------------------------
# Shared stub extractor
# ---------------------------------------------------------------------------

class Stub:
    def __init__(self, raw: str):
        self.raw = raw

    def complete(self, system: str, user: str) -> str:
        return self.raw


# ===========================================================================
# Bug 1 — project attribution
# ===========================================================================

class TestProjectAttribution:
    """harvested facts carry source project extracted from transcript path."""

    def test_source_includes_project_from_path(self, tmp_path):
        """Path ~/.claude/projects/-Users-xtorres-dev/<id>.jsonl → project in source."""
        from engram.capture.sessions import harvest_session

        project_dir = tmp_path / ".claude" / "projects" / "-Users-xtorres-dev"
        project_dir.mkdir(parents=True)
        fixture = project_dir / "abc123.jsonl"
        turn = {"message": {"role": "user", "content": "I prefer pnpm"}}
        fixture.write_text(json.dumps(turn), encoding="utf-8")

        canned = (
            '{"candidates":[{"fact":"prefers pnpm over npm for all installs",'
            '"kind":"tooling","confidence":0.9}]}'
        )
        store = MarkdownStore(tmp_path / "store")
        result = harvest_session(
            store, fixture, harness="claude-code", extractor=Stub(canned)
        )

        assert result["staged"] == 1
        assert "-Users-xtorres-dev" in result["memories"][0].source

    def test_source_format_is_harness_colon_harness_colon_project(self, tmp_path):
        """Source string should be 'harness:<harness>:<project>'."""
        from engram.capture.sessions import harvest_session

        project_dir = tmp_path / ".claude" / "projects" / "-Users-xtorres-myproject"
        project_dir.mkdir(parents=True)
        fixture = project_dir / "sess.jsonl"
        fixture.write_text(
            json.dumps({"message": {"role": "user", "content": "hello"}}), encoding="utf-8"
        )

        canned = (
            '{"candidates":[{"fact":"prefers dark theme in all editors",'
            '"kind":"tooling","confidence":0.9}]}'
        )
        store = MarkdownStore(tmp_path / "store")
        result = harvest_session(
            store, fixture, harness="claude-code", extractor=Stub(canned)
        )

        assert result["staged"] == 1
        assert result["memories"][0].source == "harness:claude-code:-Users-xtorres-myproject"

    def test_no_project_parent_falls_back_gracefully(self, tmp_path):
        """Transcript not inside a known project dir → source still set (no crash)."""
        from engram.capture.sessions import harvest_session

        fixture = tmp_path / "session.jsonl"
        fixture.write_text(
            json.dumps({"message": {"role": "user", "content": "hello"}}), encoding="utf-8"
        )

        canned = (
            '{"candidates":[{"fact":"prefers dark theme in all editors",'
            '"kind":"tooling","confidence":0.9}]}'
        )
        store = MarkdownStore(tmp_path / "store")
        result = harvest_session(
            store, fixture, harness="claude-code", extractor=Stub(canned)
        )

        assert result["staged"] == 1
        assert result["memories"][0].source.startswith("harness:claude-code")

    def test_same_fact_different_project_is_distinct_in_dedup(self, tmp_path):
        """Same fact text staged via harvest_session from two project paths → both staged.

        The staging dedup must not suppress a fact that originates from a distinct
        project slug — the source differs even if the fact text is identical.
        """
        from engram.capture.sessions import harvest_session

        fact_json = json.dumps({
            "candidates": [
                {
                    "fact": "prefers pnpm over npm for all projects",
                    "kind": "tooling",
                    "confidence": 0.9,
                }
            ]
        })

        store_a = MarkdownStore(tmp_path / "store_a")
        store_b = MarkdownStore(tmp_path / "store_b")

        proj_a = tmp_path / ".claude" / "projects" / "-proj-a"
        proj_b = tmp_path / ".claude" / "projects" / "-proj-b"
        proj_a.mkdir(parents=True)
        proj_b.mkdir(parents=True)

        fixture_a = proj_a / "sess.jsonl"
        fixture_b = proj_b / "sess.jsonl"
        turn = {"message": {"role": "user", "content": "I prefer pnpm"}}
        fixture_a.write_text(json.dumps(turn), encoding="utf-8")
        fixture_b.write_text(json.dumps(turn), encoding="utf-8")

        result_a = harvest_session(
            store_a, fixture_a, harness="claude-code", extractor=Stub(fact_json)
        )
        result_b = harvest_session(
            store_b, fixture_b, harness="claude-code", extractor=Stub(fact_json)
        )

        assert result_a["staged"] == 1, "first project should stage the fact"
        assert result_b["staged"] == 1, (
            "second project (different store) should also stage the fact"
        )
        assert result_a["memories"][0].source == "harness:claude-code:-proj-a"
        assert result_b["memories"][0].source == "harness:claude-code:-proj-b"


# ===========================================================================
# Bug 2 — engram forget <memory-id>
# ===========================================================================

class TestForgetCommand:
    """forget: marks a promoted fact rejected via store.update with undo token."""

    def _promoted_store(self, tmp_path) -> tuple[MarkdownStore, Memory]:
        store = MarkdownStore(tmp_path)
        mem = store.add(
            Memory(
                fact="prefers pnpm",
                kind=Kind.tooling,
                status=Status.promoted,
            )
        )
        return store, mem

    def test_forget_marks_rejected(self, tmp_path):
        from engram.bridge.review import forget

        store, mem = self._promoted_store(tmp_path)
        result = forget(store, mem.id)
        assert result["ok"] is True
        assert store.get(mem.id).status == Status.rejected

    def test_forget_returns_undo_token(self, tmp_path):
        from engram.bridge.review import forget

        store, mem = self._promoted_store(tmp_path)
        result = forget(store, mem.id)
        assert "undo_token" in result and result["undo_token"]

    def test_forget_writes_audit_entry(self, tmp_path):
        from engram.bridge.review import forget

        store, mem = self._promoted_store(tmp_path)
        forget(store, mem.id)
        audit_path = tmp_path / "audit.jsonl"
        raw = audit_path.read_text(encoding="utf-8").splitlines()
        lines = [json.loads(entry) for entry in raw if entry.strip()]
        forget_entries = [e for e in lines if e.get("endpoint") == "fact/forget"]
        assert forget_entries, "expected at least one fact/forget audit entry"
        assert forget_entries[0]["entity_id"] == mem.id

    def test_forget_excludes_from_rendered_body(self, tmp_path):
        from engram.bridge.review import forget
        from engram.core.store import _render_body

        store, mem = self._promoted_store(tmp_path)
        forget(store, mem.id)
        body = _render_body(store.list())
        assert mem.fact not in body

    def test_forget_errors_on_missing_memory(self, tmp_path):
        from engram.bridge.review import forget

        store = MarkdownStore(tmp_path)
        result = forget(store, "mem-9999")
        assert result["ok"] is False
        assert "error" in result

    def test_forget_errors_on_non_promoted_memory(self, tmp_path):
        from engram.bridge.review import forget

        store = MarkdownStore(tmp_path)
        mem = store.add(Memory(fact="pending fact", kind=Kind.tooling))
        result = forget(store, mem.id)
        assert result["ok"] is False


# ===========================================================================
# Bug 3 — staging dedup + triviality filter
# ===========================================================================

class TestStagingFilter:
    """harvest_session pre-filters trivial and near-duplicate candidates."""

    def _fixture(self, tmp_path, content: str) -> object:
        f = tmp_path / "s.jsonl"
        f.write_text(
            json.dumps({"message": {"role": "user", "content": content}}), encoding="utf-8"
        )
        return f

    # --- triviality ---

    def test_short_fact_is_skipped(self, tmp_path):
        from engram.capture.sessions import harvest_session

        fixture = self._fixture(tmp_path, "hello")
        canned = '{"candidates":[{"fact":"hi","kind":"tooling","confidence":0.9}]}'
        store = MarkdownStore(tmp_path / "store")
        result = harvest_session(store, fixture, harness="claude-code", extractor=Stub(canned))
        assert result["staged"] == 0
        assert result["skipped_trivial"] >= 1

    def test_home_dir_path_fact_is_skipped(self, tmp_path):
        from engram.capture.sessions import harvest_session

        fixture = self._fixture(tmp_path, "hello")
        canned = (
            '{"candidates":[{"fact":"/Users/xtorres/projects/foo",'
            '"kind":"tooling","confidence":0.9}]}'
        )
        store = MarkdownStore(tmp_path / "store")
        result = harvest_session(store, fixture, harness="claude-code", extractor=Stub(canned))
        assert result["skipped_trivial"] >= 1

    def test_user_identifier_pattern_is_skipped(self, tmp_path):
        from engram.capture.sessions import harvest_session

        fixture = self._fixture(tmp_path, "hello")
        canned = (
            '{"candidates":[{"fact":"User identifier is xantorres",'
            '"kind":"identity","confidence":0.9}]}'
        )
        store = MarkdownStore(tmp_path / "store")
        result = harvest_session(store, fixture, harness="claude-code", extractor=Stub(canned))
        assert result["skipped_trivial"] >= 1

    def test_uses_macos_style_is_skipped(self, tmp_path):
        from engram.capture.sessions import harvest_session

        fixture = self._fixture(tmp_path, "hello")
        canned = '{"candidates":[{"fact":"uses macOS","kind":"infra","confidence":0.9}]}'
        store = MarkdownStore(tmp_path / "store")
        result = harvest_session(store, fixture, harness="claude-code", extractor=Stub(canned))
        assert result["skipped_trivial"] >= 1

    # --- dedup against existing store ---

    def test_near_dup_against_existing_promoted_is_skipped(self, tmp_path):
        from engram.capture.sessions import harvest_session

        fixture = self._fixture(tmp_path, "I prefer pnpm")
        canned = (
            '{"candidates":[{"fact":"prefers pnpm over npm for installs",'
            '"kind":"tooling","confidence":0.9}]}'
        )

        store = MarkdownStore(tmp_path / "store")
        store.add(Memory(fact="prefers pnpm over npm", kind=Kind.tooling, status=Status.promoted))

        result = harvest_session(store, fixture, harness="claude-code", extractor=Stub(canned))
        assert result["skipped_dupe"] >= 1

    # --- dedup within batch ---

    def test_near_dup_within_same_batch_is_deduplicated(self, tmp_path):
        from engram.capture.sessions import harvest_session

        fixture = self._fixture(tmp_path, "hello")
        # Two nearly-identical candidates in the same batch
        canned = json.dumps({
            "candidates": [
                {"fact": "prefers pnpm over npm", "kind": "tooling", "confidence": 0.9},
                {
                    "fact": "prefers pnpm over npm for installs",
                    "kind": "tooling",
                    "confidence": 0.8,
                },
            ]
        })
        store = MarkdownStore(tmp_path / "store")
        result = harvest_session(store, fixture, harness="claude-code", extractor=Stub(canned))
        assert result["staged"] == 1
        assert result["skipped_dupe"] >= 1

    # --- return shape ---

    def test_harvest_session_returns_dict_with_counts(self, tmp_path):
        from engram.capture.sessions import harvest_session

        fixture = self._fixture(tmp_path, "I prefer pnpm")
        canned = '{"candidates":[{"fact":"prefers pnpm","kind":"tooling","confidence":0.9}]}'
        store = MarkdownStore(tmp_path / "store")
        result = harvest_session(store, fixture, harness="claude-code", extractor=Stub(canned))
        assert isinstance(result, dict)
        assert "staged" in result
        assert "skipped_dupe" in result
        assert "skipped_trivial" in result

    def test_harvest_session_staged_list_accessible(self, tmp_path):
        from engram.capture.sessions import harvest_session

        fixture = self._fixture(tmp_path, "I prefer pnpm")
        canned = '{"candidates":[{"fact":"prefers pnpm","kind":"tooling","confidence":0.9}]}'
        store = MarkdownStore(tmp_path / "store")
        result = harvest_session(store, fixture, harness="claude-code", extractor=Stub(canned))
        assert len(result["memories"]) == result["staged"]


# ===========================================================================
# Bug 4 — configurable kind allowlist
# ===========================================================================

class TestKindAllowlist:
    """promote.plan() respects kind_allowlist from config when provided."""

    def _store_with(self, tmp_path, *mems):
        store = MarkdownStore(tmp_path)
        for m in mems:
            store.add(m)
        return store

    def test_no_allowlist_uses_auto_kinds_behavior(self, tmp_path):
        """Absent allowlist → existing AUTO_KINDS tiers logic unchanged."""
        from engram.bridge import promote as bridge

        store = self._store_with(
            tmp_path,
            Memory(fact="prefers pnpm", kind=Kind.tooling),
            Memory(fact="VAT is 12345678X", kind=Kind.fiscal),
        )
        result = bridge.plan(store, kind_allowlist=None)
        actions = {r.memory.fact: r.action for r in result.routes}
        assert actions["prefers pnpm"] == "append"
        assert actions["VAT is 12345678X"] == "queue"

    def test_allowlist_cannot_promote_curated_kind(self, tmp_path):
        """Curated kinds always queue for review, even when listed in the allowlist."""
        from engram.bridge import promote as bridge

        store = self._store_with(
            tmp_path,
            Memory(fact="email is user@example.com", kind=Kind.identity),
        )
        result = bridge.plan(store, kind_allowlist=["identity"])
        assert result.routes[0].action == "queue"

    def test_allowlist_does_not_bypass_conflict_routing(self, tmp_path):
        """Conflict always queues even if kind is in allowlist."""
        from engram.bridge import promote as bridge

        store = MarkdownStore(tmp_path)
        store.add(
            Memory(
                fact="email is user@example.com",
                kind=Kind.identity,
                status=Status.promoted,
            )
        )
        store.add(Memory(fact="email is other@example.com", kind=Kind.identity))

        result = bridge.plan(store, kind_allowlist=["identity"])
        pending_routes = [r for r in result.routes if r.memory.status == Status.pending]
        assert pending_routes[0].action == "queue"

    def test_allowlist_from_config_env_override(self, tmp_path, monkeypatch):
        """ENGRAM_BRIDGE_KIND_ALLOWLIST env var splits on comma → list."""
        monkeypatch.setenv("ENGRAM_BRIDGE_KIND_ALLOWLIST", "tooling,project")
        from importlib import reload

        import engram.config as config_mod
        reload(config_mod)
        cfg = config_mod.load()
        assert cfg.kind_allowlist == ["tooling", "project"]

    def test_allowlist_absent_env_returns_none(self, tmp_path, monkeypatch):
        """No env var and no config → kind_allowlist is None."""
        monkeypatch.delenv("ENGRAM_BRIDGE_KIND_ALLOWLIST", raising=False)
        from importlib import reload

        import engram.config as config_mod
        reload(config_mod)
        cfg = config_mod.load(tmp_path / "nonexistent.toml")
        assert cfg.kind_allowlist is None


# ===========================================================================
# R001 — forget() undo token must restore the memory.md fact
# ===========================================================================

class TestForgetUndoToken:
    """The undo_token returned by forget() must revert the memory.md mutation."""

    def test_undo_token_restores_promoted_status(self, tmp_path):
        from engram.bridge.review import forget
        from engram.core import atomic

        store = MarkdownStore(tmp_path)
        mem = store.add(
            Memory(
                fact="prefers pnpm over npm for all installs",
                kind=Kind.tooling,
                status=Status.promoted,
            )
        )
        result = forget(store, mem.id)
        assert result["ok"] is True

        # After forget the memory should be rejected
        assert store.get(mem.id).status == Status.rejected

        # Applying the undo token should restore the memory.md file to promoted state
        restore = atomic.restore_from_bak(result["undo_token"], root=tmp_path)
        assert restore["ok"] is True

        # Re-read from disk — status must be promoted again
        assert store.get(mem.id).status == Status.promoted

    def test_undo_token_is_for_memory_md_not_sentinel(self, tmp_path):
        from engram.bridge.review import forget

        store = MarkdownStore(tmp_path)
        mem = store.add(
            Memory(
                fact="preferred shell is zsh with oh-my-zsh config",
                kind=Kind.tooling,
                status=Status.promoted,
            )
        )
        result = forget(store, mem.id)

        # The bak file referenced by the token must contain memory.md path, not sentinel
        bak_dir = tmp_path / ".bak"
        token = result["undo_token"]
        bak_record = json.loads((bak_dir / f"{token}.bak").read_text())
        assert "memory.md" in bak_record["path"], (
            f"undo token must point to memory.md, got: {bak_record['path']}"
        )


# ===========================================================================
# R002 — rejected facts must not block re-ingestion
# ===========================================================================

class TestRejectedFactReingestion:
    """A forgotten (rejected) fact can be re-learned on the next harvest."""

    def test_rejected_fact_does_not_block_harvest(self, tmp_path):
        from engram.bridge.review import forget
        from engram.capture.sessions import harvest_session

        store = MarkdownStore(tmp_path / "store")
        # Stage and promote a fact
        mem = store.add(
            Memory(
                fact="prefers dark theme in all editors and terminals",
                kind=Kind.tooling,
                status=Status.promoted,
            )
        )
        # Forget it (marks rejected)
        forget(store, mem.id)
        assert store.get(mem.id).status == Status.rejected

        # Now harvest the same fact again — should be staged, not skipped as dupe
        proj_dir = tmp_path / ".claude" / "projects" / "-Users-test-app"
        proj_dir.mkdir(parents=True)
        fixture = proj_dir / "sess.jsonl"
        fixture.write_text(
            json.dumps({"message": {"role": "user", "content": "I use dark theme"}}),
            encoding="utf-8",
        )
        canned = json.dumps({
            "candidates": [
                {
                    "fact": "prefers dark theme in all editors and terminals",
                    "kind": "tooling",
                    "confidence": 0.9,
                }
            ]
        })
        result = harvest_session(store, fixture, harness="claude-code", extractor=Stub(canned))
        assert result["staged"] == 1, (
            f"rejected fact must not block re-ingestion; got skipped_dupe={result['skipped_dupe']}"
        )


# ===========================================================================
# R003 — _project_from_path anchors on .claude/projects
# ===========================================================================

class TestProjectFromPathAnchor:
    """_project_from_path uses .claude/projects anchor, not first 'projects' segment."""

    def test_nested_projects_directory_uses_claude_anchor(self, tmp_path):
        from engram.capture.sessions import _project_from_path

        # Path: ~/projects/personal/.claude/projects/-Users-alice-myapp/sess.jsonl
        path = (
            tmp_path / "projects" / "personal" / ".claude" / "projects"
            / "-Users-alice-myapp" / "sess.jsonl"
        )
        path.parent.mkdir(parents=True)
        path.touch()

        slug = _project_from_path(path)
        assert slug == "-Users-alice-myapp", f"expected slug '-Users-alice-myapp', got {slug!r}"

    def test_standard_claude_path_still_works(self, tmp_path):
        from engram.capture.sessions import _project_from_path

        path = tmp_path / ".claude" / "projects" / "-Users-bob-proj" / "abc.jsonl"
        path.parent.mkdir(parents=True)
        path.touch()

        slug = _project_from_path(path)
        assert slug == "-Users-bob-proj"

    def test_no_claude_projects_falls_back_to_none(self, tmp_path):
        from engram.capture.sessions import _project_from_path

        path = tmp_path / "some" / "arbitrary" / "path" / "sess.jsonl"
        path.parent.mkdir(parents=True)
        path.touch()

        # No .claude/projects in path → None (no first-'projects' fallback)
        slug = _project_from_path(path)
        assert slug is None


# ===========================================================================
# R004 — _TRIVIAL_USER_ID_RE must not kill legitimate role-description facts
# ===========================================================================

class TestTrivialUserIdRegression:
    """Legitimate 'user is <role>' facts must survive the triviality filter."""

    def test_user_is_project_lead_survives(self, tmp_path):
        """Single-token hyphenated role 'the-project-lead' must NOT be trivially filtered."""
        from engram.capture.sessions import harvest_session

        proj_dir = tmp_path / ".claude" / "projects" / "-proj-role"
        proj_dir.mkdir(parents=True)
        fixture = proj_dir / "sess.jsonl"
        fixture.write_text(
            json.dumps({"message": {"role": "user", "content": "I lead this project"}}),
            encoding="utf-8",
        )
        # Single-token role name with no digits/@ → must survive
        canned = json.dumps({
            "candidates": [
                {
                    "fact": "user is the-project-lead",
                    "kind": "identity",
                    "confidence": 0.8,
                }
            ]
        })
        store = MarkdownStore(tmp_path / "store")
        result = harvest_session(store, fixture, harness="claude-code", extractor=Stub(canned))
        assert result["staged"] == 1, (
            f"'user is the-project-lead' must not be filtered as trivial; "
            f"skipped_trivial={result['skipped_trivial']}"
        )

    def test_user_identifier_is_still_filtered(self, tmp_path):
        from engram.capture.sessions import harvest_session

        proj_dir = tmp_path / ".claude" / "projects" / "-proj-id"
        proj_dir.mkdir(parents=True)
        fixture = proj_dir / "sess.jsonl"
        fixture.write_text(
            json.dumps({"message": {"role": "user", "content": "my username"}}),
            encoding="utf-8",
        )
        # Username with digits/special chars → should still be filtered
        canned = json.dumps({
            "candidates": [
                {
                    "fact": "User identifier is xan.torres@example.com",
                    "kind": "identity",
                    "confidence": 0.9,
                }
            ]
        })
        store = MarkdownStore(tmp_path / "store")
        result = harvest_session(store, fixture, harness="claude-code", extractor=Stub(canned))
        assert result["skipped_trivial"] >= 1, (
            "username-style user identifier must still be filtered"
        )


# ===========================================================================
# R007 — forget() KeyError must return error envelope, not raise
# ===========================================================================

class TestForgetKeyErrorEnvelope:
    """forget() must never raise; concurrent-write KeyError → ok=False envelope."""

    def test_forget_concurrent_delete_returns_error_envelope(self, tmp_path, monkeypatch):
        from engram.bridge.review import forget

        store = MarkdownStore(tmp_path)
        mem = store.add(
            Memory(
                fact="prefers pnpm over npm for all installs",
                kind=Kind.tooling,
                status=Status.promoted,
            )
        )

        # Simulate registry disappearing between get() and update()
        def _raise_key_error(_memory):
            raise KeyError("injected concurrent write")

        # forget() calls update_with_token on MarkdownStore; patch that method
        monkeypatch.setattr(store, "update_with_token", _raise_key_error)
        result = forget(store, mem.id)
        assert result["ok"] is False
        assert "error" in result


# ===========================================================================
# R008 — _env_list comma-only value returns None
# ===========================================================================

class TestEnvListCommaOnly:
    """_env_list(',') → None, not []."""

    def test_comma_only_returns_none(self, monkeypatch):
        monkeypatch.setenv("ENGRAM_BRIDGE_KIND_ALLOWLIST", ",")
        from importlib import reload

        import engram.config as config_mod
        reload(config_mod)
        cfg = config_mod.load()
        assert cfg.kind_allowlist is None, "comma-only env value must not produce empty allowlist"

    def test_blank_env_returns_none(self, monkeypatch):
        monkeypatch.setenv("ENGRAM_BRIDGE_KIND_ALLOWLIST", "  ")
        from importlib import reload

        import engram.config as config_mod
        reload(config_mod)
        cfg = config_mod.load()
        assert cfg.kind_allowlist is None
