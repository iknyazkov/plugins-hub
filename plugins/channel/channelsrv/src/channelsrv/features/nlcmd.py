"""The NL-command platform: a request in plain words becomes a daemon command.

Named NL commands live in COMMANDS — none shipped yet; future ones are one
entry here. Running one spawns a throwaway tmux agent session (the user's
agent command, via CHANNEL_AI_AGENT), pastes the command's conversion
prompt plus the request, waits for its reply — the throwaway agent is
marked ``service``, so its answer is captured as an event instead of
broadcasting to chats — then executes the resulting daemon command line
through the normal dispatcher and kills the session. One conversion at a
time. Feature-owned NL flows (like ``/cron add``) skip the COMMANDS table
and call ``Feature.convert`` directly with their own prompt.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from channelsrv import delivery, protocol
from channelsrv.registry import Agent
from channelsrv.server import Core

log = logging.getLogger(__name__)

REGISTER_WAIT = 90.0  # tmux spawn + claude startup + SessionStart hook
DEFAULT_TIMEOUT = 60.0  # conversion wait; CHANNEL_AI_TIMEOUT overrides

#: named NL commands; a new one is one entry here — none shipped yet
COMMANDS: dict[str, str] = {}


class Feature:
    def __init__(
        self,
        commands: dict[str, str] | None = None,
        new_session: Callable[[str, str, str], Awaitable[None]] | None = None,
        close_session: Callable[[str], Awaitable[None]] | None = None,
        session_exists: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._commands = commands if commands is not None else COMMANDS
        self._new = new_session or delivery.new_session
        self._close = close_session or delivery.close_session
        self._exists = session_exists or delivery.session_exists
        self._busy = False
        self._seq = itertools.count(1)
        self._core: Core

    def register(self, core: Core) -> None:
        self._core = core
        for name, prompt in self._commands.items():
            self._add(name, prompt)

    def _add(self, name: str, prompt: str) -> None:
        core = self._core

        async def handler(chat_id: int, rest: str) -> None:
            await self.convert(chat_id, prompt, rest)

        core.add_command(name, handler)

    # ------------------------------------------------------------------ flow

    async def convert(self, chat_id: int, prompt: str, text: str) -> None:
        """One NL request -> one daemon command line, executed via the dispatcher."""
        core = self._core
        assert core is not None
        if self._busy:
            await core.provider.send_text(chat_id, "Busy — one conversion at a time.")
            return
        if not text.strip():
            await core.provider.send_text(
                chat_id, "Usage: pass a request in plain words"
            )
            return
        self._busy = True
        session: str | None = None
        try:
            cwd = self._resolve_cwd(chat_id)
            session = f"nlconv-{next(self._seq)}"
            try:
                await self._new(session, agent_command(), cwd)
            except Exception as exc:  # noqa: BLE001 — answerable, not fatal
                await core.provider.send_text(chat_id, f"spawn failed: {exc}")
                return
            agent = await self._wait_registered(session)
            if agent is None or not agent.tmux_target:
                reason = "converter session did not register in time"
                if agent is None and not await self._exists(session):
                    reason = (
                        "converter session died immediately — the agent command is "
                        "failing (check CHANNEL_AI_AGENT)"
                    )
                await core.provider.send_text(chat_id, f"{reason} — closing it")
                return
            agent.service = True
            cursor = 0
            await core.deliver(agent.tmux_target, f"{prompt}\n\nRequest: {text}")
            reply = await self._wait_reply(agent, cursor)
            if reply is None:
                await core.provider.send_text(chat_id, "converter did not answer in time")
                return
            reply = reply.strip()
            if not reply.startswith("/"):
                # the converter refused or asked for details — pass it through
                await core.provider.send_text(chat_id, reply[:1000])
                return
            word, _, rest = reply[1:].partition(" ")
            await core.on_command(chat_id, word.lower(), rest.strip())
        finally:
            self._busy = False
            if session is not None:
                try:
                    await self._close(session)
                except Exception:  # a failed kill is logged; the pane dies eventually
                    log.debug("converter session close failed", exc_info=True)

    def _resolve_cwd(self, chat_id: int) -> str:
        """The active agent's project dir when there is one, HOME otherwise."""
        agent = self._core.active_agent(chat_id)
        if agent is not None and agent.session_name and not agent.service:
            return agent.session_name
        return str(Path.home())

    async def _wait_registered(self, session: str) -> Agent | None:
        """Waits for the spawned session's hook to register (name starts with the session)."""
        deadline = time.monotonic() + REGISTER_WAIT
        while time.monotonic() < deadline:
            for agent in self._core.registry.snapshot():
                if agent.name.startswith(session + "-") and agent.tmux_target:
                    return agent
            if not await self._exists(session):
                return None  # the pane died instantly — no point waiting out the clock
            await asyncio.sleep(0.5)
        return None

    async def _wait_reply(self, agent: Agent, cursor: int) -> str | None:
        timeout = float(os.environ.get("CHANNEL_AI_TIMEOUT", str(DEFAULT_TIMEOUT)))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for _cursor, event in agent.events_after(cursor):
                cursor = max(cursor, int(event.get("cursor", cursor)))
                if event.get("type") == protocol.EVENT_MESSAGE:
                    return str(event.get("text", ""))
            agent.wakeup.clear()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(agent.wakeup.wait(), timeout=remaining)
            except TimeoutError:
                return None
        return None


def agent_command() -> str:
    return os.environ.get("CHANNEL_AI_AGENT", "claude")
