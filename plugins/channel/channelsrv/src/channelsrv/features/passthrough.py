"""The /command feature: passes a command line straight into the agent's pane.

``/command /reload-plugins --force`` pastes ``/reload-plugins --force`` into
the active agent — for commands the daemon itself would otherwise swallow.
Without a pane (an MCP-bridge agent) there is nothing to paste into.
"""

from __future__ import annotations

from channelsrv.server import Core

PREFIX = "command"


class Feature:
    def register(self, core: Core) -> None:
        async def passthrough(chat_id: int, rest: str) -> None:
            agent = core.active_agent(chat_id)
            if agent is None:
                await core.provider.send_text(chat_id, core.select_hint)
                return
            text = rest.strip()
            if not text:
                await core.provider.send_text(chat_id, "Usage: /command <command for the agent>")
                return
            if agent.tmux_target is None:
                await core.provider.send_text(
                    chat_id, "The active agent has no pane — it reads events, not keystrokes."
                )
                return
            await core.deliver(agent.tmux_target, text)

        core.add_command(PREFIX, passthrough)
