"""Messenger provider interface — everything the core knows about Telegram."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import NamedTuple, Protocol


class CardButton(NamedTuple):
    text: str
    callback_data: str


class MsgRef:
    """A message_id that becomes real once the queued send is delivered.

    The outbox returns one from ``send_card`` immediately — enqueueing never
    blocks — and resolves it after the messenger accepts the message. A
    resolved value of ``None`` means the create failed permanently; edits
    targeting such a ref are dropped.
    """

    __slots__ = ("_event", "_value")

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._value: int | None = None

    @property
    def resolved(self) -> bool:
        return self._event.is_set()

    @property
    def value(self) -> int | None:
        """The message_id when resolved, else None — a non-blocking peek."""
        return self._value if self._event.is_set() else None

    def resolve(self, message_id: int | None) -> None:
        self._value = message_id
        self._event.set()

    async def message_id(self) -> int | None:
        await self._event.wait()
        return self._value


#: senders take ``message_id`` as either a real id or an unresolved ref
MessageId = int | MsgRef


class Provider(Protocol):
    """A thin door to the outside: text, card with buttons, edit, typing.

    ``instant`` marks a reply to a user action (command, button) — the
    outbox jumps it ahead of agent chatter. Direct implementations may
    ignore it: only the outbox owns the lanes.
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
    ) -> int | MsgRef | None:
        """Sends a card; returns the message_id (or a ref) for a later edit."""
        ...

    async def edit_text(
        self, chat_id: int, message_id: MessageId, markdown: str, instant: bool = False
    ) -> None: ...

    async def chat_action(self, chat_id: int) -> None: ...
