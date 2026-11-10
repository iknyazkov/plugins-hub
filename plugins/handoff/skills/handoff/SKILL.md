---
name: handoff
description: Compact the current conversation into a handoff document for another agent to pick up.
argument-hint: "What will the next session be used for?"
disable-model-invocation: true
---

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Store it with the bundled script, never by hand: compose the full markdown, then pipe it to `python3 <skill-dir>/../../scripts/handoff.py save`, where `<skill-dir>` is this skill's base directory from the invocation context. The script keeps up to 3 timestamped handoff files per project in the OS temp dir, writes atomically, and prints the saved path — every save becomes a new entry, never an overwrite, so recent handoffs survive across sessions. Never choose or modify the destination path yourself.

When a save pushes the project past 3 entries, the script deletes the oldest one without confirmation and prints an eviction warning on stderr naming its creation time and first line. The user cannot see stderr — relay that warning to them together with the printed path, since a document they may still need was just deleted.

Include a "suggested skills" section in the document, naming which skills the next agent should call the Skill tool for.

Do not duplicate content already captured in other artifacts (specs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

Redact any sensitive information, such as API keys, passwords, or personally identifiable information.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.
