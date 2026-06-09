"""Engram command-line interface.

Commands are registered as each subsystem comes online. Capture verbs
(`remember`, `harvest`, `list`) land here in Phase 2; review and recall follow.
"""

from __future__ import annotations

from pathlib import Path

import typer

from engram import __version__
from engram.config import load as load_config
from engram.core.schema import Kind, Status
from engram.core.store import MarkdownStore

app = typer.Typer(
    name="engram",
    help="Agent-agnostic memory layer: capture, review, and recall facts across any coding agent.",
    no_args_is_help=True,
    add_completion=False,
)


def _store() -> MarkdownStore:
    return MarkdownStore(load_config().store_dir)


@app.callback()
def _root() -> None:
    """Engram: capture, review, and recall facts across any coding agent."""


@app.command()
def version() -> None:
    """Print the installed engram version."""
    typer.echo(__version__)


@app.command()
def remember(
    fact: str,
    kind: str = typer.Option("preference", "--kind", "-k"),
    confidence: float = typer.Option(0.6, "--confidence", "-c"),
) -> None:
    """Stage a fact into memory (pending review)."""
    from engram.capture.active import remember as stage

    mem = stage(_store(), fact, kind=Kind(kind), confidence=confidence)
    typer.echo(f"staged {mem.id}: [{mem.kind.value}] {mem.fact}")


@app.command(name="list")
def list_memories(status: str = typer.Option(None, "--status", "-s")) -> None:
    """List memories, optionally filtered by status."""
    wanted = Status(status) if status else None
    for mem in _store().list(status=wanted):
        typer.echo(f"{mem.id}  [{mem.status.value}/{mem.kind.value}]  {mem.fact}")


@app.command()
def harvest(
    path: Path,
    harness: str = typer.Option("claude-code", "--harness", "-H"),
    min_confidence: float = typer.Option(0.5, "--min-confidence"),
) -> None:
    """Mine durable facts from a harness session transcript."""
    from engram.capture.sessions import harvest_session
    from engram.extract.client import Extractor

    config = load_config()
    staged = harvest_session(
        MarkdownStore(config.store_dir),
        path,
        harness=harness,
        extractor=Extractor(config.extractor),
        min_confidence=min_confidence,
    )
    typer.echo(f"staged {len(staged)} candidate(s) from {path}")


@app.command()
def recall(
    query: str = typer.Argument(""),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """Show promoted memories, optionally filtered by a query."""
    from engram.recall.rank import rank

    for mem in rank(_store().list(), query=query or None, limit=limit):
        typer.echo(f"{mem.id}  [{mem.kind.value}]  {mem.fact}")


@app.command(name="gen-context")
def gen_context(
    write: Path = typer.Option(None, "--write", "-w"),
    limit: int = typer.Option(30, "--limit", "-n"),
) -> None:
    """Generate the engram memory block for AGENTS.md / CLAUDE.md."""
    from engram.recall.context import render_block, upsert_block

    block = render_block(_store().list(), limit=limit)
    if write is None:
        typer.echo(block)
        return
    existing = write.read_text(encoding="utf-8") if write.exists() else ""
    write.write_text(upsert_block(existing, block), encoding="utf-8")
    typer.echo(f"updated {write}")


@app.command()
def init(harness: str) -> None:
    """Print the MCP config snippet to wire a harness to engram."""
    from engram.integrations import snippet

    typer.echo(snippet(harness))


@app.command()
def serve() -> None:
    """Start the engram MCP server (stdio)."""
    from engram.mcp.server import main as serve_main

    serve_main()


@app.command()
def sync(do_apply: bool = typer.Option(False, "--apply")) -> None:
    """Run the promotion bridge over pending candidates (dry-run unless --apply)."""
    from engram.bridge import promote as bridge

    config = load_config()
    store = MarkdownStore(config.store_dir)
    result = bridge.plan(store)
    if do_apply and config.autopromote:
        bridge.apply(store, result, autopromote=True)
        mode = "applied"
    elif do_apply:
        mode = "dry-run (autopromote off in config)"
    else:
        mode = "dry-run"
    typer.echo(
        f"[{mode}] append={len(result.appended)} "
        f"queue={len(result.queued)} skip={len(result.skipped)}"
    )
    for route in result.routes:
        typer.echo(
            f"  {route.action:6} {route.memory.id} [{route.memory.kind.value}] "
            f"{route.memory.fact}  ({route.reason})"
        )


@app.command()
def queue() -> None:
    """List memories awaiting review."""
    from engram.bridge import review

    for item in review.pending_reviews(_store()):
        mem = item["memory"]
        typer.echo(f"{mem['id']}  [{mem['kind']}]  {mem['fact']}  ({item.get('reason', 'review')})")


@app.command()
def show(memory_id: str) -> None:
    """Show a queued memory and its proposed change."""
    item = _store().queue_get(memory_id)
    if item is None:
        typer.echo(f"no queued memory {memory_id}")
        raise typer.Exit(1)
    mem = item["memory"]
    typer.echo(f"{mem['id']} [{mem['kind']}] conf={mem['confidence']}\n{mem['fact']}")
    if item.get("diff"):
        typer.echo("\n" + item["diff"])


@app.command()
def promote(memory_id: str, confirm: bool = typer.Option(False, "--confirm")) -> None:
    """Approve a queued memory (requires --confirm)."""
    from engram.bridge import review

    result = review.approve(_store(), memory_id, confirm=confirm)
    if not result["ok"]:
        typer.echo(result["error"])
        raise typer.Exit(1)
    typer.echo(f"promoted {result['id']}")


@app.command()
def reject(memory_id: str, reason: str = typer.Option("", "--reason")) -> None:
    """Reject a queued memory."""
    from engram.bridge import review

    result = review.reject(_store(), memory_id, reason=reason)
    typer.echo(f"rejected {memory_id}" if result["ok"] else result["error"])


@app.command()
def doctor() -> None:
    """Report stale, low-confidence, unverified, and conflicting memories."""
    from engram.health import doctor as run_doctor

    report = run_doctor(_store().list())
    for bucket, items in report.items():
        typer.echo(f"{bucket}: {len(items)}")
        for entry in items:
            typer.echo(f"  {entry}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
