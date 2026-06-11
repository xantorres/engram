# Engram

**An agent-agnostic memory layer.** Capture facts about you and your work from *any* coding agent, review them on your terms, and recall them everywhere.

Engram runs locally, stores memories as plain Markdown you own, and speaks the [Model Context Protocol](https://modelcontextprotocol.io) so it works with Claude Code, Codex, opencode, and any MCP-capable client - driving cloud or local models (LM Studio, Ollama) alike.

> **Status:** early development. The core engine and MCP server are being built in the open. APIs will change.

## Why

Coding agents forget everything between sessions. Workarounds exist, but each is locked to one tool: every harness has its own memory, and none of them share. Engram is the shared brain - one local store that every agent reads from and writes to, with **you** as the gatekeeper.

## How it works

```
   any agent ──remember()──▶ ┌───────────┐ ──auto-log──▶ memory-log.md
   (mid-task)                │  engram   │
                             │  capture  │ ──gate──▶ review queue ──you approve──▶ memory.md
   session transcripts ─────▶│  + bridge │
   (local-model harvest)     └─────┬─────┘
                                   │
   recall ◀── MCP resource ────────┤
   recall ◀── generated AGENTS.md ─┘
```

- **Capture** - agents call a `remember` tool mid-task, or Engram harvests durable facts from session transcripts using a local model.
- **Review** - low-risk facts are logged automatically (you choose which kinds); anything sensitive waits in a queue you approve, and any promoted fact can be retracted with `engram forget`. Nothing rewrites your curated notes without consent.
- **Recall** - every agent loads your memories through an MCP resource or a generated `AGENTS.md` / `CLAUDE.md` context block.

## Supported clients

| Client | Capture | Recall |
|---|---|---|
| Claude Code | MCP tool + transcript harvest | MCP resource + `CLAUDE.md` block |
| Codex | MCP tool + transcript harvest | MCP resource + `AGENTS.md` block |
| opencode | MCP tool + transcript harvest | MCP resource + `AGENTS.md` block |
| Any MCP client | MCP tool | MCP resource |

## Quickstart

```bash
uv tool install engram          # or: pipx install engram

engram remember "I prefer pnpm over npm"
engram recall                   # list what engram knows
engram serve                    # start the MCP server for your agents
```

Wire it into an agent (Codex shown):

```toml
# ~/.codex/config.toml
[mcp_servers.engram]
command = "engram-mcp"
```

## Design principles

- **Local-first.** Your memories never leave your machine. No telemetry.
- **You own the data.** Plain Markdown + YAML, git-diffable, no database lock-in.
- **Human in the loop.** Tiered writes: auto-log the trivial, gate the sensitive.
- **Bring your own model.** Any OpenAI-compatible endpoint extracts memories - cloud or local.

## How it compares

Most memory tools are vector stores the agent writes to directly. Engram takes a different stance:

| | Typical memory tool | Engram |
|---|---|---|
| Capture | Agent writes directly | Federated across the agents you already use |
| Trust | Whatever the agent stored | Human review gate on sensitive writes |
| Storage | Vector DB | Plain Markdown + YAML you own, git-diffable |
| Hosting | Often cloud | Local-first, no telemetry |
| Models | Provider-specific | Any OpenAI-compatible endpoint |

It federates capture across your agents, gates sensitive writes behind your approval, and keeps everything in a plain-text store on your machine.

## Documentation

- [Quickstart](examples/quickstart.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Security and privacy](docs/SECURITY.md)
- [Adapters](adapters/README.md)

## License

[MIT](LICENSE)
