"""The approvals feature: a card with ✅/❌ buttons, the result comes as an approval_result event.

The card goes to all of the agent's chats (usually one); the verdict is a
follow-up message — cards are never edited (ADR-0016: no edits anywhere).
A press on an already-answered or stale card answers "already decided".
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from channelsrv import protocol
from channelsrv.providers.base import CardButton
from channelsrv.server import Core

PREFIX = "ap:"
DETAILS_LIMIT = 1000


@dataclass
class Feature:
    _pending: dict[str, str] = field(default_factory=dict)  # approval_id -> agent_id
    _seq: itertools.count[int] = field(default_factory=itertools.count)

    def register(self, core: Core) -> None:
        async def request(request: Mapping[str, Any]) -> dict[str, Any]:
            agent = core.live_agent(request)
            kind = protocol.req_str(request, "kind")
            summary = protocol.req_str(request, "summary")
            details = protocol.opt_str(request, "details")
            text = f"**⚠️ Approval · {agent.name}**\n{kind}: {summary}"
            if details:
                text += f"\n\n```\n{details[:DETAILS_LIMIT]}\n```"
            approval_id = f"a{next(self._seq)}"
            buttons = [
                [
                    CardButton("✅ Yes", f"{PREFIX}{approval_id}:y"),
                    CardButton("❌ No", f"{PREFIX}{approval_id}:n"),
                ]
            ]
            chats = core.state.chats_for_agent(agent.id) or core.state.chat_ids()
            if not chats:
                raise protocol.ProtocolError("no known chats — message the bot first")
            self._pending[approval_id] = agent.id
            for chat_id in chats:
                await core.provider.send_card(chat_id, text, buttons, instant=True)
            return protocol.ok(approval_id=approval_id)

        async def answer(chat_id: int, args: str) -> None:
            approval_id, _, verdict = args.rpartition(":")
            agent_id = self._pending.pop(approval_id, None)
            if agent_id is None:
                await core.provider.send_text(
                    chat_id, f"`{approval_id}` — already decided", instant=True
                )
                return
            approved = verdict == "y"
            agent = core.registry.get(agent_id)
            if agent is None:
                mark = "⌛ Expired — agent is gone"
            else:
                mark = "✅ Approved" if approved else "❌ Rejected"
                core.send_event(
                    agent.id,
                    {
                        "type": protocol.EVENT_APPROVAL_RESULT,
                        "approval_id": approval_id,
                        "approved": approved,
                        "chat_id": chat_id,
                    },
                )
            await core.provider.send_text(
                chat_id, f"{mark} `{approval_id}`", instant=True
            )

        core.add_op(protocol.OP_APPROVAL_REQUEST, request)
        core.add_callback(PREFIX, answer)
