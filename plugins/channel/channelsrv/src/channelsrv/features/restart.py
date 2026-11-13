"""The /restart feature: the daemon respawns itself and exits.

A restart from Telegram cannot wait for a later hook run to respawn the
daemon — while it is down, nothing polls Telegram. So the feature starts a
fresh daemon first (the same ``uv run channelsrv`` the bridge spawns), says
goodbye, and exits. The child's uv startup takes about a second: the parent
is gone by the time the new socket binds, and the leftover socket/pidfile
cleanup on startup exists for exactly this handoff.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from channelsrv.server import Core


def srv_dir() -> Path:
    """The channelsrv project root — the cwd ``uv run channelsrv`` expects."""
    return Path(__file__).resolve().parents[3]


def spawn_daemon() -> None:
    subprocess.Popen(
        ["uv", "run", "channelsrv"],
        cwd=str(srv_dir()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class Feature:
    def __init__(
        self,
        spawn: Callable[[], None] = spawn_daemon,
        exiter: Callable[[int], None] = os._exit,
    ) -> None:
        self._spawn = spawn
        self._exiter = exiter

    def register(self, core: Core) -> None:
        async def restart(chat_id: int, rest: str) -> None:
            await core.provider.send_text(
                chat_id, "Restarting the daemon — back in a few seconds."
            )
            try:
                self._spawn()
            except OSError as exc:  # e.g. uv not found — stay alive, say why
                await core.provider.send_text(
                    chat_id, f"restart failed: {exc.__class__.__name__}: {exc}"
                )
                return
            self._exiter(0)

        core.add_command("restart", restart)
