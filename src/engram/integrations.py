"""Per-harness MCP wiring snippets, emitted by ``engram init <harness>``."""

from __future__ import annotations

_SNIPPETS = {
    "claude-code": """// .mcp.json (project root) or ~/.claude.json
{
  "mcpServers": {
    "engram": { "command": "engram-mcp" }
  }
}""",
    "codex": """# ~/.codex/config.toml
[mcp_servers.engram]
command = "engram-mcp\"""",
    "opencode": """// ~/.config/opencode/opencode.json
{
  "mcp": {
    "engram": { "type": "local", "command": ["engram-mcp"] }
  }
}""",
}


def harnesses() -> list[str]:
    return sorted(_SNIPPETS)


def snippet(harness: str) -> str:
    try:
        return _SNIPPETS[harness]
    except KeyError:
        raise ValueError(
            f"unknown harness {harness!r}; expected one of {harnesses()}"
        ) from None
