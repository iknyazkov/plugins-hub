# plugins-hub — agent guide

This repo is a personal plugin hub. The repo itself IS the marketplace: both Claude Code and Codex consume plugins from here. Keep it the single source of truth.

## Rules

- One plugin = one capability. Plugin name = directory name under `plugins/`, `kebab-case`, English.
- npm-distributed products are the one exception: register them as a marketplace entry with an `npm` source (no `plugins/` directory), pinning the exact package version inside `source`. The version-bump rule does not apply — nothing is cached from the hub tree. Such plugins are Claude Code-only.
- Secrets never live in this repo. Real values stay in the local environment; only variable NAMES may appear. A pre-commit hook (see `hooks/pre-commit`) blocks likely secrets — do not work around it.
- Language: English everywhere — SKILL.md bodies, descriptions, manifests, README. No Russian text in tracked files.
- Harness settings (settings.json, statusline, permissions, env blocks) do NOT belong here — plugins only.

## Creating a new plugin

1. Create the tree: `plugins/<name>/skills/<skill-name>/SKILL.md` (and/or `agents/`, `commands/`, `hooks/`, `.mcp.json` as needed).
2. Add `plugins/<name>/plugin.json`:
   ```json
   { "name": "<name>", "version": "0.1.0", "description": "<one line, English>" }
   ```
3. Register it in `.claude-plugin/marketplace.json` → `plugins` array (`name`, `source: "./plugins/<name>"`, `description`, `version`).
4. Add a row to the plugin table in `README.md`.
5. Validate: `claude plugin validate .` — must pass before committing.
6. Commit. In the same commit as any later content change, bump the patch `version` (in `plugin.json` and the marketplace entry) — Codex re-caches a plugin from the local source only when the version changes.

## Manifests

Each plugin has a single root `plugin.json` (Agent Plugins schema, agent-plugins.org). Claude Code picks the name up from the marketplace entry and auto-discovers the standard layout; if `claude plugin validate` ever demands `.claude-plugin/plugin.json`, add it with the same fields and keep the two in sync.

## Submodules

Some plugins vendor upstreams as pinned git submodules (`plugins/peon-ping/engine`,
`plugins/web/engine`, `plugins/dev/skills/ub-mode`). Update one with
`git submodule update --remote <path>`, then commit the gitlink bump together with the
plugin's patch `version` bump (same rule as any content change).

## Consumers cheat-sheet

- **Claude Code**: marketplace added by local path (`claude plugin marketplace add ~/Documents/plugins-hub`) → live; edits visible after session restart or `/reload-plugins`; no version pinning.
- **Codex**: marketplace added by local path (`codex plugin marketplace add ~/Documents/plugins-hub`) → still a versioned cache under `$CODEX_HOME/plugins/cache/plugins-hub/...`; enable with `codex plugin add <name>@plugins-hub` (or `[plugins."<name>@plugins-hub"] enabled = true` in `config.toml`); changes are picked up when `version` bumps — no push needed.
- **opencode**: no marketplace concept — the configs generator wires `plugins/<name>/skills` directories straight into `opencode.json`; edits are live.
