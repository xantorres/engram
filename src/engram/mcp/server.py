"""Engram MCP server.

Exposes the two capabilities an agent needs mid-task - capture and recall - to
any MCP client over stdio. Review and promotion are deliberately CLI-only: a
human approves sensitive writes, an agent does not.
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from engram.capture.active import remember as _remember
from engram.config import load as load_config
from engram.core.schema import Kind
from engram.core.store import MarkdownStore
from engram.recall.context import render_block
from engram.recall.rank import rank, to_dict

mcp = FastMCP("engram")


def _store() -> MarkdownStore:
    return MarkdownStore(load_config().store_dir)


def _kind(value: str) -> Kind:
    try:
        return Kind(value.strip().lower())
    except ValueError as e:
        valid = ", ".join(k.value for k in Kind)
        raise ToolError(f"unknown kind {value!r}; valid kinds: {valid}") from e


@mcp.tool
def remember(fact: str, kind: str = "preference", confidence: float = 0.6) -> str:
    """Stage a durable fact about the user into memory (pending review)."""
    memory = _remember(_store(), fact, kind=_kind(kind), confidence=confidence)
    return f"staged {memory.id}: [{memory.kind.value}] {memory.fact}"


@mcp.tool
def recall(query: str = "", limit: int = 20) -> list[dict]:
    """Return the user's promoted memories, most relevant first. Pass a query to filter."""
    return [to_dict(m) for m in rank(_store().list(), query=query or None, limit=limit)]


@mcp.resource("memory://recall")
def memory_recall() -> str:
    """Compact memory context for the current user, for clients to auto-load."""
    return render_block(_store().list())


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
