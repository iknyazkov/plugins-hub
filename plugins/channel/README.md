# channel

Telegram remote control for agent sessions. The bundled
[channelsrv](channelsrv/) daemon owns the bot; a session joins it in one of
two transports:

- **Hooks + tmux (primary)** — the session lives in a tmux pane; plugin hooks
  register it with the daemon, incoming messages are pasted into the pane,
  finished replies go back to Telegram automatically. No Claude Code flags.
- **MCP bridge** — a resident stdio MCP server (for sessions without tmux);
  same daemon, explicit tool calls instead of hooks.

One daemon per machine serves many agent sessions; each session registers as
a separate agent.

## Setup

```
/plugin install channel --marketplace iknyazkov/plugins-hub
```

1. Install [uv](https://docs.astral.sh/uv/) — the daemon is spawned with it.
   The hooks/bridge run on plain `python3` (stdlib only).
2. Start any session with the plugin once — it creates
   `~/.config/channelsrv/` with an `.env` template.
3. Put the bot token from @BotFather into `~/.config/channelsrv/.env`,
   optionally `PROXY_URL` and `ALLOWED_IDS` (see
   [channelsrv/README.md](channelsrv/README.md)).
4. Message the bot from Telegram — the first agent picks up automatically,
   or pick explicitly with `/agents`.

## How the tmux transport works

The daemon keeps the daemon alive (`healthz` probe → kill by `server.pid` →
spawn `uv run channelsrv` under a flock → poll healthz). Then:

| Hook | Effect |
| --- | --- |
| `SessionStart` | register with the daemon, `tmux_target = $TMUX_PANE` |
| `UserPromptSubmit` | status `thinking` (typing indicator in Telegram) |
| `Stop` | `last_assistant_message` → Telegram, status `idle`, context usage → daemon (from `transcript_path`) |
| `PermissionRequest` | approval card, then wait `CHANNEL_HOOK_VERDICT_WAIT` seconds (default 20) for a Telegram verdict — a ✅/❌ answer resolves the real permission dialog remotely; without one the dialog shows locally as usual. `AskUserQuestion` is denied instantly — its answer dialog is terminal-local; ask in plain text |
| `SessionEnd` | deregister |

A Telegram message to the active agent is bracketed-pasted into its pane
(`load-buffer` → `paste-buffer -p -r -d` → `Enter`), exactly like typing it.

Outside tmux the hooks do nothing, and the MCP bridge takes over: it
registers the session and exposes `channel_set_status`, `channel_send`,
`channel_request_approval` and `channel_wait_events` (drain buffered events).
In a tmux session the bridge deliberately stays idle — hooks own the channel.

## Environment

| Variable | Meaning |
| --- | --- |
| `CHANNELSRV_HOME` | daemon state dir override (default `~/.config/channelsrv`) |
| `CHANNEL_TMUX_SOCKET` | tmux server name for delivery (`tmux -L`), default socket |
| `CHANNEL_HOOK_VERDICT_WAIT` | seconds the `PermissionRequest` hook waits for Telegram (default 20) |
| `CHANNEL_AGENT_ID` | bridge registry identity override (parallel sessions in one project) |
| `CHANNEL_AGENT_NAME` | display name in Telegram (hooks default: `<tmux session>-<project dir>-<pane number>`; bridge default: project directory name) |
| `CHANNEL_SESSION_NAME` | session label in `/status` (default: project path) |
| `CHANNEL_AGENT_MODES` | comma-separated modes offered via the bot's `/mode` |
| `CHANNEL_CRON_TIMEOUT` | per-run timeout for `/cron` jobs in seconds (default 600) |
| `CHANNEL_AI_AGENT` | default agent command for daemon-spawned throwaway sessions — set it in `~/.config/channelsrv/.env`; a `/cron` job may carry its own launch alias, which overrides it |

## Bot commands

`/agents` — pick the active agent · `/status` — snapshot of all agents ·
`/mode` — modes of the active agent · `/command <…>` — run a command inside
the active agent's session · `/new <agent command> <session> <path>` — spawn
a new tmux session in <path> (aliases resolve; quote a multi-word command) ·
`/close <session>` — kill
one · `/cron list|add|remove|run` — scheduled agent jobs (plain-words
scheduling: recurring cron or one-shot «через 5 минут», optional per-job
launch alias; the daemon runs each job in a throwaway agent session and
sends the reply here) · plain text — a message to the agent.

## Notes

- `channelsrv/` is a vendored snapshot of the channelsrv project, translated
  to English for this hub; gates: `make lint && make test` inside `channelsrv/`.
- macOS/Linux only (UDS + tmux). Claude Code must run inside tmux for the
  primary transport (`tmux new-session -d -s work -c ~/proj claude`).
- Permission verdicts that arrive after the `PermissionRequest` hook fell
  through are ignored — the local dialog owns the request by then. The
  `permission_prompt` notification (the only signal for sandboxed-command
  network prompts, which skip `PermissionRequest` hooks) is intentionally
  not wired — add it back to `hooks/hooks.json` if you hit those.
- **One bot token = one daemon.** Telegram allows a single `getUpdates`
  consumer per token: a second machine running another daemon against the
  same token gets 409 Conflicts — its polling dies silently while outbound
  keeps working, so its agents talk but never hear. Per-machine bot tokens
  (30 seconds in @BotFather) are the clean multi-machine setup.
