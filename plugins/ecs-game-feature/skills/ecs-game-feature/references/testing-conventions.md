# Testing conventions — when a feature needs tests

This file will define when a change in this repo requires a test and what coverage means here. The intended concept: deterministic systems with real logic — state machines, simulation, algorithms — get table-driven tests against a real `World`; trivial wiring (registering a system, spawning an entity), pure data-shape changes, and pure I/O code do not, because a test would only restate the wiring itself.

Placeholder — rules are being drafted and will land here.
