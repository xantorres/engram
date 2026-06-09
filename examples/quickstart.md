# Quickstart

## Install

```bash
uv tool install engram      # or: pipx install engram
```

## Capture a few facts

```bash
engram remember "I prefer pnpm over npm" --kind tooling
engram remember "My VAT number is 12345678X" --kind fiscal
```

## Run the bridge

The bridge routes low-risk facts to the log and sensitive ones to a review
queue. It is dry-run until you enable autopromote.

```bash
export ENGRAM_AUTOPROMOTE=true
engram sync --apply
#  append mem-0001 [tooling] I prefer pnpm over npm   (low-risk)
#  queue  mem-0002 [fiscal]  My VAT number is ...     (fiscal needs review)
```

## Review the queue

```bash
engram queue
engram promote mem-0002            # refused: tier-3 needs confirmation
engram promote mem-0002 --confirm  # approved
```

## Recall

```bash
engram recall                      # what engram knows, ranked
engram recall pnpm                 # filtered by a query
```

## Wire it into an agent

```bash
engram init codex >> ~/.codex/config.toml
# or generate a context block for any harness:
engram gen-context --write AGENTS.md
```

## Learn from past sessions

```bash
engram harvest ~/.codex/sessions/2026/some-session.jsonl --harness codex
```
