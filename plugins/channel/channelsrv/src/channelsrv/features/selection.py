"""The agent-selection feature: /agents with inline buttons, the choice persists in state."""

from __future__ import annotations

from channelsrv.providers.base import CardButton
from channelsrv.server import Core

PREFIX = "sel:"


class Feature:
    def register(self, core: Core) -> None:
        core.select_hint = "No agent selected or available. Pick one: /agents"

        async def agents(chat_id: int, rest: str) -> None:
            agents = core.registry.snapshot()
            if not agents:
                await core.provider.send_text(chat_id, "No agents — start a session with the plugin.")
                return
            current = core.state.active_agent(chat_id)
            rows = [
                [CardButton(("* " if a.id == current else "") + f"{a.name} · {a.status}", f"{PREFIX}{a.id}")]
                for a in agents
            ]
            await core.provider.send_card(chat_id, "**Pick an agent:**", rows)

        async def pick(chat_id: int, agent_id: str) -> None:  # callback, not a command
            agent = core.registry.get(agent_id)
            if agent is None:
                await core.provider.send_text(chat_id, "That agent is already gone. Fresh list: /agents")
                return
            core.state.set_active_agent(chat_id, agent.id)
            await core.provider.send_text(chat_id, f"Active: **{agent.name}**")

        core.add_command("agents", agents)
        core.add_callback(PREFIX, pick)
