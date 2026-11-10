"""The approvals feature: a card with ✅/❌ buttons, the result comes as an approval_result event.

The card goes to all of the agent's chats (usually one); after a press it is
edited to the verdict. If the agent dies waiting, the card is marked expired.
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
class _Pending:
    agent_id: str
    messages: list[tuple[int, int | None]]  # (chat_id, message_id)


@dataclass
class Feature:
    _pending: dict[str, _Pending] = field(default_factory=dict)
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
            buttons = [[
                CardButton("✅ Yes", f"{PREFIX}{approval_id}:y"),
                CardButton("❌ No", f"{PREFIX}{approval_id}:n"),
            ]]
            chats = core.state.chats_for_agent(agent.id) or core.state.chat_ids()
            if not chats:
                raise protocol.ProtocolError("no known chats — message the bot first")
            messages = [
                (chat_id, await core.provider.send_card(chat_id, text, buttons)) for chat_id in chats
            ]
            self._pending[approval_id] = _Pending(agent_id=agent.id, messages=messages)
            return protocol.ok(approval_id=approval_id)

        async def answer(chat_id: int, args: str) -> None:
            approval_id, _, verdict = args.rpartition(":")
            pending = self._pending.pop(approval_id, None)
            if pending is None:
                return  # an old/duplicate card — just ignore it
            approved = verdict == "y"
            mark = "✅ Approved" if approved else "❌ Rejected"
            agent = core.registry.get(pending.agent_id)
            if agent is None:
                mark = f"⌛ Expired — agent is gone. Was: {mark}"
            else:
                core.send_event(
                    agent.id,
                    {
                        "type": protocol.EVENT_APPROVAL_RESULT,
                        "approval_id": approval_id,
                        "approved": approved,
                        "chat_id": chat_id,
                    },
                )
            for msg_chat, message_id in pending.messages:
                if message_id is not None:
                    await core.provider.edit_text(msg_chat, message_id, f"{mark} `{approval_id}`")

        core.add_op(protocol.OP_APPROVAL_REQUEST, request)
        core.add_callback(PREFIX, answer)
