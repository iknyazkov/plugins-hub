# Repo conventions for game code

These conventions are established by this skill — `internal/components` and `internal/systems` start empty, so every file added to them sets precedent. Follow these rules so every feature looks the same.

In snippets, `ecs` is the repo's `pkg/ecs`, `components` is `internal/components`, and `<module>` stands for the repo's Go module path from `go.mod`.

## File layout

- **One file per component** in `internal/components/`, named after the component in lowercase: `Position` → `position.go`, `HunterAI` → `hunter_ai.go`, `PlayerControlled` → `player_controlled.go`.
- **One file per system** in `internal/systems/`, named `<system>_system.go` (or `<system>.go` if the name already ends in `System` — pick one form and keep it): `MovementSystem` → `movement_system.go`, `HunterAISystem` → `hunter_ai_system.go`.
- **Tests sit next to the file they test**, standard Go style: `movement_system_test.go`.
- `main.go` stays the only wiring point: world creation, system registration, initial entity spawning.

## Naming

- **Components are nouns**, named for the data they hold: `Position`, `Velocity`, `HunterAI`, `Health`, `Score`. No `Component` suffix, no prefixes.
- **Systems are noun_verb or plain nouns describing their behavior**: `MovementSystem`, `CollisionSystem`, `HunterAISystem`. Keep the `System` suffix in the type name.
- **System IDs are short lowercase snake_case strings, unique across the world**: `MovementSystem.ID()` returns `"movement"`, `HunterAISystem.ID()` returns `"hunter_ai"`. The ID is what `RemoveSystem` matches on and what shows up in the tick timing metrics, so make it readable.

## Canonical snippets

All signatures below are verified against the `pkg/ecs` source. Component and system examples use `Position`, `Velocity`, and `HunterAI` as the reference set.

### (a) A component definition

Plain struct, exported fields, marker method. Components carry state only — no behavior methods, no game logic. Always stored as pointers.

```go
package components

// Position is the world-space location of an entity.
type Position struct {
	X float64
	Y float64
}

func (Position) ComponentProof() {}
```

If the component holds reference values (slices, maps, pointers) and you plan to use `Entity.Clone()`, also implement `ClonableComponent`:

```go
func (i *Inventory) Clone() ecs.ClonableComponent {
	return &Inventory{Items: slices.Clone(i.Items)}
}
```

### (b) A full system implementing the interface

One system, one concern. Empty `OnAttach`/`OnDetach` are fine when there is nothing to set up or tear down.

```go
package systems

import (
	"<module>/internal/components"
	"<module>/pkg/ecs"
)

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

Rules embedded in that snippet:

- Always instantiate the generic type parameters as **pointer types** (`*components.Position`, never `components.Position`).
- Scale by `parameters.DeltaTime` — it is real elapsed seconds, not a fixed step.
- Mutate through the pointers returned by `GetComponent` — there is no write-back call.

### (c) The ecs.NewSystem short form

For simple stateless update logic, skip the struct:

```go
score := ecs.NewSystem("score", func(parameters ecs.UpdateParameters) {
	for _, entity := range ecs.GetEntitiesWithComponents1[*components.Score](parameters.World) {
		score := ecs.GetComponent[*components.Score](entity)
		score.TimeAlive += parameters.DeltaTime
	}
})
mainWorld.AddSystem(score)
```

`OnAttach` and `OnDetach` are no-ops in this form; use the full interface (b) when you need attach-time setup or teardown.

### (d) Registration in main.go — order-aware placement

Execution order **is** registration order, so the `AddSystem` list reads as the frame's pipeline. Place each new system deliberately and keep the list readable as a sequence of phases (input → simulation → reaction → bookkeeping):

```go
func main() {
	mainWorld := ecs.NewWorld("entry", lg)

	// Order is semantics. Input writes intents, movement consumes them,
	// collision reacts to where movement put things, scoring reacts to
	// collision outcomes.
	mainWorld.AddSystem(systems.NewInputSystem())
	mainWorld.AddSystem(systems.MovementSystem{})
	mainWorld.AddSystem(systems.NewCollisionSystem())
	mainWorld.AddSystem(systems.NewScoringSystem())

	// ... ticker loop
}
```

Notes:

- `AddSystem` fires the system's `OnAttach` immediately, at registration time.
- A constructor (`NewInputSystem()`) is the norm for systems carrying state; a struct literal (`MovementSystem{}`) is fine for stateless ones. Pick per system, not per mood.
- If a family of systems must always run in one fixed internal order, `ecs.NewComposableSystem(id, logger, subsystems...)` bundles them — but plain registration keeps reordering easy.

### (e) Spawning an entity with several components

Always pointers, always through the spawn helper (it creates **and** registers):

```go
hunter := ecs.SpawnNewEntityWithComponents(mainWorld,
	&components.Position{X: 100, Y: 100},
	&components.Velocity{},
	&components.HunterAI{Alerted: false},
	&components.Collider{Radius: 12},
)
```

For prefab-style duplication, build one template entity and clone it — the clone gets a fresh ID and is **not** registered until you add it:

```go
template := ecs.NewEntityWithComponents(
	&components.Position{},
	&components.Collider{Radius: 12},
)

for i := 0; i < 10; i++ {
	mainWorld.AddEntity(template.Clone())
}
```

(If the components hold slices/maps/pointers, implement `ClonableComponent` on them or every clone shares that data.)

### (f) Query + mutate inside OnUpdate

The full pattern with post-filtering, presence-guaranteed reads, and targeted mutation:

```go
func (system HunterAISystem) OnUpdate(parameters ecs.UpdateParameters) {
	prey := ecs.ExcludeComponent[*components.PlayerControlled](
		ecs.GetEntitiesWithComponents2[*components.Position, *components.Health](parameters.World),
	)

	for _, hunter := range ecs.GetEntitiesWithComponents2[*components.Position, *components.HunterAI](parameters.World) {
		ai := ecs.GetComponent[*components.HunterAI](hunter)

		if match := ecs.FirstEntity[*components.Position](prey, func(position *components.Position) bool {
			return position.X < ai.SightRange
		}); match != nil {
			ai.Alerted = true // writes through to the entity
		}
	}
}
```

### Logger — where it lives and who uses it

The repo has a thin `*log.Logger` wrapper in `pkg/logger`:

```go
func New(domain string, writers ...io.Writer) *log.Logger  // wraps io.MultiWriter, prefixes domain, log.LstdFlags
func NewFormatWriter(writer io.Writer) io.Writer           // prepends a "2006-01-02 15:04:05 | " timestamp
```

`main.go` wires it once, as a package-level variable, and passes it to the world:

```go
var lg = logger.New("game", logger.NewFormatWriter(os.Stdout))

mainWorld := ecs.NewWorld("entry", lg)
```

Today only the world consumes it (the "world can't perform target FPS in tick" warning). `ecs.NewSystem` takes no logger; `ecs.NewComposableSystem` accepts one but does not use it. There is no per-system logger injection — if a system genuinely needs logging, build its own logger the same way (`logger.New(...)`) and store it on the system struct. Do not add logging by default; the game is a tick loop and it will spam.

## Verification

After any change, all three must hold from the repo root:

```
go vet ./...
go test ./...
go run .    # must still start (kill it once the loop is visibly ticking)
```
