"""The /status feature: a snapshot table of all agents. No buttons, no auto-refresh."""

from __future__ import annotations

from channelsrv.registry import Agent
from channelsrv.server import Core


def _line(agent: Agent) -> str:
    parts = [f"**{agent.name}**", agent.session_name, f"mode: {agent.mode or '—'}", agent.status]
    if agent.detail:
        parts[3] += f" ({agent.detail})"
    return " · ".join(parts)


class Feature:
    def register(self, core: Core) -> None:
        async def status(chat_id: int, rest: str) -> None:
            agents = core.registry.snapshot()
            if not agents:
                await core.provider.send_text(chat_id, "No agents — start a session with the plugin.")
                return
            lines = ["**Agents:**", *(f"{i}. {_line(a)}" for i, a in enumerate(agents, 1))]
            await core.provider.send_text(chat_id, "\n".join(lines))

        core.add_command("status", status)
