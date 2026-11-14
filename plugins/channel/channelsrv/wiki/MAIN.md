# channelsrv wiki

A Telegram bot ↔ a zoo of agent "bridges": one local daemon, agents come
and go, the server stays. This wiki is the distilled knowledge about how
and why it works that way; the code is the source of truth.

## The one-paragraph architecture

Every agent session (Claude Code, Codex, any process) runs a small bridge
that registers with the daemon over a UDS JSONL socket and long-polls for
events. Telegram messages arrive at the daemon, which routes them to the
chat's selected agent — pasted into its tmux pane when there is one
([ADR-0003](ADR.md)). Statuses flow back the same way; replies go out
through the provider layer. The daemon owns nothing about the agents
beyond registration — no persistence, no process management of live
sessions ([ADR-0006](ADR.md)) — so restarts are cheap and bridges are
fungible.

## Navigation

| Page | What's in it |
|---|---|
| [SCHEMA.md](SCHEMA.md) | how this wiki works |
| [ADR.md](ADR.md) | every architecture decision, oldest first |
| [KNOWN_ISSUES.md](KNOWN_ISSUES.md) | live bugs: diagnosed, not yet fixed |

Topical pages (created as needed): [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

## Hard-won lessons (pointers)

Diagnosing a hang: daemon log first
(`~/.config/channelsrv/logs/server.log`) — repeating
listening/registered pairs = spawn storm; silence with sparse Updater
errors = dead Telegram feed; then `pmset -g log` for Mac sleep.
Routing landing in a dead buffer: `ps aux | grep channel_mcp` (zombie
bridges) before anything else.
