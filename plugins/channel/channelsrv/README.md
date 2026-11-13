# channelsrv

A local daemon channel: Telegram bot ↔ a zoo of agent "bridges" (Claude Code,
Codex, any process with a plugin). One server per machine, one `get_updates`
per bot token; agents come and go, the server stays.

## Run

```sh
make install   # uv sync
make run       # or: uv run channelsrv
```

The first start creates `~/.config/channelsrv/` with an `.env` template — put
the bot token from @BotFather in and restart. Directory override: `CHANNELSRV_HOME`.

## `.env`

| Variable | Required | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | bot token |
| `PROXY_URL` | no | `socks5://…` or `http(s)://…`, applies to both API and polling |

Every other key in `.env` is exported into the daemon environment
(fill-gaps: the real process env wins) — this is how feature knobs reach the
daemon, whose parent session carries no such variables:

| Variable | Meaning |
|---|---|
| `CHANNEL_AI_AGENT` | default agent command for throwaway daemon-spawned sessions (NL converter, `/cron` runs without their own alias); interactive shell, aliases resolve — e.g. `zaiclaude` |
| `CHANNEL_CRON_TIMEOUT` | per-run timeout for `/cron` jobs in seconds (default 600) |
| `ALLOWED_IDS` | no | comma-separated telegram user ids; empty = allow everyone |

The real env beats the file.

## Files

```
~/.config/channelsrv/
├── .env         # credentials (0600)
├── state.json   # active agent choice per chat (atomic writes)
├── server.pid
├── server.sock  # UDS, JSONL protocol for bridges
└── logs/server.log  # rotation 5MB × 3
```

## Bridge protocol

One connection = one frame: a request `{"v":1,"op":"…"}\n` → a response
`{"ok":true,…}` or `{"ok":false,"error":"…"}\n`. Debugging:

```sh
echo '{"v":1,"op":"healthz"}' | socat - UNIX-CONNECT:~/.config/channelsrv/server.sock
```

| op | fields | what it does |
|---|---|---|
| `healthz` | — | alive? (300ms budget; if hung, the bridge kills by `server.pid` and restarts under a flock) |
| `register` | `agent_id, name, session_name, modes[]`, `tmux_target?`, `transcript_path?` | registration (re-registration replaces); `tmux_target` = the agent's tmux pane; `transcript_path` = the session transcript JSONL the daemon tails for the progress chain |
| `deregister` | `agent_id` | leaving |
| `status` | `agent_id, status, detail?, mode?` | status: `idle/thinking/waiting_user/waiting_approval/done/error` (+`mode` — the current mode) |
| `message` | `agent_id, text` | the agent's reply to the chat (tagged with its name) |
| `approval_request` | `agent_id, kind, summary, details?` | an approval card with ✅/❌ buttons → `approval_id` |
| `context` | `agent_id, input, output` | store last-turn context usage (reported by the Stop hook from the session transcript) |
| `events` | `agent_id, cursor` | long-poll 25s: `user_message {chat_id, text}`, `approval_result {approval_id, approved}`, `mode_set {mode}`; for `tmux_target` agents, incoming user messages are also pasted into the pane (`delivery.py`) |

A bridge is a resident process in the agent's session (for Claude Code that is
an MCP server) keeping `events` open; liveness = a live connection, silence
>30s → `offline`.

## Bot commands

- `/agents` — pick the active agent (buttons; persisted, a single agent is auto-selected)
- `/status` — a snapshot of all agents: name · session · mode · status
- `/mode` — modes of the active agent (from its `modes[]`)
- `/command <…>` — paste a command line straight into the active agent's pane
- `/new <agent command> <session> <path>` — spawn a detached tmux session running the command in <path> (interactive shell, aliases resolve; quote a multi-word command — typographic quotes work too); its hooks register it as an agent
- `/close <session>` — kill a tmux session
- `/context` — last-turn context usage of the active agent (absolute tokens; the Stop hook reads them from the session transcript)
- `/cron list | add <plain words> | remove <id|prefix> | run <id|prefix>` — scheduled agent jobs: `/cron add` turns plain words into a job via the NL converter; when a job fires the daemon spawns a throwaway tmux agent session in the job's cwd, pastes the job's prompt and sends the full reply to the creating chat. A launch alias named in the add text («элиас запуска zaiclaude») becomes the job's own agent command; jobs without one use `CHANNEL_AI_AGENT`. Recurring requests get a 5-field cron; one-shot ones («через 5 минут», «сегодня в 18:00») store an absolute `at` time, run once and remove themselves (a late fire still runs; a one-shot blocked by the run cap waits for a free slot). Jobs persist in `cron.json`; recurring runs missed while the daemon was down are skipped, overlap and more than 3 concurrent runs are skipped with a chat note. Run sessions (`cron_<6 hex>-…`) left over from a crashed daemon are killed at startup; `CHANNEL_CRON_TIMEOUT` overrides the 10-minute per-run timeout
- `/restart` — respawn the daemon: it spawns a fresh `uv run channelsrv` (the same command the bridge uses), replies, and exits — the Telegram gap is a couple of seconds
- text — a message to the active agent; approvals arrive as cards with buttons

While an agent is `thinking`, the daemon renews the typing indicator every 5s
for up to 30 minutes, so a long turn keeps showing «печатает…».

**Progress chain.** For agents registered with a `transcript_path`, the turn
is also streamed: the daemon tails the transcript (append-only, one JSONL
entry per completed block; the tail opens at registration — and re-opens on
any op, surviving a mid-turn daemon restart — and closes on a turn-end
status), turns assistant `text` blocks and `tool_use` calls (`⚙ Bash(…)`,
truncated) into a Telegram message it edits in place (2.5s throttle between
edits, a fresh message before Telegram's 4096-char cap). `thinking` blocks
and tool results never reach the chat; the final `Stop` reply arrives as
usual — unless the chain already shows it verbatim, then the duplicate is
skipped — with the chain staying above it as history.

Agents marked
`service` (spawned by daemon features, e.g. NL-command conversion) have their
replies captured as events instead of broadcasting, and are excluded from
agent auto-selection.

## Layout

```
src/channelsrv/
├── main.py          # entry: config, logs, pidfile, lifecycle
├── config.py        # .env, paths, template
├── state.py         # active agent per chat (state.json)
├── protocol.py      # frames, ops, statuses, events
├── registry.py      # agents, cursor events, liveness
├── server.py        # Core (dispatch/routing) + JsonlServer (UDS)
├── formatting.py    # markdown → entities (telegramify-markdown), 4096 split
├── providers/
│   ├── base.py      # provider interface (a second messenger = a new class)
│   └── telegram.py  # PTB: polling, buttons, silent authorization
└── features/        # each feature is a module; removing a module = removing a feature
    ├── status.py, selection.py, approvals.py, modes.py, …, nlcmd.py, cron.py
```

Python ≥3.11, uv, mypy strict. Dependencies: `python-telegram-bot[socks]`,
`telegramify-markdown`. Everything else is stdlib.
