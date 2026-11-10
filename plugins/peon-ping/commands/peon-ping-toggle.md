---
description: Toggle peon-ping sound notifications on/off
---

Toggle peon-ping sounds on or off.

Run the following command with the Bash tool:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/engine/peon.sh" toggle
```

Report the output to the user. The command prints either:
- `peon-ping: sounds paused` — sounds are now muted
- `peon-ping: sounds resumed` — sounds are now active

This toggles the master audio switch (`enabled` config): sounds, desktop
notifications, and mobile notifications all stop when paused.

If the command fails because the engine is not initialized, tell the user to
run `/peon-ping-setup` first.
