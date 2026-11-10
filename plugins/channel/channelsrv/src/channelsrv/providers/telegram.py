"""Telegram provider: PTB over the thin Provider interface.

All outbound traffic goes through entities (no parse_mode); inbound
commands and button presses are parsed generically and passed to the core;
the provider knows nothing about features.

A watchdog guards the polling loop: a healthy long poll completes every
~10s even in a silent chat, so a long silence means the connection went
black-holed (a hung request with no error) or the polling task died —
the watchdog restarts polling in place and shuts the daemon down cleanly
if that keeps failing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from channelsrv import formatting
from channelsrv.providers.base import CardButton
from channelsrv.server import Core

log = logging.getLogger(__name__)

#: no completed getUpdates for this long -> the poll loop is stuck
POLL_STALL_THRESHOLD = 300.0
WATCHDOG_INTERVAL = 60.0
#: restarts that did not bring polls back before the daemon shuts down cleanly
POLLING_RESTART_LIMIT = 3
#: PTB long-polls for 10s server-side; the read budget must exceed that
POLLING_TIMEOUT = 15.0


def parse_command(text: str) -> tuple[str, str] | None:
    """/status@mybot arg -> ("status", "arg"); not a command -> None."""
    if not text.startswith("/"):
        return None
    word, _, rest = text[1:].partition(" ")
    command = word.split("@", 1)[0].lower() or None
    if command is None:
        return None
    return command, rest.strip()


class PollingRequest(HTTPXRequest):
    """The getUpdates transport; reports completed polls to the watchdog.

    Built by hand instead of via the builder's ``get_updates_*`` arguments:
    PTB forbids combining a request instance with those, so the proxy and
    timeouts travel on the instance itself.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.on_poll: Callable[[], None] | None = None

    async def do_request(self, *args: Any, **kwargs: Any) -> tuple[int, bytes]:
        result = await super().do_request(*args, **kwargs)
        if self.on_poll is not None:
            self.on_poll()
        return result


class PollWatchdog:
    """Restarts polling when getUpdates stops completing."""

    def __init__(
        self,
        *,
        restart: Callable[[], Awaitable[None]],
        on_fatal: Callable[[], None],
        threshold: float = POLL_STALL_THRESHOLD,
        interval: float = WATCHDOG_INTERVAL,
        restart_limit: int = POLLING_RESTART_LIMIT,
    ) -> None:
        self._restart = restart
        self._on_fatal = on_fatal
        self._threshold = threshold
        self._interval = interval
        self._restart_limit = restart_limit
        self._restarts = 0
        self._last_completed = time.monotonic()

    def poke(self) -> None:
        """A getUpdates roundtrip completed — the loop is alive."""
        self._last_completed = time.monotonic()
        self._restarts = 0

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            silent = time.monotonic() - self._last_completed
            if silent < self._threshold:
                continue
            if self._restarts >= self._restart_limit:
                log.critical(
                    "getUpdates silent for %.0fs after %d restarts — giving up; "
                    "shutting down cleanly (the channel hooks respawn the daemon "
                    "on the next session activity)",
                    silent,
                    self._restarts,
                )
                self._on_fatal()
                return
            self._restarts += 1
            log.error(
                "getUpdates silent for %.0fs — restarting polling "
                "(attempt %d/%d)",
                silent,
                self._restarts,
                self._restart_limit,
            )
            try:
                await self._restart()
            except Exception:
                log.exception("polling restart failed")
            self._last_completed = time.monotonic()


