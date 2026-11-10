---
description: Initialize the peon-ping engine (submodule, MCP deps, sound pack) and verify the install
---

Set up the peon-ping engine for this plugin. Perform these steps in order,
stopping to report if one fails:

1. **Engine submodule.** If `${CLAUDE_PLUGIN_ROOT}/engine/peon.sh` does not
   exist, initialize the git submodule from the hub repo root (two levels up
   from `${CLAUDE_PLUGIN_ROOT}`):

   ```bash
   git -C "${CLAUDE_PLUGIN_ROOT}/../.." submodule update --init plugins/peon-ping/engine
   ```

2. **MCP server dependencies.** If
   `${CLAUDE_PLUGIN_ROOT}/engine/mcp/node_modules` is missing:

   ```bash
   npm ci --prefix "${CLAUDE_PLUGIN_ROOT}/engine/mcp" --no-audit --no-fund
   ```

3. **Sound pack.** Download the default pack:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/engine/peon.sh" packs install peon
   ```

   If that command errors or the syntax differs, check
   `bash "${CLAUDE_PLUGIN_ROOT}/engine/peon.sh" packs` usage and the "Sound
   Packs" section of `${CLAUDE_PLUGIN_ROOT}/engine/README.md`, then suggest a
   pack and let the user pick.

4. **Verify.** Run `bash "${CLAUDE_PLUGIN_ROOT}/engine/peon.sh" status` and
   summarize the result.

Finish by telling the user: hooks and the MCP server are picked up by Claude
Code after a session restart or `/reload-plugins`; they can test with
`/peon-ping-toggle`.
