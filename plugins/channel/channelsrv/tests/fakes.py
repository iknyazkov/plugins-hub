"""Fake provider for core and feature tests: records everything, sends nothing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from channelsrv.providers.base import CardButton


@dataclass
class FakeProvider:
    texts: list[tuple[int, str, str | None]] = field(default_factory=list)
    cards: list[tuple[int, str, tuple[tuple[CardButton, ...], ...]]] = field(default_factory=list)
    edits: list[tuple[int, int, str]] = field(default_factory=list)
    actions: list[int] = field(default_factory=list)
    _next_id: int = 1000

    async def send_text(self, chat_id: int, markdown: str, agent_name: str | None = None) -> None:
        self.texts.append((chat_id, markdown, agent_name))

    async def send_card(
        self, chat_id: int, markdown: str, buttons: Sequence[Sequence[CardButton]]
    ) -> int | None:
        self._next_id += 1
        self.cards.append((chat_id, markdown, tuple(tuple(row) for row in buttons)))
        return self._next_id

    async def edit_text(self, chat_id: int, message_id: int, markdown: str) -> None:
        self.edits.append((chat_id, message_id, markdown))

    async def chat_action(self, chat_id: int) -> None:
        self.actions.append(chat_id)
