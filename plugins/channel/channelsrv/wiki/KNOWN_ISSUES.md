# Known issues

Live bugs, diagnosed but not yet fixed. Each has a pointer in the code
(`KNOWN-ISSUE N` comments) so a fix session starts warm. When one is fixed,
move it out of this page (git history keeps it) and touch the ADR it
concerns, if any.

## KI-2 — /mode never changes anything

**Symptom.** `/mode` shows "has no modes" for hook-registered agents; for
agents that do have modes, tapping a button replies "Mode **X** sent to
<agent>" but nothing happens in the session.

**Root cause.** Two independent breaks:

1. `register()` in `hooks/channel-hook` sends `"modes": []` — the hook never
   reads `CHANNEL_AGENT_MODES`, so every tmux agent registers mode-less.
2. Even with modes, `set_mode` (`features/modes.py`) only pushes a
   `mode_set` event into the agent's queue. Nobody delivers it: tmux agents
   never consume the queue (`Core.on_text` pastes only `user_message` events
   into a pane — `mode_set` is not pasted, and nothing long-polls `events`).

**Fix direction.** Read `CHANNEL_AGENT_MODES` in the hook's `register()`,
and deliver `mode_set` to tmux panes like a user message (paste a line the
agent session understands as a mode switch).


## Progress-chain edit accepted but invisible (2026-11-15, open)

`progress: 2897 chars` logged at 16:10:51 with no warning — the API call
succeeded — yet the message the user sees never contained the edited text
(summary turn of the outbox design session). Not reproduced since. Finals
no longer depend on chain edits (ADR-0015), so the class is defused; if
it reproduces in the chain itself, suspect `tail.messages` pointing at a
stale/rolled message id (`progress.py`, `drain_chunks` rollover path).
