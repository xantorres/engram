# Security and privacy

Engram is built to hold personal information safely.

## Local-first

- Memories live on your disk, as plain files, under a directory you choose.
- There is no engram cloud and no telemetry. Nothing is sent anywhere.
- The only network call engram makes is to the extractor endpoint **you**
  configure. Point it at a local model (LM Studio, Ollama) and engram never
  leaves the machine.

## You stay in control

- **Tiered writes.** Sensitive kinds (identity, fiscal, people, health, ...) and
  any value conflict are escalated to a review queue. Approving them is a
  tier-3 action that is refused without explicit confirmation.
- **Dry-run by default.** The promotion bridge writes nothing unless
  `autopromote` is enabled in your config.
- **Undo and audit.** Every write snapshots the previous content for single-step
  undo and appends an `audit.jsonl` record.

## Secrets

- Engram stores no credentials. An optional extractor API key is read from your
  config or the `ENGRAM_EXTRACTOR_KEY` environment variable and is never
  written to the store.
- Keep your memory store out of version control if it holds private facts. A
  private deployment should point `store.dir` at a vault you already back up.

## Reporting

Found a problem? Open a private security advisory on the repository rather than
a public issue.
