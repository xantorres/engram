"""The CLI and MCP surfaces must report what capture screening did.

A refusal that looks like a success is the worst outcome: the caller believes
the fact is stored and never revisits it.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from typer.testing import CliRunner

from engram.cli.main import app
from engram.core.schema import Kind, Memory, Status
from engram.core.store import MarkdownStore

runner = CliRunner()

CREDENTIAL = "The user's GitHub Personal Access Token is named `xantorres-pat`."
OLD_PRIMARY = "The user currently uses codegraph (colbymchenry) as their primary tool."
NEW_PRIMARY = "codebase-memory is now the sole code-graph layer, replacing codegraph."


def _env(tmp_path, monkeypatch) -> MarkdownStore:
    monkeypatch.setenv("ENGRAM_STORE", str(tmp_path / "store"))
    return MarkdownStore(tmp_path / "store")


def test_cli_remember_refuses_a_credential_map(tmp_path, monkeypatch):
    store = _env(tmp_path, monkeypatch)

    result = runner.invoke(app, ["remember", CREDENTIAL])

    assert result.exit_code != 0
    assert "credential" in result.stdout
    assert store.list() == []


def test_cli_remember_force_stages_a_refused_fact(tmp_path, monkeypatch):
    store = _env(tmp_path, monkeypatch)

    result = runner.invoke(app, ["remember", CREDENTIAL, "--force"])

    assert result.exit_code == 0, result.stdout
    assert len(store.list()) == 1


def test_cli_remember_reports_an_existing_duplicate(tmp_path, monkeypatch):
    store = _env(tmp_path, monkeypatch)
    store.add(Memory(fact="The user prefers pnpm over npm for package installs"))

    result = runner.invoke(
        app, ["remember", "The user prefers pnpm over npm for installing packages"]
    )

    assert result.exit_code != 0
    assert "already known" in result.stdout


def test_cli_remember_names_what_it_would_supersede(tmp_path, monkeypatch):
    store = _env(tmp_path, monkeypatch)
    old = store.add(
        Memory(
            fact=OLD_PRIMARY,
            kind=Kind.tooling,
            status=Status.promoted,
            last_verified=dt.date.today(),
        )
    )

    result = runner.invoke(app, ["remember", NEW_PRIMARY, "-k", "tooling"])

    assert result.exit_code == 0, result.stdout
    assert old.id in result.stdout
    assert "would supersede" in result.stdout


def test_cli_harvest_reports_sensitive_skips(tmp_path, monkeypatch):
    import json

    _env(tmp_path, monkeypatch)
    fixture = tmp_path / "s.jsonl"
    fixture.write_text(
        json.dumps({"message": {"role": "user", "content": "chatter"}}), encoding="utf-8"
    )

    class Stub:
        def complete(self, system, user):
            return json.dumps(
                {"candidates": [{"fact": CREDENTIAL, "kind": "identity", "confidence": 0.9}]}
            )

    monkeypatch.setattr("engram.extract.client.Extractor", lambda _cfg: Stub())
    result = runner.invoke(app, ["harvest", str(fixture)])

    assert result.exit_code == 0, result.stdout
    assert "sensitive=1" in result.stdout


def test_cli_doctor_reports_superseded(tmp_path, monkeypatch):
    store = _env(tmp_path, monkeypatch)
    mem = store.add(Memory(fact="uses codegraph as their primary tool", status=Status.superseded))

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.stdout
    assert "superseded: 1" in result.stdout
    assert mem.id in result.stdout


def _mcp_remember(**payload) -> str:
    from fastmcp import Client

    from engram.mcp.server import mcp

    async def call():
        async with Client(mcp) as client:
            return (await client.call_tool("remember", payload)).data

    return asyncio.run(call())


def test_mcp_remember_surfaces_a_refusal_as_a_tool_error(tmp_path, monkeypatch):
    from fastmcp.exceptions import ToolError

    store = _env(tmp_path, monkeypatch)

    with pytest.raises(ToolError) as excinfo:
        _mcp_remember(fact=CREDENTIAL)

    assert "credential" in str(excinfo.value)
    assert store.list() == []


def test_mcp_remember_reports_a_contradiction(tmp_path, monkeypatch):
    store = _env(tmp_path, monkeypatch)
    old = store.add(
        Memory(
            fact=OLD_PRIMARY,
            kind=Kind.tooling,
            status=Status.promoted,
            last_verified=dt.date.today(),
        )
    )

    message = _mcp_remember(fact=NEW_PRIMARY, kind=Kind.tooling.value)

    assert old.id in message
