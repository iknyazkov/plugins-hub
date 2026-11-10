"""Outbound formatting: agent markdown -> ``(text, entities)`` for Telegram.

No ``parse_mode`` and no manual escaping: telegramify-markdown produces
ready-made entities. The converter is the single place where foreign
markdown becomes Telegram text, so its failure degrades to plain text
instead of killing the send.
"""

from __future__ import annotations

import telegram
import telegramify_markdown

TG_LIMIT = 4096
#: Cut the source with margin: the converter adds emoji headers and prefixes.
CHUNK_SOURCE_LIMIT = 3500


def split_markdown(text: str, limit: int = CHUNK_SOURCE_LIMIT) -> list[str]:
    """Splits markdown at paragraph boundaries; a giant paragraph is cut hard."""
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    buf = ""
    for paragraph in text.split("\n\n"):
        candidate = paragraph if not buf else buf + "\n\n" + paragraph
        if len(candidate) <= limit:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        if len(paragraph) <= limit:
            buf = paragraph
            continue
        for i in range(0, len(paragraph), limit):
            chunks.append(paragraph[i : i + limit])
    if buf:
        chunks.append(buf)
    return chunks


def render(markdown: str) -> tuple[str, list[telegram.MessageEntity]]:
    """markdown -> ``(plain_text, ptb_entities)``; converter failure -> plain."""
    try:
        text, raw = telegramify_markdown.convert(markdown)
    except Exception:  # noqa: BLE001 — degradation matters more than diagnosis, the text must go out
        return markdown, []
    entities = [
        telegram.MessageEntity(e.type, e.offset, e.length, url=e.url, language=e.language)
        for e in raw
    ]
    return text, entities


def with_prefix(agent_name: str, chunks: list[str]) -> list[str]:
    """Tags the first chunk with the agent name (bold), leaves the rest alone."""
    if not chunks:
        return []
    name = agent_name[:64]
    chunks[0] = f"**[{name}]**\n\n{chunks[0]}"
    return chunks
