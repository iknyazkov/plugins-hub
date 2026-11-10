# web

Web research for Claude Code through a self-hosted engine: `/web:search` for
a quick grounded answer (sources read, cited, unverified), `/web:deep-research`
for a deep, multi-source, adversarially fact-checked report. Powered by the
[iknyazkov/websearch](https://github.com/iknyazkov/websearch) MCP server
(Python), wired as a **pinned git submodule** at `engine/` and registered
directly by the plugin's `.mcp.json` — its `WebSearch` / `WebFetch` tools are
available in every session with the plugin enabled, and repeated identical
requests within a session are served from the engine's 15-minute in-memory
cache (both tools carry a `cached` flag; both workflows report cache-hit
counts in their stats).

## Setup

Clone the hub with submodules (`git clone --recurse-submodules`), or run
`/web:setup` — it fetches the engine's upstream head (initializing the
submodule on a fresh clone) and commits the pointer bump with a plugin
version bump when it moved, checks `uv` and the search-API environment, and
tells you how to verify the server. The only host
dependency is `uv` (Python >= 3.11): the server launches via
`uv run`, which creates and syncs `engine/.venv` on first start. Restart the
session or run `/reload-plugins` afterwards; `/mcp` should then show
`websearch` (v1.0.0) as connected.

**Configuration** is environment-based: the server inherits the environment
Claude Code was launched with, plus an optional credentials file.

1. `engine/.env` (recommended for credentials): copy `engine/.env.example`
   to `engine/.env` and fill it in. `make mcp` loads it via
   `uv run --env-file` whenever it exists. It is gitignored and
   machine-local — but it is also copied into the plugin's install cache on
   plugin update, so re-run the `/plugin` update after creating it.
   Variables already exported in the environment override `.env` values.
2. Exported wherever Claude Code starts (e.g. `~/.zshenv`) — the original
   path; also the only one covering GUI launches that don't source shell
   files, unless you use `.env`.

- `WebFetch` works keyless out of the box (local fetcher + content extraction;
  set `WEB_FETCH_BASE`/`_API_KEY`/`_MODEL` to route fetches through a cloud
  API instead).
- `WebSearch` needs `WEB_SEARCH_BASE`, `WEB_SEARCH_API_KEY`, `WEB_SEARCH_MODEL`
  — a Linkup-style cloud search API.
- Optional `HTTPS_PROXY` for the engine's HTTP client.
- The engine treats empty and unset values identically (Go-verbatim env
  reads), so a partially filled `.env` is safe.

Secrets never live in this repo; only variable names (the `.env.example`
template). Note the Agent Plugins spec lets clients sanitize ambient
subprocess environments — if your client does, `.env` is the reliable path
instead of MCP env settings.

## Components

- `.mcp.json` — the `websearch` stdio MCP server: `make -C
  ${CLAUDE_PLUGIN_ROOT}/engine mcp`, an echo-silent Makefile target wrapping
  `uv run --no-dev python -m websearch.main` (the venv self-syncs, so nothing
  under `engine/.venv` is tracked or pre-built); exposes the `WebSearch` and
  `WebFetch` tools. Two hard rules baked into this shape: stdout **is** the
  JSON-RPC channel, so the entry target must never print to it (that's why
  not `make run` — lint/banners would corrupt the protocol stream); and the
  engine path must be `${CLAUDE_PLUGIN_ROOT}`-anchored (a relative
  `--directory ./engine` resolves against the undocumented spawn cwd — the
  0.1.2 failure). `cwd`/`${PLUGIN_ROOT}` are Agent-Plugins-spec constructs
  Claude Code does not expand.
- `hooks/hooks.json` — PreToolUse hook denying the built-in `WebFetch` /
  `WebSearch`: two inline `echo` commands (no scripts), one per matcher, each
  returning a `deny` decision whose reason names the plugin's
  `mcp__plugin_web_websearch__WebFetch` / `WebSearch` as the replacement. The
  reason is shown to the model, steering it to the engine; hooks fire in every
  session while the plugin is enabled, subagents included. Two caveats: a
  timed-out hook does not block (fail-open), and until `/web:setup` is done
  the hook denies the built-ins with no replacement tools available. For hard
  removal from the model's context (tool definition gone entirely), also add
  `"WebFetch"` / `"WebSearch"` to `permissions.deny` in settings.json — a
  plugin cannot ship permission rules itself.
- `commands/setup.md` — `/web:setup`: one-time engine setup + verification.
- `workflows/search.js` — `/web:search <question>`: the quick rung —
  exactly 3 query variants (schema-enforced; enumeration questions get an
  inventory-targeting query; English by default, at most one query in the
  question's language, no quoted phrases or bare year tokens), one
  `WebSearch` call per searcher (empty searchers get one deterministic
  retry with a simplified query), top-7 URL-deduped reads ordered
  relevance-tier first and round-robin across searchers inside a tier — a
  strict global sort starves later searchers' equally-rated results (one
  run cut every official doc that way) — then a deterministic snowball
  round: readers report `leads` (other projects named on the page), failed
  fetches become priority leads, the rest are scored (external link >
  bare name > self-domain link) and up to 3 chased with one search each
  (empty lead searches retried once) and up to 3 new reads. One cited
  synthesis marked unverified; the script rejects answers citing sources
  outside the read set (falls back to raw extracted points); cache-hit
  counts summed from the tools' `cached` flags. Replaced the old
  thin-command version (a bare tool call the agent could make itself); the
  bare `mcp__websearch__WebSearch` call remains the instant-snippets path.
- `workflows/deep-research.js` — the heavy rung, an original implementation:
  Scope → Search → Fetch+Extract → adaptive adversarial Verify → Synthesize.
  Verify panels adapt to evidence strength — a claim sourced primary/secondary
  with a verbatim quote passes on one vote; a refute, low confidence, or an
  errored fast vote escalates to the full 3-vote panel (quorum unchanged) —
  and each verifier carries a 6-call budget on web tools. Web-derived strings
  are fenced in prompts (⟦…⟧) and scrubbed/quoted in terminal labels.

## Updating the engine

```sh
git submodule update --remote plugins/web/engine
```

Bump this plugin's patch `version` in the same commit (hub rule) so Codex
consumers re-cache. `/web:setup` runs the same flow and makes that commit
for you when the pointer moves.

## Notes

- Both workflows are self-contained scripts with no upstream coupling.

## Install

```sh
/plugin install web --marketplace iknyazkov/plugins-hub
```
