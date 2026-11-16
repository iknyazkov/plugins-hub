"""Transcript tail -> mid-turn prose messages.

The PostToolUse hook is the event source for tool calls (ADR-0016); the
tail exists only for assistant TEXT blocks — narration worth reading on
its own. Every block goes out as its own message: no chain, no in-place
edits, no rollovers, no segment math. A rewritten (compacted) transcript
is skipped to its new end — rewritten history is old by definition, and
re-reading it once re-streamed delivered content as ghosts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

#: daemon tick between transcript reads
PROGRESS_INTERVAL = 2.0


def tool_line(name: str, detail: str) -> str:
    """``⚙ Name(detail)`` — the detail arrives ready from the hook."""
    # italics set the line apart from the assistant's prose around it
    return f"*⚙ {name}({detail})*" if detail else f"*⚙ {name}*"


@dataclass
class TurnTail:
    """One agent's live-turn tail; exists only while the turn runs."""

    path: str
    offset: int = 0  # next unread byte
    buf: bytes = b""  # partial line waiting for its newline


def extract_texts(entry: object) -> list[str]:
    """Assistant text blocks of one transcript entry; everything else —
    thinking, tool calls, tool results, user rows — stays out."""
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return []
    message = entry.get("message")
    blocks = message.get("content") if isinstance(message, dict) else None
    if not isinstance(blocks, list):
        return []
    texts: list[str] = []
    for block in blocks:
        if (
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
            and block["text"].strip()
        ):
            texts.append(block["text"].strip())
    return texts


def drain_texts(tail: TurnTail) -> list[str]:
    """New transcript text blocks since the last call."""
    texts: list[str] = []
    for line in read_new_lines(tail):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        texts.extend(extract_texts(entry))
    return texts


def read_new_lines(tail: TurnTail) -> list[bytes]:
    """Drains the transcript tail; survives a missing or rewritten file."""
    try:
        with open(tail.path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size < tail.offset:  # rewritten/compacted by the harness —
                # the past was replaced, and it is OLD: re-reading it would
                # re-stream already-delivered content as prefixless ghosts
                # (2026-11-16 incident). Skip to the new end; only what is
                # appended from here on is progress.
                tail.offset = size
                tail.buf = b""
            f.seek(tail.offset)
            data = f.read()
    except OSError:
        return []
    if not data:
        return []
    tail.offset += len(data)
    tail.buf += data
    lines = tail.buf.split(b"\n")
    tail.buf = lines.pop()  # the last piece may be a partial line
    return lines
