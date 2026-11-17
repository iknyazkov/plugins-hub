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


## Ghost re-stream of old transcript content (2026-11-16, FIXED in 0.1.34)

The harness rewrites/compacts the session file; the tail's `size < offset`
path reset the offset to zero and re-streamed the whole history into the
chain — the user saw a prefixless duplicate of an old reply ("ghost").
Fixed: on shrink, skip to the new end (`read_new_chunks`). Verified by
`test_shrunk_transcript_is_skipped_not_reread` (failed on pre-fix code).
Related same-night fix: `SEGMENT_LIMIT` 3900 → 3000 — rendered entity
text runs longer than source markdown, and 3900-char segments drew
`BadRequest Message_is_too_long` (three drops on 2026-11-15/16, now
visible in logs thanks to the outbox).

## Progress-chain edit accepted but invisible (2026-11-15, CLOSED by ADR-0016)

Unexplained then, impossible now: 0.2.0 removed edits entirely — every
delivery is its own message.

`progress: 2897 chars` logged at 16:10:51 with no warning — the API call
succeeded — yet the message the user sees never contained the edited text
(summary turn of the outbox design session). Not reproduced since. Finals
no longer depend on chain edits (ADR-0015), so the class is defused; if
it reproduces in the chain itself, suspect `tail.messages` pointing at a
stale/rolled message id (`progress.py`, `drain_chunks` rollover path).

## Zombie daemon from a wedged shutdown (2026-11-17, FIXED in 0.2.1)

A daemon spawned Monday midnight ignored SIGTERM (its graceful shutdown
was stuck) and outlived every later /restart — /restart only kills the
daemon that received the command. Two daemons on one token: 409 polling
conflicts every ~35 s, and the zombie's stale tails re-streamed old
transcript content as ghost duplicates of other agents' replies.
Fix: `kill_stale_daemons()` at startup — the youngest daemon TERMs
every other channelsrv process (uv wrappers included, own parent
excluded) and KILLs the survivors after 2 s. Diagnosis shortcut:
`ps aux | grep channelsrv` and `Conflict` lines in server.log.
