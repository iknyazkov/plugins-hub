---
name: pick-up
description: Resume work from the handoff document left for this project by a previous session.
disable-model-invocation: true
---

Pick up where a previous session left off. Consume the newest handoff stored for this project:

```sh
python3 <skill-dir>/../../scripts/handoff.py consume
```

`<skill-dir>` is this skill's base directory from the invocation context. The script prints the stored document to stdout and deletes only the consumed file, so a stale handoff can never be picked up twice; stderr reports how many handoffs remain. Running pick-up again walks backwards to the next-newest handoff. If it exits non-zero, there is no handoff for this project — tell the user and stop.

A project stores up to 3 handoffs, newest first. Run the script with `show` to list them (number, creation time, first line); `show <n>` and `consume <n>` address a specific entry. Prefer the newest — reach for an older one only when the user asks for it or the newest turns out not to describe the work at hand.

Read the document and continue the work it describes. Open the artifacts it references by path or URL as needed, and call the Skill tool for the skills named in its "suggested skills" section when they become relevant.
