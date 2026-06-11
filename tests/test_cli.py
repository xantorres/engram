from typer.testing import CliRunner

from engram.cli.main import app
from engram.core.schema import Kind, Memory, Status
from engram.core.store import MarkdownStore

runner = CliRunner()


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
