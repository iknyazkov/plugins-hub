"""Fake provider for core and feature tests: records everything, sends nothing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from channelsrv.providers.base import CardButton


@dataclass
class FakeProvider:
    texts: list[tuple[int, str, str | None]] = field(default_factory=list)
    cards: list[tuple[int, str, tuple[tuple[CardButton, ...], ...]]] = field(
        default_factory=list
    )
    actions: list[int] = field(default_factory=list)

    async def send_text(
        self,
        chat_id: int,
        markdown: str,
        agent_name: str | None = None,
        instant: bool = False,
    ) -> None:
        self.texts.append((chat_id, markdown, agent_name))

    async def send_card(
        self,
        chat_id: int,
        markdown: str,
        buttons: Sequence[Sequence[CardButton]],
        instant: bool = False,
    ) -> None:
        self.cards.append((chat_id, markdown, tuple(tuple(row) for row in buttons)))

    async def chat_action(self, chat_id: int) -> None:
        self.actions.append(chat_id)
