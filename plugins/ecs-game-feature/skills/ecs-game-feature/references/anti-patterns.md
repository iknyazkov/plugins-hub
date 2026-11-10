# Anti-patterns on pkg/ecs

Mistakes that compile fine and even run — but break the framework's model. Each entry: the pattern, why it hurts *in this framework* (grounded in the `pkg/ecs` source), and what to do instead. Read this before finishing any implementation step.

## 1. Using the deprecated string-keyed accessors

**Pattern.** Calling the `// Deprecated:` methods instead of the generic free functions. Exactly four methods carry markers in source:

```go
// World (world.go)
GetEntitiesWith(components ...string) []Entity
// Deprecated: Use instead:
//   - [GetEntitiesWithComponents1]
//   - [GetEntitiesWithComponents2]
//   - [GetEntitiesWithComponents3]
//   - [GetEntitiesWithComponents4]
//   - [GetEntitiesWithComponents5]
//   - [GetEntitiesWithComponents6]

// Entity (entity.go)
GetComponent(key string) Component
// Deprecated: Use [GetComponent] instead.

RemoveComponent(key string) bool
// Deprecated: Use [RemoveComponent] instead.

HasComponent(key string) bool
// Deprecated: Use [HasComponent] instead.
```

**Why it hurts here.** The keys are hand-written type-name strings. A typo (`"Positon"`) compiles, and every lookup silently returns nothing — no error, no panic, just empty results. The generic helpers derive the same key from the type parameter itself (`KeyForComponentType[C]()`), so the compiler guarantees the lookup matches the type you read back.

**Do this instead.**

```go
// No:  entities := world.GetEntitiesWith("Position", "Velocity")
// No:  position := entity.GetComponent("Position").(*components.Position)
entities := ecs.GetEntitiesWithComponents2[*components.Position, *components.Velocity](world)
position := ecs.GetComponent[*components.Position](entity)
present := ecs.HasComponent[*components.Dead](entity)
removed := ecs.RemoveComponent[*components.Dead](entity)
```

## 2. Attaching components as values instead of pointers

**Pattern.** Spawning or adding components by value:

```go
ecs.SpawnNewEntityWithComponents(world, components.Position{X: 1, Y: 2})
```

**Why it hurts here.** Component storage is pointer-based. `GetComponent[C]` returns whatever is stored, type-asserted to `C`, and the framework's contract is "Components are stored as pointers" (get_component.go). When a value `Position{}` was stored, the assertion to `*Position` fails and `GetComponent[*Position]` silently returns the zero value — `nil` — which panics on first field access or, worse, makes a `nil`-check quietly skip all logic for that entity.

**Do this instead.** Always the pointer form, at spawn, at `AddComponents`, and at every generic lookup:

```go
ecs.SpawnNewEntityWithComponents(world, &components.Position{X: 1, Y: 2})
entity.AddComponents(&components.Position{X: 1, Y: 2})
position := ecs.GetComponent[*components.Position](entity) // non-nil, mutations write through
```

## 3. God systems — many concerns in one OnUpdate

**Pattern.** One system whose `OnUpdate` reads input, moves entities, resolves collisions, updates score, and spawns effects.

**Why it hurts here.** The framework's composition model is registration order plus per-system measurement. A god system defeats both:

- It cannot be reordered or replaced independently — the whole frame is one unit.
- `Iterate` measures time per system ID; one fat ID makes the timing warning useless for locating the slow part.
- It cannot be tested in isolation — a table-driven test would have to build the world for five features to exercise one.

**Do this instead.** One system, one concern; let registration order compose them (`input` before `movement` before `collision` before `scoring`). If a group must always run together, bundle them with `ecs.NewComposableSystem(id, logger, subsystems...)` — but prefer plain registration, which keeps reordering a one-line change.

## 4. Game logic inside component methods

**Pattern.** Giving `Position` a `MoveTowards(target Position, dt float64)` method, or `HunterAI` a `NextState()`, and having systems call it.

**Why it hurts here.** In this framework a `Component` is explicitly "a piece of data attached to an Entity" — a marker interface and nothing more. Logic hiding in component methods is invisible to the framework's query-and-mutate flow: it cannot be measured per tick, cannot be reordered relative to other behavior, and drags dependencies between `components` and `systems` packages (components importing systems, or vice versa, tangles the one-directional layout `internal/components` ← `internal/systems`). It also makes the data harder to set up in tests — you cannot construct a state without executing behavior.

**Do this instead.** Components expose plain exported fields, systems own the behavior. The state machine, the movement math, the reaction — all in `OnUpdate`, where it is visible, ordered, timed, and testable.

## 5. Hand-rolling entity iteration

**Pattern.** Copying entities into your own map/slice and ranging over it, reflecting over `world`/`entity` internals, or re-implementing filtering with the deprecated string-keyed `HasComponent` in a loop you built yourself.

**Why it hurts here.** The world's entity storage is an unexported map; the sanctioned access path is `GetEntitiesWithComponents1..6`, which AND-filters and **sorts results by entity ID ascending** — deterministic across ticks and runs. Anything you build yourself iterates a Go map, whose order is randomized: the same entities update in a different order every tick (subtle simulation bugs) and every test run (flaky assertions). You also lose the guaranteed-presence property the typed queries give you.

**Do this instead.** Use the helpers, then narrow:

```go
all := ecs.GetEntitiesWithComponents2[*components.Position, *components.Health](parameters.World)
alive := ecs.ExcludeComponent[*components.Dead](all)
first := ecs.FirstEntity[*components.Health](alive, func(h *components.Health) bool { return h.Amount < 10 })
```

## 6. Registering systems without thinking about order

**Pattern.** Adding `mainWorld.AddSystem(systems.NewScoringSystem())` at whatever line is convenient — end of the list, or worse, before the systems whose output it consumes.

**Why it hurts here.** Execution order **is** registration order — `Iterate` walks the systems slice exactly as `AddSystem` appended it. There are no priorities or dependencies to save you. A scoring system registered before collision resolution reads last tick's state; a cleanup system registered in the middle deletes entities other systems still query that same tick. The bug is invisible in the code and only shows as "off by one tick" behavior.

**Do this instead.** Treat the `AddSystem` list in `main.go` as the frame's pipeline and place each system deliberately: input → simulation → reaction → bookkeeping/cleanup last. When you add a system, note in a comment (or the surrounding grouping) where it sits in the sequence and why — the next person adding a system needs to know the slot conventions, and the tick-budget warning in the world's log will name these IDs when the frame gets slow.