class TelegramProvider:
    def __init__(
        self,
        *,
        token: str,
        proxy_url: str | None,
        on_fatal: Callable[[], None] | None = None,
    ) -> None:
        self._poll_request = PollingRequest(
            proxy=proxy_url,
            connect_timeout=POLLING_TIMEOUT,
            read_timeout=POLLING_TIMEOUT,
            write_timeout=POLLING_TIMEOUT,
            pool_timeout=POLLING_TIMEOUT,
        )
        builder = ApplicationBuilder().token(token).get_updates_request(self._poll_request)
        if proxy_url:
            builder = builder.proxy(proxy_url)
        self._app = builder.build()
        self._core: Core | None = None
        self._on_fatal = on_fatal
        self._watchdog = PollWatchdog(
            restart=self._restart_polling,
            on_fatal=on_fatal or (lambda: None),
        )
        self._poll_request.on_poll = self._watchdog.poke
        self._watchdog_task: asyncio.Task[None] | None = None

    def bind(self, core: Core) -> None:
        """Attach the core: wiring without constructor cycles."""
        self._core = core
        self._app.add_handler(MessageHandler(filters.COMMAND, self._on_command))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        self._app.add_handler(CallbackQueryHandler(self._on_callback))

    # -------------------------------------------------------------- lifecycle

    async def start(self, commands: Sequence[tuple[str, str]] | None = None) -> None:
        await self._app.initialize()
        await self._app.start()
        if self._app.updater is not None:
            await self._app.updater.start_polling(drop_pending_updates=True)
            self._watchdog.poke()  # fresh baseline for the watchdog
            self._watchdog_task = asyncio.create_task(self._watchdog.run())
        if commands:
            try:
                await self._app.bot.set_my_commands(
                    [BotCommand(name, description) for name, description in commands]
                )
            except Exception:  # the menu is cosmetic, never fatal
                log.warning("set_my_commands failed", exc_info=True)
        log.info("telegram provider started")

    async def stop(self) -> None:
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if self._app.updater is not None:
            await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    async def _restart_polling(self) -> None:
        updater = self._app.updater
        if updater is None:
            return
        if updater.running:
            # bounded: the hung poll errors out within the request timeouts
            await updater.stop()
        await updater.start_polling(drop_pending_updates=True)

    # ---------------------------------------------------------------- inbound

    def _gate(self, update: Update) -> tuple[Core, int] | None:
        """Core bound, user authorized, chat recognizable — otherwise stay silent."""
        core = self._core
        user = update.effective_user
        if core is None or user is None or not core.is_allowed(user.id):
            return None
        chat = update.effective_chat
        return (core, chat.id) if chat is not None else None

    async def _on_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        gate = self._gate(update)
        message = update.effective_message
        if gate is None or message is None:
            return
        core, chat_id = gate
        parsed = parse_command(message.text or "")
        if parsed is not None:
            command, rest = parsed
            await core.on_command(chat_id, command, rest)

    async def _on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        gate = self._gate(update)
        message = update.effective_message
        if gate is None or message is None:
            return
        core, chat_id = gate
        await core.on_text(chat_id, message.text or "")

    async def _on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        gate = self._gate(update)
        query = update.callback_query
        if gate is None or query is None:
            return
        core, chat_id = gate
        await query.answer()  # clear the spinner on the button
        await core.on_callback(chat_id, query.data or "")

    # --------------------------------------------------------------- outbound

    async def send_text(
        self, chat_id: int, markdown: str, agent_name: str | None = None
    ) -> None:
        chunks = formatting.split_markdown(markdown)
        if agent_name is not None:
            chunks = formatting.with_prefix(agent_name, chunks)
        for chunk in chunks:
            text, entities = formatting.render(chunk)
            await self._app.bot.send_message(chat_id, text, entities=entities)

    async def send_card(
        self, chat_id: int, markdown: str, buttons: Sequence[Sequence[CardButton]]
    ) -> int | None:
        text, entities = formatting.render(markdown)
        message = await self._app.bot.send_message(
            chat_id,
            text,
            entities=entities,
            reply_markup=_keyboard(buttons) if buttons else None,
        )
        message_id: int = message.message_id
        return message_id

    async def edit_text(self, chat_id: int, message_id: int, markdown: str) -> None:
        text, entities = formatting.render(markdown)
        await self._app.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, entities=entities
        )

    async def chat_action(self, chat_id: int) -> None:
        await self._app.bot.send_chat_action(chat_id, ChatAction.TYPING)


def _keyboard(buttons: Sequence[Sequence[CardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(b.text, callback_data=b.callback_data) for b in row]
            for row in buttons
        ]
    )
