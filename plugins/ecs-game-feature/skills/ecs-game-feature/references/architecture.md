# pkg/ecs — API contract

Everything below is derived from the framework source in `pkg/ecs`. The package implements a minimal entity-component-system: entities are identifiable containers of `Component` values keyed by their concrete Go type; `System` implementations query entities from a `World` and mutate their components; `ControlWorld.Iterate` runs all registered systems once per tick.

In all snippets, `ecs` refers to this repo's `pkg/ecs` package and `components` to `internal/components`. Where an import path appears, `<module>` stands for the repo's Go module path from `go.mod` — substitute the real one.

## Contents

- [World](#world)
- [Iterate — what one tick does](#iterate--what-one-tick-does)
- [The main loop (main.go)](#the-main-loop-maingo)
- [Systems](#systems)
- [Short systems — ecs.NewSystem](#short-systems--ecsnewsystem)
- [Composer systems](#composer-systems)
- [Components](#components)
- [Entities and spawning](#entities-and-spawning)
- [Queries](#queries)
- [Component access on an entity](#component-access-on-an-entity)
- [Deprecated API — do not use](#deprecated-api--do-not-use)

## World

```go
func NewWorld(id string, logger *log.Logger) ControlWorld
```

`NewWorld` creates a world with a string ID and a `*log.Logger` (the world uses it only for the tick-budget warning described below). The concrete implementation is the exported `DefaultWorld` struct with unexported fields, returned as the `ControlWorld` interface; you work through the two interfaces:

```go
type World interface {
	ID() string

	AddSystem(s System)
	RemoveSystem(s System)

	AddEntity(entity Entity)
	GetEntity(id int) (Entity, error)

	// Deprecated: Use instead:
	//   - [GetEntitiesWithComponents1]
	//   - ... through [GetEntitiesWithComponents6]
	GetEntitiesWith(components ...string) []Entity
	RemoveEntity(id int)
}

type ControlWorld interface {
	World

	Iterate(parameters IterationParameters)
}
```

Behavior verified in source:

- `AddSystem(s)` appends the system to the world's list **and immediately calls `s.OnAttach(AttachParameters{World: world})`** — attach side effects happen at registration time, not at the first tick.
- `RemoveSystem(s)` removes the registered system whose `ID()` equals `s.ID()` (matching is by ID string, not by pointer identity), then calls `s.OnDetach(DetachParameters{World: world})`.
- `AddEntity(entity)` registers an entity under its `entity.ID()`; `GetEntity(id)` returns it or an error ("entity was not found for id: %d"); `RemoveEntity(id)` deletes it.
- Systems run in the order they were added. There is no priority, no tags, no automatic ordering — registration order is the whole ordering model.

## Iterate — what one tick does

```go
type IterationParameters struct {
	DeltaTime float64
	TargetFPS int
}

func (world *DefaultWorld) Iterate(parameters IterationParameters)
```

`Iterate` is the tick. For each registered system, in registration order, it calls:

```go
system.OnUpdate(UpdateParameters{World: world, DeltaTime: parameters.DeltaTime})
```

with these guarantees:

1. **Registration order.** Systems execute in exactly the order `AddSystem` was called, every tick.
2. **Per-system timing.** Each `OnUpdate` call is wrapped in `Measure` (a `time.Now()` before/after difference); durations are collected into a map keyed by `system.ID()`.
3. **Tick-budget warning.** After the loop, if the total cycle time exceeds `1/float64(parameters.TargetFPS)` seconds, the world logs `"world can't perform target FPS in tick. Systems time: %v"` with the per-system durations map. `TargetFPS` thus doubles as the tick budget: at `TargetFPS: 10` the whole tick chain must finish within 100 ms or you get the warning.
4. **DeltaTime propagation.** Only `DeltaTime` flows from `IterationParameters` into every system's `UpdateParameters`; `TargetFPS` is used by the world alone.

```go
type UpdateParameters struct {
	World     World
	DeltaTime float64
}

type AttachParameters struct {
	World World
}

type DetachParameters struct {
	World World
}
```

## The main loop (main.go)

The game's `main.go` is the canonical wiring point. Its loop contract:

```go
import (
	"os"
	"time"

	"<module>/pkg/ecs"
	"<module>/pkg/logger"
)

const fps time.Duration = 10

var lg = logger.New("game", logger.NewFormatWriter(os.Stdout))

func main() {
	mainWorld := ecs.NewWorld("entry", lg)

	interval := time.Second / fps

	ticker := time.NewTicker(interval)
	lastTime := time.Now()
	defer ticker.Stop()

	for {
		<-ticker.C
		now := time.Now()
		delta := now.Sub(lastTime).Seconds()
		lastTime = now
		mainWorld.Iterate(ecs.IterationParameters{DeltaTime: delta, TargetFPS: int(fps)})
	}
}
```

Contract, point by point:

- A `time.Ticker` at `time.Second / fps` drives the ticks.
- `DeltaTime` is the **real elapsed time since the previous tick**, in seconds (not a fixed step) — systems must scale movement and timers by `parameters.DeltaTime`.
- `TargetFPS` is `int(fps)` and must match the ticker rate for the tick-budget warning to be meaningful.
- Systems are registered between `NewWorld(...)` and the `for` loop; initial entities are spawned there too. Nothing else in the loop is game-specific.

## Systems

```go
// System is a unit of logic that processes the entities of a [World] on
// every iteration.
type System interface {
	ID() string
	OnAttach(parameters AttachParameters)
	OnUpdate(parameters UpdateParameters)
	OnDetach(parameters DetachParameters)
}
```

- `ID() string` must be unique across the world: it is the key for `RemoveSystem` matching and for the per-tick timing metrics.
- `OnAttach` fires once, synchronously inside `AddSystem` — use it for setup that needs the `World` (e.g. spawning state entities or capturing references).
- `OnUpdate` fires once per `Iterate` call (once per tick) with the world and delta time.
- `OnDetach` fires inside `RemoveSystem`.

A full system implementation:

```go
type MovementSystem struct{}

func (MovementSystem) ID() string { return "movement" }

func (system MovementSystem) OnAttach(parameters ecs.AttachParameters) {}

func (system MovementSystem) OnUpdate(parameters ecs.UpdateParameters) {
	for _, entity := range ecs.GetEntitiesWithComponents2[*components.Position, *components.Velocity](parameters.World) {
		position, velocity := ecs.GetComponent2[*components.Position, *components.Velocity](entity)
		position.X += velocity.X * parameters.DeltaTime
		position.Y += velocity.Y * parameters.DeltaTime
	}
}

func (system MovementSystem) OnDetach(parameters ecs.DetachParameters) {}
```

## Short systems — ecs.NewSystem

For systems that only need an update closure:

```go
func NewSystem(id string, updateFunc func(parameters UpdateParameters)) System
```

`OnAttach` and `OnDetach` are no-ops in this form. Use it for simple, stateless logic; reach for the full interface when you need attach-time setup or teardown.

```go
tick := ecs.NewSystem("tick", func(parameters ecs.UpdateParameters) {
	for _, entity := range ecs.GetEntitiesWithComponents1[*components.Position](parameters.World) {
		ecs.GetComponent[*components.Position](entity).X += 1 * parameters.DeltaTime
	}
})
mainWorld.AddSystem(tick)
```

## Composer systems

```go
type ComposerSystem interface {
	System

	Systems() []System
}

func NewComposableSystem(id string, logger *log.Logger, subsystems ...System) ComposerSystem
```

`NewComposableSystem` groups subsystems under one ID. Its `BaseComposerSystem` forwards `OnAttach`, `OnUpdate` and `OnDetach` to each subsystem **in the order the subsystems were passed in**. Registering one composer system therefore registers the whole chain with a fixed internal order — useful for bundling, but the bundle's order is frozen at construction.

The `logger` argument is stored on the composer but is not used by the forwarded lifecycle calls; a subsystem that wants to log captures its own logger.

## Components

```go
// Component is a piece of data attached to an [Entity].
//
// Any type can be a component; components are matched by their concrete Go
// type, so an entity holds at most one component of each type.
type Component interface {
	ComponentProof()
}
```

- Any type can be a component — implement the `ComponentProof()` marker method, that is the entire requirement.
- Components are keyed per entity by their **concrete Go type name** (`KeyForComponentType[C]()` / `KeyForComponent(component)` return `reflect.Type.Name()` with the pointer stripped). Consequences:
  - An entity holds **at most one component of each concrete type** — `AddComponents` with an already-present type replaces it.
  - `Position` and `*Position` map to the same key `"Position"`.
- **Components are stored as pointers.** Pass `*Position` when attaching/spawning, and read back with `GetComponent[*Position]` — the framework's lookups are built around pointer storage (see [Component access](#component-access-on-an-entity)).

```go
// ClonableComponent is implemented by components whose copying needs
// more than assignment — for example, components holding reference
// values. Clone must return an independent copy of the component.
//
// Components made only of basic values — int, string and similar
// fields — do not implement it; assignment already copies them fully.
type ClonableComponent interface {
	Component
	Clone() ClonableComponent
}
```

`ClonableComponent` matters only for `Entity.Clone()`. Components made only of basic values need nothing extra. Any component that holds reference values (slices, maps, pointers, channels, funcs) should implement `Clone() ClonableComponent` returning an independent copy — otherwise two "clones" share the referenced data.

```go
type Inventory struct {
	Items []string
}

func (i *Inventory) ComponentProof() {}

func (i *Inventory) Clone() ecs.ClonableComponent {
	return &Inventory{Items: slices.Clone(i.Items)}
}
```

## Entities and spawning

```go
func NewEntity() Entity
func NewEntityWithComponents(components ...Component) Entity
func SpawnNewEntityWithComponents(world World, components ...Component) Entity
```

- **IDs are assigned sequentially, starting at 1, and are unique across all worlds** (a single global counter, so two worlds never hand out the same ID).
- `NewEntity` / `NewEntityWithComponents` create **unregistered** entities; nothing sees them until `world.AddEntity(entity)`.
- `SpawnNewEntityWithComponents(world, components...)` is the one-call form: it creates the entity with the given components and registers it with `world`, returning the registered entity. This is the canonical way to spawn.

```go
player := ecs.SpawnNewEntityWithComponents(mainWorld,
	&components.Position{X: 10, Y: 10},
	&components.Velocity{},
	&components.Collider{Radius: 8},
)
```

Other entity operations:

- `entity.AddComponents(components ...Component)` — adds each component, **replacing any component of the same concrete type**.
- `entity.Clone() Entity` — returns a new entity with a **new ID** and copies of all components. Components implementing `ClonableComponent` are copied by their own `Clone`; basic-value components need nothing special. **The clone is not registered with any world** — register it with `World.AddEntity` to make it visible to systems. This is the prefab pattern: build one template entity, clone-and-register N times.

## Queries

The generic free functions are the **canonical** query API (the string-keyed alternative is deprecated — see the last section).

### Entity queries

```go
func GetEntitiesWithComponents1[C1 Component](world World) []Entity
func GetEntitiesWithComponents2[C1, C2 Component](world World) []Entity
func GetEntitiesWithComponents3[C1, C2, C3 Component](world World) []Entity
func GetEntitiesWithComponents4[C1, C2, C3, C4 Component](world World) []Entity
func GetEntitiesWithComponents5[C1, C2, C3, C4, C5 Component](world World) []Entity
func GetEntitiesWithComponents6[C1, C2, C3, C4, C5, C6 Component](world World) []Entity
```

- **AND filter**: an entity is returned only if it has every listed component type.
- Results are **sorted by entity ID in ascending order**, so iteration is deterministic across ticks and runs — important for tests and for stable update order.
- Instantiate the type parameters with the pointer types: `GetEntitiesWithComponents2[*components.Position, *components.Velocity](world)`.

```go
func GetFirstEntity[C Component](world World) Entity
```

Returns the first entity (lowest ID) with component `C`, or `nil` if none exists.

### Post-filtering a slice you already have

```go
// FirstEntity returns the first entity whose C component satisfies match,
// or nil if no entity does.
func FirstEntity[C Component](entities []Entity, match func(component C) bool) Entity

func ExcludeComponent[C Component](entities []Entity) []Entity
func ExcludeComponents2[C1, C2 Component](entities []Entity) []Entity
func ExcludeComponents3[C1, C2, C3 Component](entities []Entity) []Entity
```

These compose with the queries above: `ExcludeComponent[*components.Dead](GetEntitiesWithComponents2[...](world))` drops entities that have the excluded component; `ExcludeComponents2/3` drop entities that have **any** of the excluded components.

## Component access on an entity

```go
func GetComponent[C Component](entity Entity) C
func GetComponent2[C1, C2 Component](entity Entity) (C1, C2)
func GetComponent3[C1, C2, C3 Component](entity Entity) (C1, C2, C3)
func GetComponent4[C1, C2, C3, C4 Component](entity Entity) (C1, C2, C3, C4)
func GetComponent5[C1, C2, C3, C4, C5 Component](entity Entity) (C1, C2, C3, C4, C5)
func GetComponent6[C1, C2, C3, C4, C5, C6 Component](entity Entity) (C1, C2, C3, C4, C5, C6)

func HasComponent[C Component](entity Entity) bool
func HasComponent2[C1, C2 Component](entity Entity) bool   // AND
func HasComponent3[C1, C2, C3 Component](entity Entity) bool // AND
func HasComponent4[C1, C2, C3, C4 Component](entity Entity) bool // AND

func RemoveComponent[C Component](entity Entity) bool
```

Two properties drive every usage pattern:

1. **`GetComponent` returns the stored pointer, so mutations write through.** From the source docs: "Components are stored as pointers, so the returned pointer refers to the stored component: mutating it changes the entity." This is *the* way to mutate state — no setter, no write-back call. With `C = *Position`, `ecs.GetComponent[*Position](entity).X = 5` updates the entity.
2. **A missing component yields the zero value of `C`**, not an error. With pointer types that is `nil` — nil-check when presence is not guaranteed by your query, or use `HasComponent` first. (Queries with `GetEntitiesWithComponentsN` already guarantee presence of the listed types.)

The canonical query-and-mutate loop inside `OnUpdate`:

```go
func (system HunterAISystem) OnUpdate(parameters ecs.UpdateParameters) {
	for _, hunter := range ecs.GetEntitiesWithComponents2[*components.Position, *components.HunterAI](parameters.World) {
		ai, position := ecs.GetComponent2[*components.HunterAI, *components.Position](hunter)
		if !ai.Alerted {
			continue
		}
		ai.StateTicks += parameters.DeltaTime // writes through to the entity
		position.X += ai.StepSize * parameters.DeltaTime
	}
}
```

And the exclusion pattern for "has these, but not those":

```go
alive := ecs.ExcludeComponent[*components.Dead](
	ecs.GetEntitiesWithComponents2[*components.Position, *components.Health](parameters.World),
)
```

## Deprecated API — do not use

Four methods carry `// Deprecated:` markers in source. The generic free functions listed above replace all of them.

| Deprecated | Marker comment | Use instead |
|---|---|---|
| `World.GetEntitiesWith(components ...string) []Entity` | "Deprecated: Use instead: [GetEntitiesWithComponents1] … [GetEntitiesWithComponents6]" | `GetEntitiesWithComponents1..6` |
| `Entity.GetComponent(key string) Component` | "Deprecated: Use [GetComponent] instead." | `ecs.GetComponent[C](entity)` |
| `Entity.RemoveComponent(key string) bool` | "Deprecated: Use [RemoveComponent] instead." | `ecs.RemoveComponent[C](entity)` |
| `Entity.HasComponent(key string) bool` | "Deprecated: Use [HasComponent] instead." | `ecs.HasComponent[C](entity)` |

The string-keyed forms take hand-derived type-name strings — a typo compiles and silently returns nothing, while the generic helpers derive the key from the type parameter itself. See `anti-patterns.md` for the full write-up.
