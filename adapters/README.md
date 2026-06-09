# Adapters

Drop-in snippets that wire `engram-mcp` into each agent. `engram init <harness>`
prints the same snippet on demand.

| Harness | File | Where it goes |
|---|---|---|
| Claude Code | [claude-code/.mcp.json](claude-code/.mcp.json) | project `.mcp.json` or `~/.claude.json` |
| Codex | [codex/config.toml](codex/config.toml) | `~/.codex/config.toml` or `.codex/config.toml` |
| opencode | [opencode/opencode.json](opencode/opencode.json) | `~/.config/opencode/opencode.json` |

After wiring, the agent gets two capabilities:

- **Capture** via the `remember` tool, plus passive `engram harvest` over the
  harness transcript.
- **Recall** via the `memory://recall` resource, or by writing a memory block
  into the harness context file with `engram gen-context --write AGENTS.md`.
