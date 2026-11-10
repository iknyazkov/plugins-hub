"""The /context feature: last-turn context usage of the active agent.

The numbers come from the Stop hook (it parses the session transcript and
reports them via the ``context`` op); this command only formats what is
stored on the agent. Absolute tokens only — no window limit is knowable for
third-party models, so no percentages.
"""

from __future__ import annotations

from channelsrv.server import Core


def _fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    text = f"{n / 1000:.1f}".rstrip("0").rstrip(".")
    return f"{text}k"


class Feature:
    def register(self, core: Core) -> None:
        async def context(chat_id: int, rest: str) -> None:
            agent = core.active_agent(chat_id)
            if agent is None:
                await core.provider.send_text(chat_id, core.select_hint)
                return
            usage = agent.context
            if usage is None:
                await core.provider.send_text(
                    chat_id, "No context data yet — the agent hasn't finished a turn."
                )
                return
            await core.provider.send_text(
                chat_id,
                f"ctx {_fmt_tokens(usage['input'])} in · "
                f"{_fmt_tokens(usage['output'])} out (last turn)",
            )

        core.add_command("context", context)
