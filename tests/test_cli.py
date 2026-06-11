import os
import subprocess
import sys

from typer.testing import CliRunner

from engram.cli.main import app
from engram.core.schema import Kind, Memory, Status
from engram.core.store import MarkdownStore

runner = CliRunner()


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
