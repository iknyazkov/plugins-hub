---
description: Set up the websearch engine (fetch latest, uv), commit the engine bump, verify the MCP server
---

Set up the web plugin engine. Perform these steps in order, stopping to
report if one fails:

1. **Engine submodule — init, update, commit the bump.** Work from the hub
   repo root (two levels up from `${CLAUDE_PLUGIN_ROOT}`):

   - If `${CLAUDE_PLUGIN_ROOT}/engine/src/websearch` does not exist,
     initialize the submodule first:

     ```bash
     git -C "${CLAUDE_PLUGIN_ROOT}/../.." submodule update --init plugins/web/engine
     ```

   - Fetch the latest engine — `update --remote` checks out the submodule's
     upstream head (its `origin` on GitHub). Local engine work must be
     pushed first to be picked up.

     ```bash
     git -C "${CLAUDE_PLUGIN_ROOT}/../.." submodule update --remote plugins/web/engine
     ```

   - **If the pointer moved, commit the bump** (hub rule: a content change
     and its version bump share one commit). A non-empty
     `git status --porcelain -- plugins/web/engine` means the pointer
     changed:

     1. Note the new engine SHA: `git -C plugins/web/engine rev-parse --short HEAD`.
     2. Bump the patch `version` in `plugins/web/plugin.json` AND the
        matching entry in `.claude-plugin/marketplace.json` (keep the two
        in sync).
     3. Stage ONLY these three paths — never unrelated working-tree
        changes — and commit:

        ```bash
        git add plugins/web/engine plugins/web/plugin.json .claude-plugin/marketplace.json
        git commit -m "web: engine -> <sha>, <new version>"
        ```

     Report the old -> new SHA and the new version. If the pointer did not
     move, report that the engine is already at the upstream head — no
     commit, no version bump.

2. **Python environment (self-bootstrapping).** Check that `uv` is on PATH
   (`command -v uv`). The MCP server launches via `uv run`, which creates
   and syncs `engine/.venv` automatically on first start — no manual step
   required. Optionally pre-warm it now (surfaces network/dependency errors
   here instead of at server start; always worth it right after an engine
   update):

   ```bash
   uv sync --no-dev --directory "${CLAUDE_PLUGIN_ROOT}/engine"
   ```

3. **Search API configuration (names only — never echo values).** Check via
   Bash whether `WEB_SEARCH_BASE`, `WEB_SEARCH_API_KEY`, and
   `WEB_SEARCH_MODEL` are set in the environment, e.g.
   `[ -n "$WEB_SEARCH_BASE" ] && echo set || echo unset`; also check
   `[ -f "${CLAUDE_PLUGIN_ROOT}/engine/.env" ]` for the credentials file.
   The server takes them from either source (exported vars override `.env`
   values); without them, `WebSearch` fails at request time (`WebFetch`
   works keyless). If neither is configured, offer to create
   `${CLAUDE_PLUGIN_ROOT}/engine/.env` from the `.env.example` template for
   the user to fill in, or tell them to export the vars wherever Claude
   Code is launched from (e.g. `~/.zshenv`) — then restart the session.

4. **Verify.** Tell the user to restart the session or run
   `/reload-plugins`, then check that `/mcp` lists the `websearch` server as
   connected (server info: `websearch v1.0.0`, tools `WebSearch` and
   `WebFetch`). After that `/web:search` exercises both paths.
