"""The /new and /close features: spawn and kill agent tmux sessions from the chat.

``/new <agent command> <session name>`` starts a detached tmux session
running the command through an interactive shell (aliases resolve); the new
session's own plugin hooks register it as an agent on their own.
``/close <session name>`` kills it — the pane-liveness sweep marks its
agents offline moments later.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from channelsrv import delivery
from channelsrv.server import Core


class Feature:
    def __init__(
        self,
        new_session: Callable[[str, str, str], Awaitable[None]] | None = None,
        close_session: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        # injectable for tests; defaults hit the real tmux
        self._new = new_session or delivery.new_session
        self._close = close_session or delivery.close_session

    def register(self, core: Core) -> None:
        async def new(chat_id: int, rest: str) -> None:
            parts = rest.split()
            if len(parts) != 3:
                await core.provider.send_text(
                    chat_id,
                    "Usage: /new <agent command> <session name> <path>\n\n"
                    "Spawns a detached tmux session that runs the agent command "
                    "in <path>; the command goes through an interactive shell, "
                    "so aliases resolve. Example:\n\n"
                    "/new claude work ~/Documents/plugins-hub",
                )
                return
            command, name, raw_path = parts
            path = os.path.expanduser(raw_path)
            if not os.path.isabs(path):
                await core.provider.send_text(
                    chat_id, f"Path must be absolute (got {raw_path!r}) — use the ~/… form."
                )
                return
            if not os.path.isdir(path):
                await core.provider.send_text(chat_id, f"No such directory: {path}")
                return
            try:
                await self._new(name, command, path)
            except ValueError as exc:
                await core.provider.send_text(chat_id, str(exc))
                return
            except Exception as exc:  # noqa: BLE001 — tmux failures are answerable, not fatal
                await core.provider.send_text(chat_id, f"tmux failed: {exc}")
                return
            await core.provider.send_text(
                chat_id,
                f"Spawned `{name}` in `{path}`. It registers itself as an agent in a "
                f"moment; attach locally: `tmux attach -t {name}`",
            )

        async def close(chat_id: int, rest: str) -> None:
            name = rest.strip()
            if not name or " " in name:
                await core.provider.send_text(
                    chat_id,
                    "Usage: /close <session name>\n\n"
                    "Kills the tmux session; its agents drop out of /agents "
                    "within seconds. Example:\n\n/close work",
                )
                return
            try:
                await self._close(name)
            except ValueError as exc:
                await core.provider.send_text(chat_id, str(exc))
                return
            except Exception as exc:  # noqa: BLE001
                await core.provider.send_text(chat_id, f"tmux failed: {exc}")
                return
            await core.provider.send_text(chat_id, f"Closed `{name}`.")

        core.add_command("new", new)
        core.add_command("close", close)
