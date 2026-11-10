# Testing rules — how to test ECS logic

This file will define HOW to test logic in this repo: build a `World` with `ecs.NewWorld`, register the system under test, spawn entities with `ecs.SpawnNewEntityWithComponents`, run `Iterate` N times with fixed `IterationParameters`, and assert on component state read back through `ecs.GetComponent` — table-driven style, one case per scenario. Because entity queries are sorted by ID ascending and `DeltaTime` is passed in, tests are deterministic without mocks.

Placeholder — rules are being drafted and will land here.
