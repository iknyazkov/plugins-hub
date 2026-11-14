# Wiki schema

How this wiki works. It follows the LLM Wiki method: the LLM writes and
maintains every page; the human sources, steers, and asks.

## Layers

- **Source of truth** — the code, tests, and git history of `channelsrv/`.
  The wiki reads from them, never overwrites them. When wiki and code
  disagree, the code wins and the wiki gets fixed.
- **The wiki** — these files. LLM-owned. Distilled knowledge, not a mirror:
  a page should answer a question, not restate a module.
- **This schema** — conventions and workflows below.

## Files

Flat, no subdirectories. `MAIN.md` indexes everything.

| File | Role |
|---|---|
| `MAIN.md` | entry point: overview + navigation |
| `ADR.md` | all architecture decisions, one `## ADR-NNNN` per entry |
| `SCHEMA.md` | this file |
| `<topic>.md` | topical pages, created on demand when a subsystem outgrows a MAIN.md section |

## Conventions

- English everywhere. No secrets: env var **names** only, never values.
- Markdown relative links (`[progress chain](progress-chain.md)`), no
  wikilinks — this renders on GitHub.
- **No log file.** Git history is the timeline; docs travel with the change
  they describe, so history and wiki stay aligned.

## ADR format

Append-only, oldest at top. Each entry:

```
## ADR-NNNN — <title>

**Status:** accepted | superseded by ADR-MMMM (decided <date>, recorded <date>)

**Context:** why a decision was needed.
**Decision:** what was decided.
**Consequences:** what this costs or implies.
**Alternatives considered:** required when the choice wasn't obvious.
```

Supersede by editing the old entry's Status line — never delete entries.
Numbering never reuses.

## Workflows

**Ingest (event-driven).** Any session that changes channelsrv behavior
updates the affected wiki pages and adds/updates the relevant ADR **in the
same commit** as the code. Wiki-only changes do **not** bump the channel
plugin version.

**Query.** Read `MAIN.md` first to find pages, then drill in. Answers worth
keeping (comparisons, analyses, discovered connections) get filed back as
wiki pages instead of evaporating into chat history.

**Lint (on request).** Health-check: contradictions between pages, claims
stale after a refactor, orphan pages, concepts mentioned everywhere but
documented nowhere, missing ADRs for visible decisions. Lint results that
change pages are committed like any ingest.
