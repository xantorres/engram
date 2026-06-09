"""Engram MCP server.

Exposes capture, recall, and review to any MCP client (Claude Code, Codex,
opencode, ...) over stdio. The store path and extractor come from engram
configuration, so the same server works for every harness on the machine.
"""

from __future__ import annotations

from fastmcp import FastMCP

from engram.bridge import review
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
    except ValueError:
        return Kind.preference


@mcp.tool
def remember(fact: str, kind: str = "preference", confidence: float = 0.6) -> str:
    """Stage a durable fact about the user into memory (pending review)."""
    memory = _remember(_store(), fact, kind=_kind(kind), confidence=confidence)
    return f"staged {memory.id}: [{memory.kind.value}] {memory.fact}"


@mcp.tool
def recall(query: str = "", limit: int = 20) -> list[dict]:
    """Return the user's promoted memories, most relevant first."""
    return [to_dict(m) for m in rank(_store().list(), query=query or None, limit=limit)]


@mcp.tool
def search(query: str, limit: int = 20) -> list[dict]:
    """Search the user's memories by keyword."""
    return [to_dict(m) for m in rank(_store().list(), query=query, limit=limit)]


@mcp.tool
def review_queue() -> list[dict]:
    """List memories awaiting human review."""
    return [
        {
            "id": item["memory"]["id"],
            "fact": item["memory"]["fact"],
            "kind": item["memory"]["kind"],
            "reason": item.get("reason", ""),
        }
        for item in review.pending_reviews(_store())
    ]


@mcp.tool
def promote(memory_id: str, confirm: bool = False) -> str:
    """Approve a queued memory (tier-3 write; requires confirm=True)."""
    result = review.approve(_store(), memory_id, confirm=confirm)
    return f"promoted {memory_id}" if result["ok"] else result["error"]


@mcp.tool
def reject(memory_id: str, reason: str = "") -> str:
    """Reject a queued memory."""
    result = review.reject(_store(), memory_id, reason=reason)
    return f"rejected {memory_id}" if result["ok"] else result["error"]


@mcp.resource("memory://recall")
def memory_recall() -> str:
    """Compact memory context for the current user, for clients to auto-load."""
    return render_block(_store().list())


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
