# Claude Code Migration Kit

A starter kit for running large-scale language migrations with Claude Code: the
prompts, templates, and scripts behind the process described in
**How Anthropic runs large-scale code migrations with Claude Code**.

This kit defaults to **structure-preserving migrations** — same architecture,
same data structures, new language. That's the case where the process below is
mechanical enough to template. If you're redesigning as you migrate, read
[If you're redesigning](#if-youre-redesigning) first: parts of this kit change,
and one part becomes invalid.

> **Plugin port:** packaged as a Claude Code plugin (orchestrator skill +
> hidden phase skills + scripts + templates) by fivecoil. Ported 1:1 from
> [anthropics/code-migration-kit-with-claude-code](https://github.com/anthropics/code-migration-kit-with-claude-code)
> (Apache-2.0). Mapping: the kit's `prompts/` became the hidden phase skills
> (`feasibility`, `judge-setup`, `dependency-map`, `gap-inventory`,
> `stress-test`, `translation-kickoff`, `survey-build`, `post-parity`),
> orchestrated by the `code-migration` skill; the kit's `scripts/` lives at
> the plugin root; `templates/` lives at
> `skills/code-migration/references/templates/`; the kit's operating manual
> (`CLAUDE.md`) is folded into the `code-migration` skill as its Standing
> rules. `fixtures/`, `examples/`, and `RUN-NOTES.md` were not ported —
> see the upstream repo.

> **Provenance:** these prompts are reconstructions — generalized and reviewed
> against real production migrations, including Bun's 1M+ line Zig→Rust port,
> not transcripts. The real artifacts were messier and more specific: the Bun
> port's "prompt" was a 576-line rulebook
> ([PORTING.md](https://github.com/oven-sh/bun/commit/46d3bc29f270fa881dd5730ef1549e88407701a5))
> and a one-sentence kickoff.

> **Status:** Reference code. This repo is a companion to the blog post and is
> not actively maintained. Issues and PRs are not monitored.

## Quick start

1. Install the plugin from the marketplace.
2. Open Claude Code in the repo you're migrating and invoke the
   `code-migration` skill (or just ask "can we migrate this to Rust?" —
   the skill's description routes it).
3. Make sure you have a judge before Step 1. If your existing test suite hits
   the public surface (or already lives in a third language), it's your judge —
   carry it into Step 6. If it imports internals that die with the old language,
   the `judge-setup` skill builds a portable parity harness and
   validates it (against the original *and* against deliberately broken code)
   before any translation. No judge, no exit condition.
4. Before any translation fan-out (`stress-test` onward): copy the plugin's
   `templates/settings.json` to the target repo's `.claude/settings.json`
   (see `templates/settings.README.md`) — installed by you before the stress
   test (Step 2's pilot needs the denies live), active through Step 4 (the
   `survey-build` skill), test denies re-activated for Step 6's fix loops.
   The `stress-test` and `translation-kickoff` skills verify
   it exists and stop without it — in one early test run this step was silently
   skipped and nothing caught it.
5. Then work `dependency-map`–`post-parity` in order, one gate at a time.
   The orchestrator routes: at every gate, re-invoke `code-migration` and it
   picks up from disk state.

## Should you migrate at all?

Start with the `feasibility` skill — the `code-migration` orchestrator runs
it first thing in your repo.
It produces a read-only report: the case for leaving, three committed calls
(structure-preserving or redesign, what verification costs, whether your tests
survive), a sketch of the six steps for your repo, and a verdict. "Don't
migrate" is a valid outcome. Nothing else in this kit matters until that
report says go.

## The six steps

Every step below follows the same doctrine: **you don't fix the code — you fix
the process that produced the code.** Individual failures get burned down by
fixer agents; recurring failures indict a rule, and the rule gets amended.

| Step | What happens | Kit artifact |
|---|---|---|
| 1. Create the map and the rules | Dependency map orders the work; gap inventory traces what the target language demands; the rulebook decides every translation question once | `scripts/depmap_*`, `dependency-map`, `gap-inventory`, `templates/RULEBOOK.md`, `templates/inventory.tsv` |
| 2. Stress-test the rules | Dual-translation bakeoff + pilot run on a handful of nasty files; the only surviving output is rule changes | `stress-test` |
| 3. Translate everything | Implementer + two adversarial reviewers + fixer per unit, fanned out over a mechanical queue; the compiler waits | `translation-kickoff`, `scripts/queue_runner.mjs` |
| 4. Compile | One survey build grades everything; error list becomes a machine queue sliced by module; fixers work without compiler access | `survey-build`, `templates/settings.json` (the bans) |
| 5. Run it | Hello world, then smoke tests — the cheap end-to-end proof before the expensive one | — |
| 6. Match behavior | Inherited test suite burndown, or build a parity referee against the old code | your existing test suite is the artifact; `post-parity` after the gate |

### Step 1 — Create the map and the rules

Three artifacts, built in parallel, audited together:

1. **The rulebook** (`templates/RULEBOOK.md` — copy it to
   `migration/RULEBOOK.md` in the target repo): every decision a translator could
   make two ways, decided once. The meta-rule: *if two agents could answer
   differently, it goes in the rulebook.* Draft it in a conversation with
   Claude, then have agents survey the codebase for the facts ("how many struct
   fields," "how many uses tree-wide") and adversarial reviewers audit it — one
   mistake class each.
2. **The dependency map** (`scripts/depmap_*.py|.mjs` + the `dependency-map` skill): a
   deterministic script, not agent judgment. Orders the work, finds the cycles
   — at file granularity AND at the target's package granularity. (Bun's port
   had a clean file graph and still hit ~16K compile errors from package-level
   cycles nobody had checked.)
3. **The gap inventory** (the `gap-inventory` skill + `templates/inventory.tsv`): one flat
   table of every site where the target language demands something the source
   let you keep in your head — ownership, lifetimes, nullability, interface
   contracts. Implementers grep it; nobody reads it top to bottom.

The sign-off gates in these prompts work like this: a workflow runs its phase
to completion, returns the evidence, and exits. **Your sign-off is the act of
kicking off the next one.** Every queue is defined by what exists on disk, so
stopping is free and resuming is a re-invocation, not a recovery. When a
phase skill says "use a workflow," it means: run the phase as parallel subagents
that complete and stop at the gate — in stock Claude Code, that's a Task/Agent
fan-out.

### Step 2 — Stress-test the rules

The rulebook has never met real code. Before any fan-out:
**bakeoff** (two translators, separate contexts — one follows the rules, one
never learns they exist; a diff inspector turns every difference into a verdict
on a rule) and **pilot** (the production pipeline exactly as the fan-out will
run it, graded on obedience, not output quality). Amendments are queued for
you, never self-applied. See the `stress-test` skill.

### Step 3 — Translate everything

The kickoff prompt is deliberately short (the `translation-kickoff` skill) because by now the
rulebook is the prompt. The queue is mechanical: every manifest entry with no
output file on disk yet (`scripts/queue_runner.mjs`) —
`migration/manifest.tsv` is a Step 1 output, generated by `dependency-map`'s
closing action from the dependency map's order with
`scripts/make_manifest.py`. Don't run the compiler — it grades everything next
step. Ban the expensive operations by configuration, not request:
`templates/settings.json`, copied to the target repo's `.claude/settings.json`
by you before the stress test (Step 2's pilot needs the denies live), active
through Step 4 (the `survey-build` skill), test denies re-activated for Step 6's fix loops
(see `templates/settings.README.md`).

### Step 4 — Compile

One scripted **survey build** runs the compiler across everything and emits the
error list as a machine queue, sliced by module, leaves-to-root. Parallel
fixers work the queue **without compiler access** — the daemon that owns the
build is the only process that runs it. Repeat until clean.
The `survey-build` skill runs this step. With denies active the standard
transport is `scripts/build_daemon.sh`: the human starts it once, it reruns
the build when the tree changes, and fixers consume the numbered
`migration/build-output-rN.txt` files. In one early test a human relayed every
build by hand — fine for a couple of rounds, not for fifty.

**If your target's typecheck is cheap (TypeScript, Go), this step dissolves
into Step 3** — run the typechecker inside every unit's loop instead of
batching it. The rule: every referee has a price, and the price decides its
position in the loop. Dissolving is an edit to the installed
`.claude/settings.json` at the gate — remove the typecheck denies so the loop
may run it. If settings.json was never installed there is nothing to edit,
and the dissolve silently proceeds with no guardrails at all — one early run did
exactly that, end to end. Install first, then dissolve.

### Step 5 — Run it

Hello world. Then the smallest end-to-end command your binary supports. Cheap
proofs before expensive ones.

### Step 6 — Match behavior

This is where the judge you set up before Step 1 does its job. If your old tests
hit the public surface (CLI, API, exported interface) they were your judge from
the start — run them against the new code, triage every failure by also running
it against the *old* code (regression / inherited / environment), and burn the
queue down. If your tests import source-language internals, the portable parity
harness you built with the `judge-setup` skill is the judge — run it against
both old and new and diff the results. Either way the old code is the executable
spec, the judge was validated against deliberately broken code before you trusted
it, and you've kept it running the whole migration. **Keep it running until the
end.**

The done-gate is explicit: every test in the parity referee passes, AND the
original suite has been re-run on the original code with zero inherited
failures — both counts documented in the report. For redesign migrations with no inherited suite, the gate is the
built parity referee's scenario list, signed off by the human. After the
gate, the `post-parity` skill burns down the deferred `BUG(port)` /
`TODO(port)` / `PERF(port)` markers — each fix its own flagged change, proved
by a parity re-run.

One lesson from early testing: debug the referee before believing its verdicts.
In one run, all reported divergences were false — traced to two comparator bugs
(whitespace handling; JSON.stringify converting NaN/-Infinity to null), none to
the port. A referee that fails everything is usually broken, not right.

## If you're redesigning

Three things change and one thing breaks:

- The rulebook becomes a **design document** (what the new architecture is),
  not a lookup table (how each construct translates).
- The **bakeoff is invalid** — a diff between rule-following and native
  translations measures your redesign, not your rules. Substitute: adversarial
  review of the design doc itself, plus disposable full runs — run the whole
  migration cheaply, review what came out, fix the rules, throw the run away.
- The unit of work is a module or subsystem, not a file.
- Behavior matching still works unchanged — it never sees your internals.

## When you don't need this kit

JavaScript→TypeScript is the most common migration by volume, and you mostly
don't need any of this: TypeScript is a superset, adoption can be incremental,
file by file, with no parallel codebase. Use this kit when the move is total —
when every file must cross and the old language ends up deleted.

## What's in the box

```
plugins/code-migration/
├── skills/
│   ├── code-migration/   the orchestrator (routing + standing rules) with references/templates/
│   └── …                 eight hidden phase skills: feasibility, judge-setup,
│                          dependency-map, gap-inventory, stress-test,
│                          translation-kickoff, survey-build, post-parity
├── scripts/              dependency mappers (Python, JS/TS, C headers) + queue runner + manifest builder + build daemon
├── README.md             the process definition (this file)
└── LICENSE               Apache-2.0, inherited from the upstream kit
```

`templates/` (RULEBOOK.md, inventory.tsv, settings.json + why each rule
exists) sits at `skills/code-migration/references/templates/`. Not ported
from the upstream kit: `fixtures/` (script test trees), `examples/` (a
complete example run), and `RUN-NOTES.md` (run receipts) — grab them from
the upstream repo if you want them.

## A note on cost and rate limits

A production-scale port can consume billions of input tokens inside two weeks.
The process
works at any rate limit — the calendar doesn't. At standard API limits, expect
the same steps across a longer wall clock. Budget with a visible
multiplication: units of work × per-unit estimate, never a vibes number. And
weigh the figure against what the spend buys: most of the multiplier is the
review topology, and in testing that's where the catches came from (RUN-NOTES).

## Contributing

The upstream kit is reference code published as a blog companion and is not
accepting contributions. This plugin port lives in
[plugins-hub](https://github.com/fivecoil/plugins-hub) — the dependency
mappers are theirs to break, and yours to fork.

## License

Apache-2.0 — see [LICENSE](LICENSE).
## Install

```sh
/plugin install code-migration --marketplace iknyazkov/plugins-hub
```
