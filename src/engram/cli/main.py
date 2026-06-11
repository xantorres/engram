"""Engram command-line interface.

Commands are registered as each subsystem comes online. Capture verbs
(`remember`, `harvest`, `list`) land here in Phase 2; review and recall follow.
"""

from __future__ import annotations

from pathlib import Path

import typer

from engram import __version__
from engram.config import ConfigError
from engram.config import load as load_config
from engram.core.schema import Kind, Status
from engram.core.store import MarkdownStore, StoreFormatError

app = typer.Typer(
    name="engram",
    help="Agent-agnostic memory layer: capture, review, and recall facts across any coding agent.",
    no_args_is_help=True,
    add_completion=False,
)


def _store_for(config) -> MarkdownStore:
    return MarkdownStore(
        config.store_dir,
        bak_keep_days=config.gc.bak_keep_days,
        audit_max_bytes=config.gc.audit_max_bytes,
    )


def _store() -> MarkdownStore:
    return _store_for(load_config())


def _auto_refresh(config, store: MarkdownStore) -> None:
    """Rewrite the configured context blocks after a promoted-state change."""
    if not (config.recall.auto_refresh and config.recall.refresh_targets):
        return
    from engram.recall.refresh import refresh_targets

    refresh_targets(store, config.recall.refresh_targets, limit=config.recall.limit)


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
    force: bool = typer.Option(False, "--force", help="Stage despite a capture screen."),
) -> None:
    """Stage a fact into memory (pending review)."""
    from engram.capture.active import stage

    try:
        parsed_kind = Kind(kind)
    except ValueError:
        valid = ", ".join(k.value for k in Kind)
        typer.echo(f"unknown kind {kind!r}; valid kinds: {valid}", err=True)
        raise typer.Exit(2) from None

    result = stage(_store(), fact, kind=parsed_kind, confidence=confidence, force=force)
    if not result.admitted:
        typer.echo(f"not staged: {result.reason}")
        typer.echo("pass --force to stage it anyway")
        raise typer.Exit(1)

    mem = result.memory
    typer.echo(f"staged {mem.id}: [{mem.kind.value}] {mem.fact}")
    if result.superseded:
        typer.echo(
            f"retired from recall pending review: {', '.join(result.superseded)}  "
            f"(engram show <id> to resolve)"
        )


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
    result = harvest_session(
        _store_for(config),
        path,
        harness=harness,
        extractor=Extractor(config.extractor),
        min_confidence=min_confidence,
    )
    typer.echo(
        f"staged {result['staged']} candidate(s) from {path} "
        f"(skipped dupe={result['skipped_dupe']} trivial={result['skipped_trivial']} "
        f"sensitive={result['skipped_sensitive']})"
    )
    if result["superseded"]:
        typer.echo(
            f"retired from recall pending review: {', '.join(result['superseded'])}  "
            f"(engram show <id> to resolve)"
        )


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
    project: str = typer.Option(None, "--project", "-p"),
) -> None:
    """Generate the engram memory block for AGENTS.md / CLAUDE.md."""
    from engram.recall.context import render_block, upsert_block
    from engram.recall.refresh import atomic_replace

    block = render_block(_store().list(), limit=limit, project=project)
    if write is None:
        typer.echo(block)
        return
    existing = write.read_text(encoding="utf-8") if write.exists() else ""
    atomic_replace(write, upsert_block(existing, block))
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
def sync(
    do_apply: bool = typer.Option(False, "--apply"),
    ids: list[str] | None = typer.Option(
        None, "--id", help="Only this memory id; repeat to add more."
    ),
    kinds: list[str] | None = typer.Option(
        None, "--kind", "-k", help="Only this kind; repeatable."
    ),
    limit: int | None = typer.Option(
        None, "--limit", "-n", help="Process at most this many candidates."
    ),
) -> None:
    """Run the promotion bridge over pending candidates (dry-run unless --apply).

    Without a filter this walks the entire pending backlog. --id, --kind and
    --limit narrow the batch so a backlog can be reviewed in bites.
    """
    from engram.bridge import promote as bridge

    for kind in kinds or ():
        try:
            Kind(kind)
        except ValueError:
            valid = ", ".join(k.value for k in Kind)
            typer.echo(f"unknown kind {kind!r}; valid kinds: {valid}", err=True)
            raise typer.Exit(2) from None

    config = load_config()
    store = _store_for(config)
    result = bridge.plan(
        store,
        kind_allowlist=config.kind_allowlist,
        ids=ids or None,
        kinds=kinds or None,
        limit=limit,
    )
    if do_apply and config.autopromote:
        bridge.apply(store, result, autopromote=True)
        _auto_refresh(config, store)
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
    """Show a memory awaiting review and its proposed change."""
    store = _store()
    item = store.queue_get(memory_id)
    if item is not None:
        mem = item["memory"]
        typer.echo(
            f"{mem['id']} [{mem['status']}/{mem['kind']}] conf={mem['confidence']}\n{mem['fact']}"
        )
        if item.get("reason"):
            typer.echo(f"\nreason: {item['reason']}")
        if item.get("diff"):
            typer.echo("\n" + item["diff"])
        return
    memory = store.get(memory_id)
    if memory is None:
        typer.echo(f"no memory {memory_id}")
        raise typer.Exit(1)
    typer.echo(
        f"{memory.id} [{memory.status.value}/{memory.kind.value}] "
        f"conf={memory.confidence}\n{memory.fact}"
    )


