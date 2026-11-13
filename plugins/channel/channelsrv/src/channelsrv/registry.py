"""Agent registry: life from registrations, cursor events, liveness.

The registry lives in memory: server state is derived from bridge
re-registrations. Liveness is the last-touch time (any op or the events
loop); whoever stays silent longer than the grace period gets marked
``offline`` by the server itself.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from channelsrv import progress, protocol

#: How many events per agent we keep — only the nearest retrospective is needed.
BACKLOG_LIMIT = 256


@dataclass
class Agent:
    id: str
    name: str
    session_name: str
    modes: tuple[str, ...]
    tmux_target: str | None = None  # set: user messages are pasted into the pane
    status: str = protocol.STATUS_IDLE
    detail: str | None = None
    mode: str | None = None
    thinking_since: float | None = None  # the typing indicator renews while set
    #: transcript JSONL path (reported at registration) — the progress tail reads it
    transcript_path: str | None = None
    #: live-turn transcript tail; None between turns
    tail: progress.TurnTail | None = None
    #: last known context usage {"input": int, "output": int}, reported by the Stop hook
    context: dict[str, int] | None = None
    #: service agents (e.g. the NL-command converter) are captured, never broadcast
    service: bool = False
    events: deque[tuple[int, dict[str, Any]]] = field(default_factory=deque)
    next_cursor: int = 1
    wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    last_seen: float = 0.0

    def push(self, event: dict[str, Any]) -> int:
        """Records an event, wakes the long-poll, returns the event cursor."""
        cursor = self.next_cursor
        self.next_cursor += 1
        self.events.append((cursor, event))
        while len(self.events) > BACKLOG_LIMIT:
            self.events.popleft()
        self.wakeup.set()
        return cursor

    def events_after(self, cursor: int) -> list[tuple[int, dict[str, Any]]]:
        return [(c, event) for c, event in self.events if c > cursor]

    @property
    def last_cursor(self) -> int:
        return self.next_cursor - 1


class Registry:
    """A single writer — the server; time comes from injectable clocks (tests)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._agents: dict[str, Agent] = {}

    def register(
        self,
        *,
        agent_id: str,
        name: str,
        session_name: str,
        modes: Sequence[str],
        tmux_target: str | None = None,
        transcript_path: str | None = None,
        service: bool = False,
    ) -> Agent:
        agent = Agent(
            id=agent_id,
            name=name,
            session_name=session_name,
            modes=tuple(modes),
            tmux_target=tmux_target,
            transcript_path=transcript_path,
            service=service,
            last_seen=self._clock(),
        )
        self._agents[agent_id] = agent  # re-registration replaces, no history carried over
        return agent

    def deregister(self, agent_id: str) -> bool:
        return self._agents.pop(agent_id, None) is not None

    def get(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def touch(self, agent_id: str) -> None:
        agent = self._agents.get(agent_id)
        if agent is not None:
            agent.last_seen = self._clock()

    def snapshot(self) -> list[Agent]:
        return list(self._agents.values())

    def sweep_offline(self, grace: float = 30.0) -> list[str]:
        """Marks silent agents; returns ids that went offline in this pass.

        tmux agents are exempt: they only touch the server during turns, so
        silence says nothing — the core checks their pane instead.
        """
        now = self._clock()
        offlined: list[str] = []
        for agent in self._agents.values():
            if agent.tmux_target is not None:
                continue
            if agent.status != protocol.STATUS_OFFLINE and now - agent.last_seen > grace:
                agent.status = protocol.STATUS_OFFLINE
                offlined.append(agent.id)
        return offlined
