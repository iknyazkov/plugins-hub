"""Transcript tail -> Telegram progress chain.

The daemon tails an agent's ``transcript_path`` while its turn is live: the
transcript is append-only, one JSONL entry per completed content block
(``thinking`` / ``text`` / ``tool_use``). Text blocks and tool calls become
chunks; ``thinking`` and tool *results* never leave this module. A chain
grows inside one Telegram message (edited in place) and rolls over to a fresh
message before the 4096-character API limit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

#: daemon tick between transcript reads
PROGRESS_INTERVAL = 2.0
#: minimum seconds between Telegram writes for one agent (edit rate limits)
PROGRESS_THROTTLE = 2.5
#: Telegram messages cap at 4096 chars — roll over before it
SEGMENT_LIMIT = 3900
#: visible length of a ``⚙ Tool(...)`` line
TOOL_LINE_LIMIT = 80

#: keys of a tool input worth showing, most human-readable first
_TOOL_INPUT_KEYS = ("description", "command", "file_path", "pattern", "url", "prompt")


def tool_line(name: str, tool_input: object) -> str:
    """``⚙ Name(detail)`` — the detail is one collapsed line, truncated."""
    detail = ""
    if isinstance(tool_input, dict):
        for key in _TOOL_INPUT_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                detail = value
                break
        else:
            for value in tool_input.values():
                if isinstance(value, str) and value.strip():
                    detail = value
                    break
    detail = " ".join(detail.split())
    if len(detail) > TOOL_LINE_LIMIT:
        detail = detail[: TOOL_LINE_LIMIT - 1] + "…"
    # italics set the line apart from the assistant's prose around it
    return f"*⚙ {name}({detail})*" if detail else f"*⚙ {name}*"


def extract_chunks(entry: object) -> list[str]:
    """Chunks to show from one transcript entry: text blocks and tool calls."""
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return []
    message = entry.get("message")
    blocks = message.get("content") if isinstance(message, dict) else None
    if not isinstance(blocks, list):
        return []
    chunks: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str) and block["text"].strip():
            chunks.append(block["text"].strip())
        elif kind == "tool_use":
            name = block.get("name")
            chunks.append(
                tool_line(str(name) if name else "tool", block.get("input") or {})
            )
    return chunks


class Chain:
    """The turn's progress text; closes the live segment before it overflows."""

    def __init__(self) -> None:
        self.text = ""  # the live segment's text (not yet rolled over)

    def add(self, chunk: str) -> list[str]:
        """Appends a chunk as its own paragraph; returns segments closed by this chunk."""
        closed: list[str] = []
        for piece in _pieces(chunk):
            if not self.text:
                self.text = piece
            elif len(self.text) + 2 + len(piece) <= SEGMENT_LIMIT:
                self.text += "\n\n" + piece
            else:
                closed.append(self.text)
                self.text = piece
        return closed


def _pieces(chunk: str) -> list[str]:
    """A chunk as pieces that each fit a segment; oversized chunks are split."""
    stripped = chunk.strip()
    if not stripped:
        return []
    if len(stripped) <= SEGMENT_LIMIT:
        return [stripped]
    return [
        stripped[start : start + SEGMENT_LIMIT]
        for start in range(0, len(stripped), SEGMENT_LIMIT)
    ]


@dataclass
class TurnTail:
    """One agent's live-turn tail state; exists only while the turn runs."""

    path: str
    offset: int = 0  # next unread byte
    buf: bytes = b""  # partial line waiting for its newline
    chain: Chain = field(default_factory=Chain)
    #: segments closed before Telegram ever saw them (rollover within one
    #: throttle window) — they must still be sent, or their text is lost
    pending: list[str] = field(default_factory=list)
    messages: dict[int, int] = field(default_factory=dict)  # chat_id -> live message
    sent_text: str = ""  # what the live message currently shows
    last_touch: float = 0.0  # monotonic of the last Telegram write


def drain_chunks(tail: TurnTail) -> None:
    """Reads new transcript chunks into the chain, collecting unsent segments."""
    rolled = False
    for chunk in read_new_chunks(tail):
        closed_segments = tail.chain.add(chunk)
        if closed_segments:
            rolled = True
        for closed in closed_segments:
            if closed != tail.sent_text:  # already shown -> just abandon the message
                tail.pending.append(closed)
    if rolled:  # the live segment died: its message is abandoned, a new one follows
        tail.messages.clear()
        tail.sent_text = ""


def chain_covers(tail: TurnTail, final_text: str) -> bool:
    """True when the chain already shows the final reply verbatim.

    The Stop hook re-sends ``last_assistant_message``, which the chain has
    usually streamed a moment earlier — the caller drains the tail first, so
    a verbatim match means the second copy would be pure duplication.
    """
    text = final_text.strip()
    if not text or tail.pending:  # unsent segments exist — the chain shows less than was made
        return False
    chain = tail.chain.text.rstrip()
    return chain == text or chain.endswith(text)


def read_new_chunks(tail: TurnTail) -> list[str]:
    """Drains the transcript tail; survives a missing or truncated file."""
    try:
        with open(tail.path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size < tail.offset:  # truncated/rewritten — start over
                tail.offset = 0
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
    chunks: list[str] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        chunks.extend(extract_chunks(entry))
    return chunks
