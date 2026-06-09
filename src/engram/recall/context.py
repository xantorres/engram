"""Generate a portable memory block for AGENTS.md / CLAUDE.md.

The block is delimited by HTML comment markers so engram can refresh it in place
without disturbing the rest of the user's context file.
"""

from __future__ import annotations

from engram.core.schema import Memory
from engram.recall.rank import rank

BLOCK_BEGIN = "<!-- engram:begin -->"
BLOCK_END = "<!-- engram:end -->"


def render_block(memories: list[Memory], *, limit: int = 30) -> str:
    items = rank(memories, limit=limit)
    lines = [BLOCK_BEGIN, "## What engram remembers about you", ""]
    if not items:
        lines.append("_No memories yet._")
    else:
        by_kind: dict[str, list[Memory]] = {}
        for memory in items:
            by_kind.setdefault(memory.kind.value, []).append(memory)
        for kind in sorted(by_kind):
            lines.append(f"**{kind}**")
            lines.extend(f"- {memory.fact}" for memory in by_kind[kind])
            lines.append("")
    lines.append(BLOCK_END)
    return "\n".join(lines).rstrip() + "\n"


def upsert_block(existing: str, block: str) -> str:
    """Replace an existing engram block in ``existing``, or append a new one."""
    block = block.rstrip() + "\n"
    if BLOCK_BEGIN in existing and BLOCK_END in existing:
        pre = existing[: existing.index(BLOCK_BEGIN)]
        post = existing[existing.index(BLOCK_END) + len(BLOCK_END) :]
        return pre.rstrip("\n") + "\n\n" + block + post.lstrip("\n")
    if not existing.strip():
        return block
    return existing.rstrip("\n") + "\n\n" + block
