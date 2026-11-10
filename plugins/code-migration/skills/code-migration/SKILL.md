---
name: code-migration
description: >
  Run a large-scale language migration with the six-step process: create the
  map and the rules, stress-test the rules, translate everything, compile, run
  it, match behavior. Use when the user wants to migrate, port, or rewrite a
  codebase from one language to another ("migrate this to Rust", "port our
  Python CLI to TypeScript", "should we rewrite this in Go?"), or asks for a
  migration feasibility assessment. Not for incremental JS→TS adoption — that
  needs no machinery — or single-file conversions.
---

# Code migration (six-step process)

You are orchestrating a language migration using the code-migration plugin,
inside the repository being migrated. The plugin's
[README](${CLAUDE_PLUGIN_ROOT}/README.md) defines the process; the Standing
rules section below is your operating manual — read the README before acting.
Do not improvise a different process when a phase skill exists for the step.
The standing rules override
convenience: queues live on disk, sign-off gates end workflows, the rulebook
is read-only inside loops, reviewers are adversarial and separate.

## Standing rules

These rules bind every phase, for you and for every agent you fan out.

1. **The rulebook is read-only inside any loop.** Implementers, reviewers, and
   fixers cite it; they never edit it. Rulebook amendments are queued for the
   human and applied between batches. The graded party does not relax its own
   grader.
2. **Queues live on disk.** A unit of work is done when its output file exists
   at the path the naming rules dictate — never when an orchestrator remembers
   it. If you find yourself tracking state in conversation, stop and write a
   manifest instead (`${CLAUDE_PLUGIN_ROOT}/scripts/queue_runner.mjs`).
3. **Sign-off gates end the workflow.** When a phase skill says "stop and show
   me" or "no fan-out until I sign off," the phase is over: return the
   evidence and exit. Do not proceed on inferred approval. The human
   re-invokes the next phase; resumability is free because of rule 2.
4. **Reviewers are adversarial, separate, and read-only.** Two reviewers per
   unit, separate contexts, assume the work is wrong, touch nothing. Every
   finding cites a rule or a source line. A third rules on disagreements,
   defaulting to not-confirmed. Fixers apply confirmed findings only.
5. **Banned operations are banned by configuration.** The plugin's
   `templates/settings.json` denies the expensive commands inside loops
   (mutating version control, the compiler, long-running test commands). If a
   denied command blocks you, that is the design working: flag it, don't route
   around it. Exception: a cheap typecheck (tsc, go vet) may be promoted into
   the loop per README Step 4 — that swap is made by editing settings.json at
   a gate, never by routing around a live deny. If the platform's own
   safeguards block you from editing settings.json (self-modification
   protection), that is not an error to work around: the human makes the edit
   manually, or serves as build daemon per the `survey-build` skill. Routing
   around a live deny is forbidden no matter which layer enforces it.
6. **Recurring failures move upstream.** Fix one instance, fine. See the same
   failure twice more, stop fixing instances: write up which rule produced it,
   queue the rulebook amendment, and propose regenerating the slice that rule
   touched.
7. **The old code is the spec.** It stays runnable until behavior matching is
   done. When a test fails on the new code, run it on the old code before
   classifying: old-fails-too = inherited, not yours. Never skip, delete, or
   weaken a test to make a queue shrink; slow-but-passing goes in a ledger,
   not a commit.
8. **Unknown is an answer.** When neither the rulebook nor the inventory
   decides a question, translate to the most conservative representation the
   target offers, leave a greppable TODO marker, and keep moving. A searchable
   artifact beats a stalled batch.

## Where things live

| Artifact | Path | Created by |
|---|---|---|
| Rulebook | `migration/RULEBOOK.md` (copy from the plugin's `templates/`) | Step 1 |
| Dependency map | `migration/depmap/` (edges.tsv, order.txt, cycles.txt) | `scripts/depmap_*` |
| Gap inventory | `migration/inventory.tsv` | Step 1, `gap-inventory` |
| Work manifest | `migration/manifest.tsv` | Step 1, `dependency-map`'s closing action (`scripts/make_manifest.py`) → Step 3 input |
| Stress-test report | `migration/stress-test/` | Step 2 |
| Translated output | path per the rulebook's naming rules | Step 3 |
| Cost log | `migration/cost-log.tsv` | every phase, one line per gate |
| Deviation log | `migration/RULEBOOK.md`, Deviation log section | written at gates |

If these paths don't exist yet, the migration hasn't started: invoke the
`feasibility` skill first and stop at its verdict.

## Model guidance

Token spend concentrates in loops; blast radius decides the tier, not task
prestige. Rulebook authorship and amendments: largest available model —
one-time work, and every error replicates into every translated file.
Skeptic reviewers: largest or mid tier, depending on gap complexity.
High-volume translation implementers: mid or small tier — two reviewers and
a compiler stand behind them. Fixers: mid tier — the compiler is the real
referee. The model plan is the human's decision, made at the feasibility
gate (the `feasibility` skill's Model plan section), and it binds every
post-feasibility phase — `dependency-map` through `post-parity` take the
chosen tiers as explicit `[model]` placeholders. Running subagents on an
untriggered default is a process violation — log it in the deviation log
(RULEBOOK.md, Deviation log section).

## Routing

1. **No migration artifacts exist yet** (`migration/` absent): invoke the
   `feasibility` skill. Produce the report, deliver the verdict, STOP.
   Do not begin Step 1 in the same session, even if the verdict is "migrate
   now" — the human kicks off each phase.
1b. **Feasibility signed off, no judge yet:** before Step 1, confirm a judge
   exists that runs against both old and new code through the public surface.
   If the existing suite is public-surface (or already in a third language),
   it's the judge — carry it to Step 6. If it imports internals, invoke the
   `judge-setup` skill to build and validate a portable parity harness
   (validated against the original AND deliberately broken code) and STOP at
   its gate. Never start Step 1 without a judge — there's no exit condition
   without one.
2. **Feasibility and judge signed off, no map/rules:** run Step 1 — adapt a
   `${CLAUDE_PLUGIN_ROOT}/scripts/depmap_*` script for the source ecosystem
   (the `dependency-map` skill), copy the plugin's
   `skills/code-migration/references/templates/RULEBOOK.md` to
   `migration/RULEBOOK.md` and draft it with the human (the policy decisions
   are theirs; survey the codebase for the facts), then the `gap-inventory`
   skill for the gap inventory. Each ends at its own gate.
3. **Step 1 signed off:** confirm the HUMAN has installed
   `.claude/settings.json` from the plugin's
   `skills/code-migration/references/templates/settings.json` — the
   `stress-test` skill verifies it; never install it yourself — then invoke
   the `stress-test` skill. But FIRST check the rulebook's §0 posture:
   if this is a redesign migration, the bakeoff is invalid; substitute
   adversarial design-doc review and tell the human why.
4. **Stress test signed off:** confirm `migration/manifest.tsv` exists
   (the `dependency-map` skill's closing action generates it), then invoke
   the `translation-kickoff` skill with
   `${CLAUDE_PLUGIN_ROOT}/scripts/queue_runner.mjs` as the queue — first
   check the referee price: if the target's typecheck is cheap, the dissolve
   edit is the human's act at the gate — ask them to remove those denies from
   settings.json; never edit it yourself (README Step 4 dissolves into Step 3).
5. **Translation queue empty:** Step 4 via the `survey-build` skill
   (survey build → machine queue → fixers without compiler access; skip it
   entirely if Step 4 dissolved into Step 3), then Step 5 (hello world,
   smoke), then Step 6 (inherited suite burndown, or parity referee against
   the old code).
6. **Step 6 done-gate passed** (both counts documented — README Step 6):
   invoke the `post-parity` skill — collect the port markers, classify
   fix-now vs document-and-close, ship each fix as its own flagged change
   proved by a parity re-run. Ends at its own gate.

## Constant behaviors

- Report progress as burndown numbers
  (`${CLAUDE_PLUGIN_ROOT}/scripts/queue_runner.mjs status`), not prose.
- A failure seen three times is a rule bug: stop fixing instances, queue the
  amendment, propose regenerating the slice.
- "Don't migrate" and "stop here" are valid recommendations at every gate.
