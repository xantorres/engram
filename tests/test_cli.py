import os
import stat
import subprocess
import sys

from typer.testing import CliRunner

from engram.cli.main import app
from engram.core.schema import Kind, Memory, Status
from engram.core.store import MarkdownStore

runner = CliRunner()


def _seed_promoted(store_dir):
    store = MarkdownStore(store_dir)
    store.add(Memory(fact="prefers pnpm", kind=Kind.tooling, status=Status.promoted))


def test_gen_context_write_leaves_original_intact_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_STORE", str(tmp_path / "store"))
    _seed_promoted(tmp_path / "store")
    target = tmp_path / "AGENTS.md"
    original = "# Project\noriginal content\n"
    target.write_text(original, encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("simulated interrupt")

    monkeypatch.setattr(os, "replace", boom)
    result = runner.invoke(app, ["gen-context", "--write", str(target)])
    assert result.exit_code != 0
    # Atomic write: a failed swap leaves the user's file exactly as it was.
    assert target.read_text(encoding="utf-8") == original


def test_gen_context_write_preserves_mode_without_store_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_STORE", str(tmp_path / "store"))
    _seed_promoted(tmp_path / "store")
    target = tmp_path / "AGENTS.md"
    target.write_text("# Project\n", encoding="utf-8")
    os.chmod(target, 0o644)

    result = runner.invoke(app, ["gen-context", "--write", str(target)])
    assert result.exit_code == 0
    text = target.read_text(encoding="utf-8")
    assert "# Project" in text and "prefers pnpm" in text
    # The user's own file keeps its mode - not tightened to 0600.
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    # No store machinery littered beside the user's file.
    assert not (tmp_path / ".bak").exists()
    assert not (tmp_path / "audit.jsonl").exists()


def test_cli_reports_store_format_error_cleanly(tmp_path):
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / "memory.md").write_text("garbage without frontmatter", encoding="utf-8")
    env = {**os.environ, "ENGRAM_STORE": str(store_dir)}
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.argv = ['engram', 'list']; "
            "from engram.cli.main import main; main()",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "frontmatter" in proc.stderr.lower()


def test_migrate_projects_backfills_from_source(tmp_path, monkeypatch):
    store_dir = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_STORE", str(store_dir))
    store = MarkdownStore(store_dir)
    mem = store.add(
        Memory(
            fact="uses uv for dependency management",
            kind=Kind.tooling,
            source="harness:claude-code:-Users-alice-engram",
        )
    )

    dry = runner.invoke(app, ["migrate-projects"])
    assert dry.exit_code == 0
    assert MarkdownStore(store_dir).get(mem.id).project is None

    applied = runner.invoke(app, ["migrate-projects", "--apply"])
    assert applied.exit_code == 0
    assert MarkdownStore(store_dir).get(mem.id).project == "-Users-alice-engram"

    again = runner.invoke(app, ["migrate-projects", "--apply"])
    assert "0 fact" in again.stdout


def _enable_auto_refresh(monkeypatch, store_dir, target):
    monkeypatch.setenv("ENGRAM_STORE", str(store_dir))
    monkeypatch.setenv("ENGRAM_RECALL_AUTO_REFRESH", "true")
    monkeypatch.setenv("ENGRAM_RECALL_REFRESH_TARGETS", str(target))
    target.write_text("<!-- engram:begin -->\nold\n<!-- engram:end -->\n", encoding="utf-8")
    os.chmod(target, 0o644)


def test_auto_refresh_on_promote(tmp_path, monkeypatch):
    store_dir = tmp_path / "store"
    target = tmp_path / "AGENTS.md"
    _enable_auto_refresh(monkeypatch, store_dir, target)
    store = MarkdownStore(store_dir)
    mem = store.add(Memory(fact="prefers ripgrep for searching code", kind=Kind.tooling))
    store.enqueue(mem, dest="memory.md")

    result = runner.invoke(app, ["promote", mem.id, "--confirm"])
    assert result.exit_code == 0

    text = target.read_text(encoding="utf-8")
    assert "ripgrep" in text and "old" not in text
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    # Refresh uses the user-file path, not store machinery: nothing littered beside it.
    assert not (tmp_path / ".bak").exists()
    assert not (tmp_path / "audit.jsonl").exists()


def test_auto_refresh_on_forget(tmp_path, monkeypatch):
    store_dir = tmp_path / "store"
    target = tmp_path / "CLAUDE.md"
    _enable_auto_refresh(monkeypatch, store_dir, target)
    store = MarkdownStore(store_dir)
    mem = store.add(Memory(fact="prefers ripgrep for searching code", status=Status.promoted))
    # Seed the block with the fact so we can prove forget removes it.
    runner.invoke(app, ["gen-context", "--write", str(target)])
    assert "ripgrep" in target.read_text(encoding="utf-8")

    result = runner.invoke(app, ["forget", mem.id])
    assert result.exit_code == 0
    assert "ripgrep" not in target.read_text(encoding="utf-8")


def test_auto_refresh_on_sync_apply(tmp_path, monkeypatch):
    store_dir = tmp_path / "store"
    target = tmp_path / "AGENTS.md"
    _enable_auto_refresh(monkeypatch, store_dir, target)
    monkeypatch.setenv("ENGRAM_AUTOPROMOTE", "true")
    store = MarkdownStore(store_dir)
    store.add(Memory(fact="prefers fd over find for file search", kind=Kind.tooling))

    result = runner.invoke(app, ["sync", "--apply"])
    assert result.exit_code == 0
    assert "fd over find" in target.read_text(encoding="utf-8")


def test_no_auto_refresh_when_disabled(tmp_path, monkeypatch):
    store_dir = tmp_path / "store"
    target = tmp_path / "AGENTS.md"
    monkeypatch.setenv("ENGRAM_STORE", str(store_dir))
    monkeypatch.delenv("ENGRAM_RECALL_AUTO_REFRESH", raising=False)
    monkeypatch.setenv("ENGRAM_RECALL_REFRESH_TARGETS", str(target))
    original = "<!-- engram:begin -->\nold\n<!-- engram:end -->\n"
    target.write_text(original, encoding="utf-8")
    store = MarkdownStore(store_dir)
    mem = store.add(Memory(fact="prefers ripgrep for searching code", status=Status.promoted))

    runner.invoke(app, ["forget", mem.id])
    assert target.read_text(encoding="utf-8") == original


def test_forget_reports_truthful_wording(tmp_path, monkeypatch):
    store_dir = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_STORE", str(store_dir))
    store = MarkdownStore(store_dir)
    mem = store.add(Memory(fact="prefers pnpm", kind=Kind.tooling, status=Status.promoted))

    result = runner.invoke(app, ["forget", mem.id])
    assert result.exit_code == 0
    assert "removed" in result.stdout
    assert "may retain" in result.stdout
    assert "forgotten" not in result.stdout
