# plugins-hub

A personal plugin hub for Claude Code, Codex and opencode. The repo itself IS
the marketplace: skills, agents, commands, hooks and MCP servers — all packaged
as plugins. Secrets never live in this repo; only environment-variable NAMES do.

## Install

Clone with `--recurse-submodules`: the web/peon-ping engines and the ub-mode
skill are pinned git submodules.

### Claude Code

```sh
claude plugin marketplace add https://github.com/iknyazkov/plugins-hub
claude plugin install grilling@plugins-hub
```

### Codex

```sh
git clone --recurse-submodules https://github.com/iknyazkov/plugins-hub
codex plugin marketplace add ./plugins-hub
codex plugin add grilling@plugins-hub
```

Codex caches plugins under `$CODEX_HOME/plugins/cache/...`; a plugin's changes
are picked up when its `version` bumps (see AGENTS.md).

### opencode

No marketplace concept — point `opencode.json` at the `plugins/<name>/skills`
directories directly.

## Plugins

| Plugin | What it does | Consumers |
|--------|--------------|-----------|
| `grilling` | Stress-test plans through design-tree interview rounds | CC, Codex, opencode |
| `code-migration` | Six-step large-scale language migration kit: map and rules, rule stress-test, translation, compile, run, behavior parity | CC, Codex, opencode |
| `ecs-game-feature` | Implement game features on a custom Go ECS: design interview via grilling first, then components, systems, wiring, tests | CC, Codex, opencode |
| `peon-ping` | Game-character sound notifications on session events (engine is a git submodule); hooks, toggle/config commands, MCP | CC |
| `refactoring` | Structured, rules-guided refactoring pass: readability over speed | CC, Codex, opencode |
| `web` | Web search and deep research through the self-hosted websearch engine (git submodule wired as MCP, 15-minute cache): `/web:search` — quick grounded answer (sources read, cited, unverified), `/web:deep-research` — adversarially verified report | CC |
| `dev` | Working principles for code changes via the ub-mode skill (pinned submodule of [unclebob/ub-mode](https://github.com/unclebob/ub-mode)); `/dev:ub-setup` installs the external analysis tools (crapper, dryer, mutator) | CC, Codex, opencode |
| `handoff` | Session handoff: `/handoff:handoff` compacts the dialog into a document (suggested skills, artifacts by path/URL, no secrets), `/handoff:pick-up` resumes from it; queue of up to 3 files per project in the OS temp dir, managed by `handoff.py` | CC, Codex, opencode |
| `rea` | Reverse engineering for agents — native binaries, Electron/JavaScript apps, .NET assemblies, packaged apps, firmware, websites; MCP server from the pinned npm package [rea-agents](https://www.npmjs.com/package/rea-agents) | CC |
| `channel` | Telegram remote control for agent sessions: plugin hooks + tmux paste put bot messages into the session and replies/approvals back (PermissionRequest hook waits for the Telegram verdict); resident MCP bridge as the no-tmux fallback; vendored [channelsrv](plugins/channel/channelsrv/) daemon | CC |

## Credits

- [grilling](https://github.com/mattpocock/skills/tree/main/skills/productivity/grilling)
  and [handoff](https://github.com/mattpocock/skills/tree/main/skills/productivity/handoff) —
  from Matt Pocock's [skills](https://github.com/mattpocock/skills) (MIT): grilling is a
  near-verbatim copy, handoff has been substantially rewritten (see each plugin's NOTICE).
- `dev` — the ub-mode skill, tracked as a pinned submodule of
  [unclebob/ub-mode](https://github.com/unclebob/ub-mode) (MIT).
- `code-migration` — packaged from Anthropic's code-migration kit (Apache-2.0, see the
  plugin's LICENSE).
- `peon-ping` — engine upstream [PeonPing/peon-ping](https://github.com/PeonPing/peon-ping)
  (MIT), pinned as a submodule.
- `web` — engine upstream [iknyazkov/websearch](https://github.com/iknyazkov/websearch),
  pinned as a submodule.
- `rea` — not vendored: installed from the npm package
  [rea-agents](https://www.npmjs.com/package/rea-agents) (MIT, © morluto), pinned at 6.8.0.

## Adding a plugin

The guide lives in [AGENTS.md](AGENTS.md) (for agents and humans).
