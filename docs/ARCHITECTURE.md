# Architecture

Engram is a small, layered Python package. Each layer has one job and depends
only on the layers beneath it.

```
cli/  mcp/        <- surfaces: a CLI and an MCP server
  |     |
recall/ bridge/   <- selection, ranking, context generation; promotion + review
  |     |
capture/ extract/ <- active remember + transcript harvest; pluggable extractor LLM
  |     |
       core/      <- schema, store, tiers, atomic write+undo, dedup, freshness
```

## Core (`core/`)

- **`schema.py`** defines `Memory` (the `memory.v1` model): one atomic fact plus
  provenance (`source`, `learned_by`, `confidence`), lifecycle (`status`,
  `last_verified`, `decay`), and the `risk_tier` that governs its write.
- **`store.py`** is the `Store` interface and the default `MarkdownStore`, which
  keeps everything as plain Markdown + YAML: a `memory.md` registry, an
  append-only `memory-log.md`, and a `queue/` of items awaiting review.
- **`tiers.py`** is the entire write-safety model: tier 1 auto-appends, tier 3
  edits require confirmation. Sensitive kinds and conflicts are always tier 3.
- **`atomic.py`** writes through a temp file + `os.replace`, snapshots the prior
  content for single-step undo, and appends an audit record.
- **`dedup.py`** decides duplicate vs conflict vs distinct using token overlap
  and exact-match "precision tokens" (ids, dates, money).
- **`freshness.py`** parses decay horizons and decides staleness.

## Capture (`capture/`, `extract/`)

Two paths feed the same store:

- **Active:** an agent calls `remember(fact, kind)`; it lands as a pending
  candidate.
- **Passive harvest:** a per-harness reader flattens a session transcript to
  text, and the configured extractor (any OpenAI-compatible endpoint) returns
  candidate facts as strict JSON.

The extractor is pluggable and local-first by default (LM Studio / Ollama).

## Bridge (`bridge/`)

`promote.plan()` reads pending candidates, dedups them against promoted
memories, classifies risk, and routes each: low-risk to the log, sensitive or
conflicting to the review queue. `promote.apply()` does nothing unless
`autopromote` is on, so the bridge ships dark and is `--dry-run` by default.
`review.approve()` is a tier-3 write and is refused without explicit confirm.

## Recall (`recall/`)

`rank()` returns promoted, fresh memories ordered by relevance. `context.py`
renders a delimited memory block that can be refreshed in place inside an
`AGENTS.md` or `CLAUDE.md` file.

## Surfaces (`cli/`, `mcp/`)

The CLI and the FastMCP server are thin shells over the layers above. The MCP
server exposes just `remember` and `recall` as tools, plus `memory://recall` as
a resource, so any MCP client shares one store with a minimal context footprint.
Review and promotion stay CLI-only, since approving a sensitive memory is a
human action, not an agent's.
