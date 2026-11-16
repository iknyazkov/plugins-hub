"""Messenger provider interface — everything the core knows about Telegram."""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple, Protocol


class CardButton(NamedTuple):
    text: str
    callback_data: str


class Provider(Protocol):
    """A thin door to the outside: text, card with buttons, typing.

    There is deliberately no edit: every user-bound delivery is its own
    message (ADR-0016) — in-place edits once proved invisible and dragged
    a chain of state (message ids, rollovers, echo suppression) behind
    them. ``instant`` marks a reply to a user action (command, button) —
    the outbox jumps it ahead of agent chatter; direct implementations
    may ignore it: only the outbox owns the lanes.
    """

    async def send_text(
        self,
        chat_id: int,
        markdown: str,
        agent_name: str | None = None,
        instant: bool = False,
    ) -> None: ...

    async def send_card(
        self,
        chat_id: int,
        markdown: str,
        buttons: Sequence[Sequence[CardButton]],
        instant: bool = False,
    ) -> None: ...

    async def chat_action(self, chat_id: int) -> None: ...
