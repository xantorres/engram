import asyncio

from fastmcp import Client

from engram.core.schema import Status
from engram.core.store import MarkdownStore
from engram.mcp.server import mcp


def _run(coro):
    return asyncio.run(coro)


def test_tools_registered():
    async def run():
        async with Client(mcp) as client:
            return {t.name for t in await client.list_tools()}

    assert {"remember", "recall", "search"} <= _run(run())


def test_remember_then_recall_roundtrip(tmp_path, monkeypatch):
    store_dir = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_STORE", str(store_dir))

    async def stage():
        async with Client(mcp) as client:
            await client.call_tool("remember", {"fact": "prefers pnpm", "kind": "tooling"})

    _run(stage())

    # Recall returns promoted memories only, so promote the staged candidate.
    store = MarkdownStore(store_dir)
    staged = store.list()[0]
    store.update(staged.model_copy(update={"status": Status.promoted}))

    async def fetch():
        async with Client(mcp) as client:
            return (await client.call_tool("recall", {})).data

    data = _run(fetch())
    assert any(item["fact"] == "prefers pnpm" for item in data)
