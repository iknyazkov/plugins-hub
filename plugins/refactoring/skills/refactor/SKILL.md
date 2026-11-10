---
name: refactor
description: Apply a structured, language-agnostic refactoring pass to any package or set of packages. Use when the user asks to "refactor" code, wants a refactor guided by rules. Produces readable, understandable code — no corners cut for speed.
---

## Prime directive

We do not cut corners for speed. The output must be **readable and understandable**. If a faster change would leave the code harder to read, reject it and do it the readable way.

## Rules

### Split packages by responsibility
A package owns one responsibility. When a file or group of files answers a different question than its parent package ("how do I tail a file?" vs. "how do I parse a log line?"), move them into their own package. Name the package after what it does, not after a generic "util" or "dto" bucket.

### Split files by logical content, not as a dumping ground
When a package has many distinct concepts (events, entities, tags, zones), give each a file named after what it contains — `event.go`, `entity.go`, `zone.go`. Do **not** name files `dto.go`, `types.go`, `misc.go`, or `common.go`; a file's name must describe what is inside it.

### Complex loops and deep nesting are broken up

Deeply nested loops and conditionals are hard to read and hard to trust at a glance. If a loop body needs more than 2–3 levels of nesting (loop inside loop inside conditional, etc.), extract the inner logic into a well-named function. A loop's purpose should be obvious without tracing through multiple indentation levels.
- Prefer early `return`/`continue`/`break` over nesting another `if`.
- If a loop body does more than one distinct thing, pull it into a function, e.g. `processEntity(entity)` instead of 15 inline lines mixing lookup, validation, and mutation.
- Target no more than 3 levels of nesting per function — a 4th level is your signal to extract.
- The extracted function name should describe what it does, same bar as any other name in this doc.

### Keep functions and methods short
No method or function exceeds 20 lines (count the body, not the signature). When one does, extract focused helpers named after what they do. A long function is a smell that responsibilities are tangled together.

### Prefer pure functions over unnecessary methods
If a method does not read or write its receiver — or only uses it as overhead — make it a package-level function. Keep a receiver only when the method genuinely depends on the receiver's state. Pure functions are easier to test, reason about, and reuse.

### Names are readable and understandable
Names describe intent. Single letters and terse abbreviations (`i`, `j`, `k`, `n`, `l`, `m`, `s`, `v`, etc.) are **not approved** outside a trivial index in a tight loop. Use `entityID`, `cardName`, `consumeOffset`, `latestPath`. Read the name aloud — if it doesn't describe what the variable holds, rename it. This included Go-style short receivers when they hide meaning: `w` for `Watcher` is tolerable; `s` for a scanner is not.


### Inject what varies, default only what doesn't
A parameter that genuinely varies per caller must be injected (e.g. poll interval). A parameter that never varies in practice must not be injectable — it's a buildable default. Do not ship a default for an injected parameter; if it's injected, the caller owns it.

### Make magic values and repeatable strings constants
Literals that repeat (filenames, prefixes, separators, buffer sizes, channel buffer sizes, key strings) become named constants. One source of truth is better than many copies that can drift apart.

### Avoid nested structures inside a function
Do not nest loops inside `if` inside `loop` inside `if`. Flatten with early returns, extracted helpers, or guards. A reader should not track indentation depth past two levels to follow the logic.

### Comments explain why, not what

A comment that just restates the name or type below it is noise — delete it, or fold its meaning into the name instead. `Line string // raw line for fallback/debug` should become `RawFallbackLine string`, no comment needed.
- Don't write doc comments that only repeat the identifier in prose.
- Keep comments for things the code can't say: non-obvious constraints, workarounds, or caveats.
- Tempted to comment because a name is unclear? Rename it instead.


## How to apply

1. Read every file in the target scope end to end before editing. Trace the real flow, don't guess.
2. Identify responsibilities and propose the package/file split.
3. Apply rules.
4. Build and run the tests. Fix what breaks.
