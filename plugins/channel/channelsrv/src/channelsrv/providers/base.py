"""Messenger provider interface — everything the core knows about Telegram."""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple, Protocol


class CardButton(NamedTuple):
    text: str
    callback_data: str


class Provider(Protocol):
    """A thin door to the outside: text, card with buttons, edit, typing."""

    async def send_text(
        self, chat_id: int, markdown: str, agent_name: str | None = None
    ) -> None: ...

    async def send_card(
        self, chat_id: int, markdown: str, buttons: Sequence[Sequence[CardButton]]
    ) -> int | None:
        """Sends a card; returns the message_id for a later edit."""
        ...

    async def edit_text(self, chat_id: int, message_id: int, markdown: str) -> None: ...

    async def chat_action(self, chat_id: int) -> None: ...
