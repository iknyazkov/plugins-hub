# peon-ping

Sound notifications for Claude Code: game-character voice lines and banners on
session events — task complete, permission needed, errors, rate limits, session
start. Powered by the upstream [PeonPing/peon-ping](https://github.com/PeonPing/peon-ping)
engine (MIT), wired as a **pinned git submodule** at `engine/`.

Claude Code only. The engine's other IDE adapters are out of scope for this
plugin.

## Setup

Clone the hub with submodules (`git clone --recurse-submodules`), or run
`/peon-ping-setup` once — it initializes the submodule, installs the MCP server
dependencies (`npm ci` in `engine/mcp/`), and downloads a sound pack. Hooks
guard on a missing engine and stay silent until setup is done. Restart the
session or run `/reload-plugins` afterwards.

Until a pack is downloaded, the engine has nothing to play.

## Components

- `hooks/hooks.json` — mirrors the upstream installer's registration one-to-one:
  `SessionStart` (sync), `SessionEnd`, `SubagentStart`, `SubagentStop`,
  `UserPromptSubmit`, `Stop`, `Notification`, `PermissionRequest`, `PreToolUse`,
  `PostToolUseFailure` (matcher `Bash`), `PreCompact`; all async except
  `SessionStart`, timeout 10s. A second `UserPromptSubmit` entry runs the
  upstream `hook-handle-use.sh` / `hook-handle-rename.sh` handlers (timeout 5s,
  sync) for `/peon-ping-use` and `/peon-ping-rename` prompt handling.
- `commands/peon-ping-toggle.md` — master sound on/off.
- `commands/peon-ping-setup.md` — one-time engine setup.
- `commands/peon-ping-config.md` — change settings (volume, pack, rotation,
  categories, notifications, ...). Routes common settings through the engine
  CLI, everything else through direct `config.json` edits.
- `.mcp.json` — the `peon-mcp.js` MCP server (`play_sound` tool for
  agent-chosen sounds) behind a guard wrapper that stays quiet until
  dependencies are installed. Requires Node 18+.

The upstream `/peon-ping-use`, `/peon-ping-log`, `/peon-ping-rename` skills
are not bundled here (decided scope: toggle + setup + config only).

## Updating the engine

```sh
git submodule update --remote plugins/peon-ping/engine
```

Bump this plugin's patch `version` in the same commit (hub rule) so Codex
consumers re-cache.

## Config

Since the engine directory has no `packs/` of its own, the hook runtime
resolves config the upstream way: `~/.claude/hooks/peon-ping/config.json` if
that directory has packs, else `~/.openpeon/config.json`. A project-local
`$PWD/.claude/hooks/peon-ping/config.json` overrides both. `peon.sh status
--verbose` prints the active path on its `config:` line — that is the file
`/peon-ping-config` edits (seeded from the `engine/config.json` template if
missing). The MCP server reads the same locations and honors a `PEON_VOLUME`
env override. State and packs live next to the resolved config
(`~/.openpeon/` on a fresh install).

## Install

```sh
/plugin install peon-ping --marketplace iknyazkov/plugins-hub
```
