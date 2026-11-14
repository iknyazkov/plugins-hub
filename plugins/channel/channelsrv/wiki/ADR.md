# Architecture decision records

Oldest first; see [SCHEMA.md](SCHEMA.md) for the format.

## ADR-0001 — Stationary daemon, transient agents

**Status:** accepted (decided ~2026-11-08, recorded 2026-11-14)

**Context:** agent sessions (Claude Code, Codex…) start and die constantly,
while the Telegram bot must be reachable around the clock; Telegram allows
only one `get_updates` poller per bot token.

**Decision:** one resident daemon per machine owns the bot and all routing;
agent sessions are clients that register over a UDS socket and deregister
when they exit.

**Consequences:** the daemon must be robust (see ADR-0008); agents are
fungible and stateless from the daemon's perspective; a second daemon on
another machine can't share the token (ADR-0002).

**Alternatives considered:** per-session bot connections — rejected: 409
conflicts, no single point of routing.

## ADR-0002 — One token = one daemon

**Status:** accepted (decided ~2026-11-10, recorded 2026-11-14)

**Context:** a second machine polling the same bot token gets 409s on
`get_updates` while outbound sends still work — silent half-breakage.

**Decision:** one bot token serves exactly one daemon; multi-machine setups
get one token per machine from @BotFather.

**Consequences:** token management is a real deployment concern; the docs
call it out.

## ADR-0003 — Hooks + tmux paste primary, MCP fallback; CC channels stripped

**Status:** accepted (decided ~2026-11-09, contract stripped in 0.1.13,
recorded 2026-11-14)

**Context:** Claude Code's native channels proved too raw: per-session
`--channels` flags, third-party providers refused.

**Decision:** primary transport is tmux: the registration hook pastes
incoming Telegram text into the agent's pane, hooks (Stop, PermissionRequest)
send replies and approvals. The MCP bridge remains as fallback for non-tmux
sessions. The CC-channels contract code was stripped from the bridge and must
never be re-added (`claude/channel` capability, `notifications/claude/channel`).

**Consequences:** interactive tmux is the rich path (aliases, /command
passthrough); zombie MCP bridge processes can hijack routing — check
`ps aux | grep channel_mcp` when routing lands in a dead buffer.

**Alternatives considered:** CC channels as primary — rejected (see above);
MCP-only — rejected: no pane interactivity.

## ADR-0004 — Deny AskUserQuestion; structured questions as plain text

**Status:** accepted (decided ~2026-11-09, recorded 2026-11-14)

**Context:** `AskUserQuestion`'s dialog is terminal-local; over the channel
it either can't render or stalls (approval-card variant never worked).

**Decision:** the channel hook denies `AskUserQuestion` instantly; agents
ask structured questions as plain text through the channel; approvals use
the daemon's ✅/❌ cards.

**Consequences:** agents in channel sessions must phrase questions in text;
the instruction lives in the plugin's MCP server description.

## ADR-0005 — `.env` fill-gaps export into the daemon env

**Status:** accepted (decided ~2026-11-09, recorded 2026-11-14)

**Context:** the daemon is spawned by whatever session touched it first, so
feature knobs can't ride its parent env; also, bare `claude` panes die
instantly on this machine (403 to api.anthropic.com) — daemon-spawned
sessions need the working `zclaude`/`zaiclaude` aliases.

**Decision:** every key in `~/.config/channelsrv/.env` is exported into the
daemon environment, fill-gaps style — the real process env wins. Keys like
`CHANNEL_AI_AGENT` (default agent command for throwaway sessions) live there.

**Consequences:** `.env` is config surface, not just credentials (0600
nonetheless); misconfigured spawn aliases fail fast at registration with
"died immediately".

**Alternatives considered:** per-feature config files — rejected: more
moving parts for the same result.

## ADR-0006 — In-memory registry, stale pins fall through

**Status:** accepted (decided ~2026-11-09, recorded 2026-11-14)

**Context:** agent churn is the norm; persisting registrations would resurface
dead panes after every restart.

**Decision:** the registry is in-memory only, wiped on daemon restart. A
pinned agent that's gone falls through to auto-selection; a pin to a
present-but-offline agent still requires an explicit re-pick.

**Consequences:** after a daemon restart users may need to re-pick an agent;
acceptable churn cost.

**Alternatives considered:** persistent registry — rejected: resurrects
zombies, contradicts ADR-0001.

## ADR-0007 — Liveness = connection or pane, not silence

**Status:** accepted (decided ~2026-11-09, recorded 2026-11-14)

**Context:** MCP bridges hold a long-poll connection, so silence marks death;
tmux agents only touch the daemon during turns, so a silent thinking agent
is normal.

**Decision:** MCP bridge liveness = live connection, silence >30s → offline;
tmux agents are exempt from the silence sweep — their liveness is pane
existence (`pane_alive`).

**Consequences:** a dead tmux pane is noticed lazily (on routing), not
proactively; fine, since the daemon never manages live agent processes
(ADR-0001).

## ADR-0008 — Never kill on a slow healthz; self-healing poll loop

