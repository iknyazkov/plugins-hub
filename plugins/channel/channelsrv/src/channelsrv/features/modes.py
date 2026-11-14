"""The modes feature: /mode renders buttons from the active agent's modes[],
the choice is sent to the agent as a mode_set event."""

from __future__ import annotations

from channelsrv import protocol
from channelsrv.providers.base import CardButton
from channelsrv.server import Core

PREFIX = "mode:"


class Feature:
    def register(self, core: Core) -> None:
        async def show(chat_id: int, rest: str) -> None:
            agent = core.active_agent(chat_id)
            if agent is None:
                await core.provider.send_text(chat_id, core.select_hint)
                return
            if not agent.modes:
                await core.provider.send_text(chat_id, f"**{agent.name}** has no modes.")
                return
            rows = [
                [CardButton(("● " if m == agent.mode else "") + m, f"{PREFIX}{m}")] for m in agent.modes
            ]
            title = f"**Mode · {agent.name}** · now: {agent.mode or '—'}"
            await core.provider.send_card(chat_id, title, rows)

        async def set_mode(chat_id: int, mode: str) -> None:
            # KNOWN-ISSUE 2 (wiki/KNOWN_ISSUES.md): the event below is only
            # consumed by an MCP events long-poll — tmux agents never see a
            # mode_set (on_text pastes user messages only), so /mode is a
            # no-op for them.
            agent = core.active_agent(chat_id)
            if agent is None:
                await core.provider.send_text(chat_id, core.select_hint)
                return
            core.send_event(
                agent.id,
                {"type": protocol.EVENT_MODE_SET, "mode": mode, "chat_id": chat_id},
            )
            await core.provider.send_text(chat_id, f"Mode **{mode}** sent to {agent.name}")

        core.add_command("mode", show)
        core.add_callback(PREFIX, set_mode)
