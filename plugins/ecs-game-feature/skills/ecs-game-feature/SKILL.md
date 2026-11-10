---
name: ecs-game-feature
description: Implement game features on a custom Go ECS framework (pkg/ecs) — components, systems, entity spawning, and world wiring. Use this skill whenever the user asks to add, change, or fix game behavior in the Go game repo: new mechanics, movement, AI, collision reactions, abilities, scoring, spawning — even when they never say "ECS" or "system". Also use it when defining new components or systems, or when wiring systems into the main update loop.
compatibility: Requires Claude Code and a Go game repository built on the custom pkg/ecs framework (components implement ComponentProof, systems implement OnAttach/OnUpdate/OnDetach, tick loop via ControlWorld.Iterate).
metadata:
  author: fivecoil
  version: "0.1.0"
---

# Implementing game features on pkg/ecs

This game runs on a small custom ECS in `pkg/ecs`. The framework is minimal and opinionated; following its exact conventions is what keeps features composable. Game code lives in `internal/components` and `internal/systems`.

## Process

### Step 1 — Settle the design before coding

If the request adds or changes game behavior — a new mechanic, ability, entity behavior, win/lose condition, anything observable in play — do not start coding. First invoke the `grilling` skill (`grilling:grilling`) on the feature idea and work through its interview rounds; implement only once the design is settled. The grilling output (settled decisions, rejected options) is the design doc for Step 2.

Trivial changes — renames, constant tweaks, formatting, pure refactors with no behavior change — skip the interview and go straight to Step 2.

### Step 2 — Implement, in this order

Each step builds on the previous one; do not reorder.

1. **Model the data.** Define components in `internal/components`: plain structs implementing `ComponentProof()`. Components carry state only — no behavior methods, no game logic.
2. **Model the behavior.** Define systems in `internal/systems`: implement `ID()/OnAttach/OnUpdate/OnDetach`, or use `ecs.NewSystem(...)` for simple update closures. One system, one concern. Query entities with the generic helpers and mutate state through the pointers they return.
3. **Wire it up.** Register systems in `main.go` via `world.AddSystem(...)` — execution order equals registration order, so place each system deliberately in the sequence. Spawn initial entities with `SpawnNewEntityWithComponents`.
4. **Test the logic.** If a new system contains real logic (state machines, simulation, algorithms — not trivial wiring), write a table-driven test: build a World, add the system, spawn entities, run `Iterate` N times, assert component state. See references/testing-conventions.md for when tests are worth it.
5. **Verify.** `go vet ./...` and `go test ./...` pass, and `go run .` still starts.

## References — read on demand, do not preload

- `references/architecture.md` — the full pkg/ecs API contract: components, systems, World lifecycle, iteration order, queries, spawning. Read before Step 2 if any API detail is unclear.
- `references/conventions.md` — file layout, naming rules, and a canonical snippet for every checklist step.
- `references/anti-patterns.md` — mistakes that compile but break the framework's model. Read before finishing Step 2.
- `references/testing-conventions.md` — when a feature needs tests (placeholder, content pending).
- `references/testing-rules.md` — how to test ECS logic (placeholder, content pending).