@app.command()
def promote(memory_id: str, confirm: bool = typer.Option(False, "--confirm")) -> None:
    """Approve a memory awaiting review (requires --confirm)."""
    from engram.bridge import review

    config = load_config()
    store = _store_for(config)
    # Show what is being approved. --confirm is otherwise a blind rubber-stamp on
    # an id, and ids arrive from scripts and suggestions as often as from reading.
    memory = store.get(memory_id)
    if memory is not None:
        typer.echo(f"{memory.id} [{memory.status.value}/{memory.kind.value}] {memory.fact}")


    result = review.approve(store, memory_id, confirm=confirm)
    if not result["ok"]:
        typer.echo(result["error"])
        raise typer.Exit(1)
    if result.get("warning"):
        typer.echo(f"warning: {result['warning']}")
    typer.echo(f"promoted {result['id']}")
    _auto_refresh(config, store)


@app.command()
def reject(memory_id: str, reason: str = typer.Option("", "--reason")) -> None:
    """Reject a memory awaiting review."""
    from engram.bridge import review

    result = review.reject(_store(), memory_id, reason=reason)
    if not result["ok"]:
        typer.echo(result["error"])
        raise typer.Exit(1)
    message = f"rejected {memory_id}"
    if result["undo_token"]:
        message += f"  undo_token={result['undo_token']}"
    typer.echo(message)


@app.command()
def forget(memory_id: str) -> None:
    """Retract a promoted memory (marks it rejected, emits undo token)."""
    from engram.bridge import review

    config = load_config()
    store = _store_for(config)
    result = review.forget(store, memory_id)
    if not result["ok"]:
        typer.echo(result["error"])
        raise typer.Exit(1)
    typer.echo(
        f"removed {result['id']} from recall (history and audit may retain it)  "
        f"undo_token={result['undo_token']}"
    )
    _auto_refresh(config, store)


@app.command()
def doctor() -> None:
    """Report stale, low-confidence, unverified, and conflicting memories."""
    from engram.health import doctor as run_doctor

    report = run_doctor(_store().list())
    for bucket, items in report.items():
        typer.echo(f"{bucket}: {len(items)}")
        for entry in items:
            typer.echo(f"  {entry}")


@app.command(name="migrate-projects")
def migrate_projects(do_apply: bool = typer.Option(False, "--apply")) -> None:
    """Backfill the project field from each fact's source (dry-run unless --apply)."""
    report = _store().backfill_projects(dry_run=not do_apply)
    mode = "applied" if do_apply else "dry-run"
    typer.echo(f"[{mode}] backfilled project on {report['count']} fact(s)")


@app.command(name="import")
def import_(directory: Path) -> None:
    """Import memories from a directory of frontmatter-markdown files."""
    from engram.capture.importer import import_markdown_dir

    staged = import_markdown_dir(_store(), directory)
    typer.echo(f"imported {len(staged)} memories from {directory}")
    for mem in staged:
        typer.echo(f"  {mem.id} [{mem.kind.value}] {mem.fact[:70]}")


def main() -> None:
    try:
        app()
    except (StoreFormatError, ConfigError) as e:
        typer.echo(str(e), err=True)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
