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

## KI-3 — sometimes the output arrives twice

**Symptom.** Occasionally the user sees the same text twice: once in the
progress chain and again as the final reply (or twice across chain
messages). Long turns trigger it reliably; short ones never do.

**Root cause.** The duplicate-skip in `_op_message` (`server.py`) only
recognizes a final reply that lives entirely in the live chain segment.
Any turn long enough to roll the chain defeats it:

- A turn whose chain text passes `SEGMENT_LIMIT` (3900 chars, `progress.py`)
  rolls the earlier text into `tail.pending`, and a final reply over 3900
  chars is split into pieces with only the last piece in `chain.text`.
- `chain_covers` (`progress.py`) then returns False (pending segments exist,
  or `chain.text` cannot end-with the full multi-piece reply), so
  `_op_message` sends the complete reply via `deliver_from_agent`.
- The Stop hook's follow-up `status idle` runs `close_turn` (`server.py`),
  which sends the pending segments and the live chain text — the same
  content again.

Secondary race, same symptom: if the turn-end status ever arrives *before*
the message op (hook re-run, duplicated Stop), `close_turn` has already
dropped the tail, the duplicate check is skipped entirely, and the full
text goes out again.

**Fix direction.** Make the close path aware of what the final message
already covered (compare against pending+pieces, not just the live segment),
or let `chain_covers` reconstruct the full streamed text before deciding.
