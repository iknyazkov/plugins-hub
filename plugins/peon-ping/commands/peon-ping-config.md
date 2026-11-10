---
description: Change peon-ping settings — volume, pack, rotation, categories, notifications, and more
argument-hint: "[setting] [value]"
---

Change peon-ping configuration. User request (may be empty): $ARGUMENTS

To mute/unmute sounds entirely, use `/peon-ping-toggle` instead of editing
the `enabled` key.

## Route through the CLI when possible

Run all commands with the Bash tool as:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/engine/peon.sh" <subcommand>
```

Prefer the CLI — it keeps config, state, and adapter sync consistent:

| Setting | Subcommand |
| --- | --- |
| Volume | `volume [0.0-1.0]` (no arg shows current) |
| Active pack | `packs use <name>` or `packs use --install <name>` |
| Install packs | `packs install <p1,p2>`, `packs list` / `packs list --registry` |
| Rotation | `rotation <random\|round-robin\|session_override>`, `packs rotation add <pack>` |
| Per-directory pack | `packs bind <name>` / `packs unbind` / `packs bindings` |
| Desktop notifications | `notifications on\|off`, `notifications position <pos>`, `notifications dismiss <N>`, `notifications label <text\|reset>`, `notifications test` |
| Debug logging | `debug on\|off\|status` |
| Single sounds off | `sounds list`, `sounds disable <category> <file>`, `sounds enable <category> <file>` |
| Preview sounds | `preview [category]`, `preview --list` |

If no route above fits and the request maps to an undocumented subcommand,
check `bash "${CLAUDE_PLUGIN_ROOT}/engine/peon.sh" help` first.

## Everything else — edit config.json directly

Settings without a CLI route (e.g. `categories.*`, `annoyed_threshold`,
`silent_window_seconds`, `suppress_*`, `tts.*`, `trainer.*`):

1. Find the active config file — use the path from the `config:` line of:
   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/engine/peon.sh" status --verbose
   ```
2. If that file does not exist yet, seed it from the bundled default template
   first:
   ```bash
   cp "${CLAUDE_PLUGIN_ROOT}/engine/config.json" <config-path>
   ```
3. Read the file, edit only the keys the user asked about, and keep the JSON
   valid. Do not reformat, reorder, or "clean up" unrelated keys.

Sound categories (for `categories.*`): `session.start`, `task.acknowledge`,
`task.complete`, `task.error`, `input.required`, `resource.limit`,
`user.spam`.

## Verify and report

Run `bash "${CLAUDE_PLUGIN_ROOT}/engine/peon.sh" status` and summarize what
changed. Config edits apply to the next hook invocation — no reload needed.

If the engine is not initialized, tell the user to run `/peon-ping-setup`
first.
