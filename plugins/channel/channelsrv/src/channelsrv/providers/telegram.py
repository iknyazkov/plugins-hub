"""Telegram provider: PTB over the thin Provider interface.

All outbound traffic goes through entities (no parse_mode); inbound
commands and button presses are parsed generically and passed to the core;
the provider knows nothing about features.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from channelsrv import formatting
from channelsrv.providers.base import CardButton
from channelsrv.server import Core

log = logging.getLogger(__name__)


def parse_command(text: str) -> tuple[str, str] | None:
    """/status@mybot arg -> ("status", "arg"); not a command -> None."""
    if not text.startswith("/"):
        return None
    word, _, rest = text[1:].partition(" ")
    command = word.split("@", 1)[0].lower() or None
    if command is None:
        return None
    return command, rest.strip()


class TelegramProvider:
    def __init__(self, *, token: str, proxy_url: str | None) -> None:
        builder = ApplicationBuilder().token(token)
        if proxy_url:
            builder = builder.proxy(proxy_url).get_updates_proxy(proxy_url)
        self._app = builder.build()
        self._core: Core | None = None

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
        if commands:
            try:
                await self._app.bot.set_my_commands(
                    [BotCommand(name, description) for name, description in commands]
                )
            except Exception:  # the menu is cosmetic, never fatal
                log.warning("set_my_commands failed", exc_info=True)
        log.info("telegram provider started")

    async def stop(self) -> None:
        if self._app.updater is not None:
            await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

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