**Status:** accepted (decided 2026-11-10, commit 8eaa362, recorded 2026-11-14)

**Context:** the bridge's `ensure_daemon()` killed daemons that missed a
0.3s healthz budget; a flaky network day turned that into spawn storms
(~34 daemon respawns in 5 minutes) and "hung" mid-turn sessions; separately,
a dead long-poll connection never recovered on its own after Mac sleep.

**Decision:** distinguish "socket not answering" from "pid actually dead":
probe healthz with 2.5s + 8s grace; `stop_wedged_daemon` kills only a
confirmed-live pid and logs the reason. On the Telegram side, a PollWatchdog
restarts polling in place after 5 min without a completed `getUpdates`
(15s request timeouts; 3 fruitless restarts → clean shutdown so hooks
respawn fresh).

**Consequences:** stalls no longer cascade into daemon churn; diagnostics
live in `server.log` under `channelsrv.bridge:`.

**Alternatives considered:** keeping the tight budget with backoff —
rejected: it still murdered live daemons.

## ADR-0009 — Progress chain tails the transcript

**Status:** accepted (decided 2026-11-13, recorded 2026-11-14)

**Context:** users want to see the turn as it happens, not just the final
reply; hooks could stream events, but the transcript already is an
append-only record of completed blocks.

**Decision:** agents register with a `transcript_path`; the daemon tails the
JSONL, rendering assistant text and tool calls as an in-place-edited Telegram
message (2.5s throttle, fresh message before the 4096-char cap; thinking
blocks and tool results never shown). The tail re-opens on any op, surviving
a mid-turn daemon restart; the final Stop reply is skipped when the chain
already shows it verbatim.

**Consequences:** progress depends on registration carrying the transcript
path; restart mid-turn costs at most a duplicated partial chain.

**Alternatives considered:** hook-driven streaming — rejected: more moving
parts; the transcript tail is self-healing.

## ADR-0010 — `/context` reports absolute tokens

**Status:** accepted (decided ~2026-11-09, recorded 2026-11-14)

**Context:** users want to know context usage; percentages require knowing
the true window limit, which no system reliably knows for third-party
models.

**Decision:** the Stop hook reads input + cache_read + cache_creation from
the session transcript and the daemon reports absolute token counts only.

**Consequences:** interpretation is on the user; no wrong percentages.

## ADR-0011 — `/restart`: the daemon respawns itself

**Status:** accepted (decided 2026-11-14, commit 5ec3e90, recorded 2026-11-14)

**Context:** the bridge's healthz-ensure can't see a wedged-but-alive daemon
as stale (ADR-0008), so recovering from a bad state needed an explicit
`kill $(cat server.pid)` — an SSH-only operation.

**Decision:** a `/restart` bot command: the daemon spawns a fresh
`uv run channelsrv` (the same command the bridge uses), replies, and exits.
The Telegram gap is a couple of seconds.

**Consequences:** restart is now user-reachable from the phone; updates
dropped during the gap stay dropped (`drop_pending_updates`).

## ADR-0012 — Token hygiene: httpx silenced, rotation recommended

**Status:** accepted (decided ~2026-11-09, recorded 2026-11-14)

**Context:** httpx logs full Telegram URLs — including the bot token — at
INFO; the token leaked once into local logs and a session transcript.

**Decision:** httpx/httpcore are forced to WARNING in `setup_logging`; token
rotation via @BotFather remains recommended practice.

**Consequences:** less HTTP-level visibility when debugging the Telegram
provider; enable temporarily, never commit logs.

## ADR-0013 — Startup pane-scan adoption; MCP transport removed

**Status:** accepted (decided 2026-11-14, recorded 2026-11-14)

**Context:** the registry is in-memory (ADR-0006), so every restart emptied
it; an idle tmux pane fires no hook, so nothing re-registered it — after
`/restart` Telegram messages landed in the selection hint. The only
re-attach path was a hook firing inside the pane. The MCP bridge fallback
(ADR-0003) had no users: sessions without tmux never happen, and in tmux it
deliberately idled.

**Decision:** on every start the daemon scans tmux panes running `claude`
(`list-panes`), reproduces the hook's identity formula
(`claude-{slug}-{pane}` from the pane's cwd), and re-registers the found
sessions — liveness is the pane scan itself, nothing about liveness is
persisted. `state.json` gains per-agent last `transcript_path` (data, not
liveness: the file does not die with the daemon), so re-attached agents
resume streaming without waiting for a hook. Chats pinned to a re-attached
agent get a short «re-attached» note, making dropped-during-downtime
messages visible instead of silent. The MCP server (`.mcp.json`, tools,
non-tmux registration, resident events long-poll) is removed;
`bridge/channel_mcp.py` stays as the hook shim's transport + daemon-lifecycle
library.

**Consequences:** supersedes the fallback half of ADR-0003 and the
connection-liveness half of ADR-0007 (only tmux agents remain); Telegram
messages typed while the daemon is down are still dropped
(`drop_pending_updates`), the note just says so. A claude pane whose plugin
hooks are gone would still be adopted — accepted, since the plugin is
installed user-level and hooks fire in every session anyway.
