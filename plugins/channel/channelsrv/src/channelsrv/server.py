"""Core: protocol dispatch, user-to-agent routing, UDS server.

Core knows neither Telegram nor features: the provider arrives as an
interface, features plug in their ops/commands/buttons via
``add_op/add_command/add_callback``. JsonlServer is a thin transport: one
connection = one request-response frame.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from pathlib import Path
from typing import Any

from channelsrv import delivery, progress, protocol
from channelsrv.config import Config
from channelsrv.providers.base import Provider
from channelsrv.registry import Agent, Registry
from channelsrv.state import StateStore

log = logging.getLogger(__name__)

OpHandler = Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]
CommandHandler = Callable[[int, str], Awaitable[None]]
CallbackHandler = Callable[[int, str], Awaitable[None]]

SWEEP_INTERVAL = 5.0
#: how long a single "typing…" indicator lives in Telegram before fading
TYPING_INTERVAL = 5.0
TYPING_MAX = 30 * 60.0
#: how many bytes we read from a frame — plenty for a message text
FRAME_LIMIT = 2**20


class Core:
    """The only state writer; lifetime equals the server's lifetime."""

    def __init__(
        self,
        *,
        config: Config,
        registry: Registry,
        state: StateStore,
        provider: Provider,
        events_timeout: float = 25.0,
        deliver: Callable[[str, str], Coroutine[Any, Any, None]] | None = None,
        pane_alive: Callable[[str], Coroutine[Any, Any, bool]] | None = None,
        list_panes: Callable[[], Coroutine[Any, Any, list[dict[str, str]]]] | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.state = state
        self.provider = provider
        #: how a user message reaches a tmux-based agent (tests inject fakes)
        self.deliver: Callable[[str, str], Coroutine[Any, Any, None]] = (
            deliver or delivery.paste_safely
        )
        #: how the sweeper checks a tmux agent is still there (tests inject fakes)
        self.pane_alive: Callable[[str], Coroutine[Any, Any, bool]] = (
            pane_alive or delivery.pane_alive
        )
        #: how startup adoption scans the panes (tests inject fakes)
        self.list_panes: Callable[[], Coroutine[Any, Any, list[dict[str, str]]]] = (
            list_panes or delivery.list_panes
        )
        self._events_timeout = events_timeout
        self._ops: dict[str, OpHandler] = {
            protocol.OP_HEALTHZ: self._op_healthz,
            protocol.OP_REGISTER: self._op_register,
            protocol.OP_DEREGISTER: self._op_deregister,
            protocol.OP_STATUS: self._op_status,
            protocol.OP_MESSAGE: self._op_message,
            protocol.OP_CONTEXT: self._op_context,
            protocol.OP_EVENTS: self._op_events,
        }
        self._commands: dict[str, CommandHandler] = {}
        self._callbacks: dict[str, CallbackHandler] = {}
        #: the selection feature overrides the hint with its own (with /agents)
        self.select_hint = "Agent unavailable or not selected."

    # ------------------------------------------------------------ plugin side

    async def handle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """One protocol frame -> one answer. Protocol errors are answers, not exceptions."""
        try:
            protocol.check_version(request)
            op = protocol.req_str(request, "op")
            handler = self._ops.get(op)
            if handler is None:
                raise protocol.ProtocolError(f"unknown op {op!r}")
            return await handler(request)
        except protocol.ProtocolError as exc:
            return protocol.fail(str(exc))

    async def _op_healthz(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return protocol.ok(pid=os.getpid())

    async def _op_register(self, request: Mapping[str, Any]) -> dict[str, Any]:
        modes_raw = request.get("modes", [])
        if not isinstance(modes_raw, list) or not all(isinstance(m, str) for m in modes_raw):
            raise protocol.ProtocolError("field 'modes' must be a list of strings")
        agent = self.registry.register(
            agent_id=protocol.req_str(request, "agent_id"),
            name=protocol.req_str(request, "name"),
            session_name=protocol.req_str(request, "session_name"),
            modes=modes_raw,
            tmux_target=protocol.opt_str(request, "tmux_target"),
            transcript_path=protocol.opt_str(request, "transcript_path"),
        )
        log.info(
            "agent registered: %s (%s), modes=%s, tmux=%s, transcript=%s",
            agent.id, agent.name, agent.modes, agent.tmux_target,
            "yes" if agent.transcript_path else "no",
        )
        if agent.transcript_path:  # data for a post-restart re-attach to resume streaming
            self.state.set_transcript(agent.id, agent.transcript_path)
        self._start_tail(agent)  # a mid-turn re-registration must resume streaming
        return protocol.ok()

    async def _op_deregister(self, request: Mapping[str, Any]) -> dict[str, Any]:
        agent_id = protocol.req_str(request, "agent_id")
        self.registry.deregister(agent_id)
        self.state.drop_transcript(agent_id)  # the session ended; the path is dead
        log.info("agent gone: %s", agent_id)
        return protocol.ok()

    async def _op_status(self, request: Mapping[str, Any]) -> dict[str, Any]:
        agent = self.live_agent(request)
        status = protocol.req_str(request, "status")
        if status not in protocol.AGENT_STATUSES:
            raise protocol.ProtocolError(
                f"unknown status {status!r}; allowed: {sorted(protocol.AGENT_STATUSES)}"
            )
        agent.status = status
        agent.detail = protocol.opt_str(request, "detail")
        if status == protocol.STATUS_THINKING:
            agent.thinking_since = time.monotonic()
        else:
            agent.thinking_since = None
        mode = protocol.opt_str(request, "mode")
        if mode is not None:
            agent.mode = mode
        if status == protocol.STATUS_THINKING:
            for chat_id in self._agent_chats(agent.id):
                try:
                    await self.provider.chat_action(chat_id)
                except Exception:  # the indicator is cosmetic, never fatal
                    log.debug("chat action failed", exc_info=True)
        if status == protocol.STATUS_THINKING:
            self._start_tail(agent)
        elif status in protocol.TURN_END_STATUSES and agent.tail is not None:
            await self.close_turn(agent)
        return protocol.ok()

    def _start_tail(self, agent: Agent) -> None:
        """Opens the turn's transcript tail at the current end of the file."""
        if agent.tail is not None or agent.transcript_path is None:
            return
        try:
            offset = os.path.getsize(agent.transcript_path)
        except OSError:
            offset = 0
        agent.tail = progress.TurnTail(path=agent.transcript_path, offset=offset)

    async def close_turn(self, agent: Agent) -> None:
        """Drains the tail one last time, flushes the chain, drops the tail."""
        # KNOWN-ISSUE 3 (wiki/KNOWN_ISSUES.md): when chain_covers already
        # said "not covered" in _op_message, this flush re-sends segments the
        # final reply duplicates; and a message op arriving after this drops
        # the tail, skipping the duplicate check entirely.
        tail, agent.tail = agent.tail, None
        if tail is not None:
            progress.drain_chunks(tail)  # the close flush skips the throttle
            await self._write_progress(agent, tail)

    async def progress_once(self) -> None:
        """One tail pass: new transcript chunks into the Telegram progress chain.

        The tail's lifetime is the turn (opened at registration, closed by a
        turn-end status), not the thinking flag — a mid-turn daemon restart
        re-registers the agent as idle, and the rest of the turn still streams.
        """
        now = time.monotonic()
        for agent in self.registry.snapshot():
            tail = agent.tail
            if tail is None:
                continue
            progress.drain_chunks(tail)
            if now - tail.last_touch < progress.PROGRESS_THROTTLE:
                continue
            await self._write_progress(agent, tail)

    async def _write_progress(self, agent: Agent, tail: progress.TurnTail) -> None:
        chats = self._agent_chats(agent.id)
        try:
            for segment in tail.pending:  # closed before ever shown — send as-is
                for chat_id in chats:
                    await self.provider.send_card(chat_id, segment, [])
            tail.pending.clear()
        except Exception as exc:  # progress is cosmetic, never fatal — but visible
            log.warning("progress pending write failed: %s: %s", exc.__class__.__name__, exc, exc_info=True)
            return
        text = tail.chain.text
        if not text or text == tail.sent_text:
            return
        for chat_id in chats:
            try:
                message_id = tail.messages.get(chat_id)
                if message_id is None:
                    message_id = await self.provider.send_card(chat_id, text, [])
                    if message_id is not None:
                        tail.messages[chat_id] = message_id
                else:
                    await self.provider.edit_text(chat_id, message_id, text)
            except Exception as exc:  # progress is cosmetic, never fatal — but visible
                log.warning("progress write failed: %s: %s", exc.__class__.__name__, exc, exc_info=True)
        tail.sent_text = text
        tail.last_touch = time.monotonic()
        log.info("progress: %d chars -> %d chat(s)", len(text), len(chats))

    async def progress_forever(self) -> None:
        while True:
            await asyncio.sleep(progress.PROGRESS_INTERVAL)
            try:
                await self.progress_once()
            except Exception:
                log.exception("progress tick failed")

    async def _op_message(self, request: Mapping[str, Any]) -> dict[str, Any]:
        agent = self.live_agent(request)
        text = protocol.req_str(request, "text")
        if agent.tail is not None:
            progress.drain_chunks(agent.tail)
            if progress.chain_covers(agent.tail, text):
                log.info("progress: final already streamed — duplicate skipped")
                return protocol.ok()
        await self.deliver_from_agent(agent.id, text)
        return protocol.ok()

    async def _op_events(self, request: Mapping[str, Any]) -> dict[str, Any]:
        agent = self.live_agent(request)
        cursor = protocol.req_int(request, "cursor")
        return await self._poll_events(agent, cursor)

    async def _op_context(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """The Stop hook reports last-turn context usage; /context reads it back."""
        agent = self.live_agent(request)
        agent.context = {
            "input": protocol.req_int(request, "input"),
            "output": protocol.req_int(request, "output"),
        }
        return protocol.ok()

    async def _poll_events(self, agent: Agent, cursor: int) -> dict[str, Any]:
        """Long-poll: hold the frame until an event arrives or the timeout expires."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._events_timeout
        while True:
            pending = agent.events_after(cursor)
            if pending:
                return protocol.ok(
                    events=[{"cursor": c, **event} for c, event in pending],
                    cursor=pending[-1][0],
                )
            agent.wakeup.clear()
            if agent.events_after(cursor):  # second look after clear — do not sleep through a push
                continue
            remaining = deadline - loop.time()
            if remaining <= 0:
                return protocol.ok(events=[], cursor=agent.last_cursor)
            try:
                await asyncio.wait_for(agent.wakeup.wait(), timeout=remaining)
            except TimeoutError:
                return protocol.ok(events=[], cursor=agent.last_cursor)

    def live_agent(self, request: Mapping[str, Any]) -> Agent:
        agent_id = protocol.req_str(request, "agent_id")
        agent = self.registry.get(agent_id)
        if agent is None:
            raise protocol.ProtocolError(f"agent {agent_id!r} is not registered")
        self.registry.touch(agent.id)
        self._start_tail(agent)  # self-heal: any op re-opens a lost tail
        return agent

    # -------------------------------------------------------- messenger side

    def is_allowed(self, user_id: int) -> bool:
        return self.config.allowed_ids is None or user_id in self.config.allowed_ids

    async def on_text(self, chat_id: int, text: str) -> None:
        self.state.note_chat(chat_id)
        agent = self.active_agent(chat_id)
        if agent is None:
            await self.provider.send_text(chat_id, self.select_hint)
            return
        agent.push({"type": protocol.EVENT_USER_MESSAGE, "chat_id": chat_id, "text": text})
        if agent.tmux_target is not None:
            # fire-and-forget: Telegram updates must not wait on tmux
            asyncio.create_task(self.deliver(agent.tmux_target, text))

    def active_agent(self, chat_id: int) -> Agent | None:
        """The chat's chosen agent; None if not chosen/offline and the choice is ambiguous."""
        chosen = self.state.active_agent(chat_id)
        if chosen is not None:
            agent = self.registry.get(chosen)
            if agent is not None and agent.status != protocol.STATUS_OFFLINE:
                return agent
            if agent is not None:
                return None  # the pick exists but died — make the user choose again
            # a missing pick is a stale pin — fall through to auto-selection;
            # a restart no longer leaves the registry empty (adopt_agents runs
            # at startup), so a stale pin means the pane itself is gone
        agents = [a for a in self.registry.snapshot() if not a.service]
        if len(agents) == 1:  # the only agent — auto-selected without questions
            return agents[0]
        alive = [a for a in agents if a.status != protocol.STATUS_OFFLINE]
        if len(alive) == 1:
            return alive[0]
        return None

    async def on_command(self, chat_id: int, command: str, rest: str = "") -> None:
        self.state.note_chat(chat_id)
        handler = self._commands.get(command)
        if handler is not None:
            await handler(chat_id, rest)

    async def on_callback(self, chat_id: int, data: str) -> None:
        for prefix, handler in self._callbacks.items():
            if data.startswith(prefix):
                await handler(chat_id, data[len(prefix) :])
                return
        log.warning("callback without handler: %r", data)

    # ------------------------------------------------------- restart adoption

    async def adopt_agents(self) -> list[Agent]:
        """Re-registers agents for tmux panes running claude after a restart.

        A restart wipes the in-memory registry, and an idle pane fires no hook
        to re-register itself — so the daemon pulls instead: panes running
        claude are exactly the sessions a SessionStart hook would register,
        and the hook's identity formula reproduces their ids deterministically.
        Liveness is the pane scan itself — no persisted liveness anywhere.
        """
        adopted: list[Agent] = []
        for pane in await self.list_panes():
            if pane["command"] != "claude":
                continue
            agent_id = delivery.agent_id_for(pane["path"], pane["id"])
            agent = self.registry.register(
                agent_id=agent_id,
                name=delivery.agent_name_for(pane["session"] or None, pane["path"], pane["id"]),
                session_name=pane["path"],
                modes=[],
                tmux_target=pane["id"],
                transcript_path=self.state.transcript(agent_id),
            )
            self._start_tail(agent)
            adopted.append(agent)
        if adopted:
            self.state.prune_transcripts({a.id for a in adopted})
        if adopted:
            log.info("adopted %d agent(s) from tmux panes", len(adopted))
        return adopted

    async def announce_adopted(self, adopted: list[Agent]) -> None:
        """One line in the chats whose pinned agent came back: downtime stops being silent."""
        if not adopted:
            return
        text = f"daemon restarted: {len(adopted)} agent(s) re-attached"
        chats = {c for a in adopted for c in self.state.chats_for_agent(a.id)}
        for chat_id in chats:
            try:
                await self.provider.send_text(chat_id, text)
            except Exception:  # the notice is cosmetic, never fatal
                log.warning("restart notice to chat %s failed", chat_id, exc_info=True)

    # ---------------------------------------------------------------- helpers

    def add_op(self, name: str, handler: OpHandler) -> None:
        self._ops[name] = handler

    def add_command(self, name: str, handler: CommandHandler) -> None:
        self._commands[name] = handler

    def add_callback(self, prefix: str, handler: CallbackHandler) -> None:
        self._callbacks[prefix] = handler

    def send_event(self, agent_id: str, event: dict[str, Any]) -> None:
        agent = self.registry.get(agent_id)
        if agent is not None:
            agent.push(event)

    def _agent_chats(self, agent_id: str) -> list[int]:
        """Where to deliver the agent's outgoing traffic: its chats, else all known ones."""
        return self.state.chats_for_agent(agent_id) or self.state.chat_ids()

    async def deliver_from_agent(self, agent_id: str, text: str) -> None:
        agent = self.registry.get(agent_id)
        if agent is None:
            log.warning("message from unregistered agent %s", agent_id)
            return
        if agent.service:
            # whoever spawned the service agent is waiting for this reply —
            # it becomes an event on the agent, never a broadcast to chats
            agent.push({"type": protocol.EVENT_MESSAGE, "text": text})
            return
        chats = self._agent_chats(agent_id)
        if not chats:
            log.warning("message from %s has nowhere to go: no chats yet", agent_id)
            return
        for chat_id in chats:
            await self.provider.send_text(chat_id, text, agent_name=agent.name)

    async def sweep_forever(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_INTERVAL)
            await self.sweep_once()

    async def typing_forever(self) -> None:
        while True:
            await asyncio.sleep(TYPING_INTERVAL)
            await self.typing_once()

    async def typing_once(self) -> None:
        """Renews the typing indicator for every agent mid-thought.

        Telegram's typing state fades after a few seconds; without this a
        long turn goes quiet. The 30-minute cap keeps a wedged agent from
        typing forever.
        """
        now = time.monotonic()
        for agent in self.registry.snapshot():
            if agent.status != protocol.STATUS_THINKING or agent.thinking_since is None:
                continue
            if now - agent.thinking_since > TYPING_MAX:
                continue
            for chat_id in self._agent_chats(agent.id):
                try:
                    await self.provider.chat_action(chat_id)
                except Exception:  # a failed renewal must not break the loop
                    log.debug("typing renewal failed", exc_info=True)

    async def sweep_once(self) -> None:
        for agent_id in self.registry.sweep_offline():
            log.info("agent %s went offline (liveness)", agent_id)
        for agent in self.registry.snapshot():
            # tmux agents have no resident connection to keep alive: their
            # liveness is the pane itself, not a silence timeout
            if agent.tmux_target is None or agent.status == protocol.STATUS_OFFLINE:
                continue
            if not await self.pane_alive(agent.tmux_target):
                agent.status = protocol.STATUS_OFFLINE
                log.info("agent %s went offline (tmux pane gone)", agent.id)


class JsonlServer:
    """UDS server on top of the core; frames are JSON lines, one per connection."""

    def __init__(self, core: Core, sock_path: Path) -> None:
        self._core = core
        self._sock_path = sock_path
        self._server: asyncio.AbstractServer | None = None
        self._sweeper: asyncio.Task[None] | None = None
        self._progress: asyncio.Task[None] | None = None

    async def serve(self) -> None:
        self._sock_path.parent.mkdir(parents=True, exist_ok=True)
        if self._sock_path.exists():
            self._sock_path.unlink()  # leftover from the previous run
        self._server = await asyncio.start_unix_server(
            self._handle_conn, path=str(self._sock_path), limit=FRAME_LIMIT
        )
        os.chmod(self._sock_path, 0o600)
        self._sweeper = asyncio.create_task(self._core.sweep_forever())
        self._progress = asyncio.create_task(self._core.progress_forever())
        log.info("listening on %s", self._sock_path)
        try:
            async with self._server:
                await self._server.serve_forever()
        finally:
            for task in (self._sweeper, self._progress):
                if task is not None:
                    task.cancel()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for task in (self._sweeper, self._progress):
            if task is not None:
                task.cancel()

    async def _handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            try:
                request = json.loads(line)
            except ValueError:
                request = None
            if not isinstance(request, dict):
                response = protocol.fail("malformed frame: expected a single JSON object line")
            else:
                response = await self._core.handle(request)
            writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
            await writer.drain()
        except Exception:  # transport must not die from a single frame
            log.exception("connection handling error")
        finally:
            writer.close()
