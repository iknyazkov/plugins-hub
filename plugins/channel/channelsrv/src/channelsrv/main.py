"""Entry point: a bare ``channelsrv`` run creates everything that is missing.

No subcommands: the config directory and the ``.env`` template appear on
first start; without a token — a clear error and exit. The server outlives
agents; it stops on SIGINT/SIGTERM, cleaning up the socket and pidfile.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from telegram.error import BadRequest, Forbidden

from channelsrv import config
from channelsrv.config import Config, ConfigError
from channelsrv.features import MENU, background_tasks, register_all
from channelsrv.outbox import OutboxProvider
from channelsrv.providers.telegram import TelegramProvider
from channelsrv.registry import Registry
from channelsrv.server import Core, JsonlServer
from channelsrv.state import StateStore

log = logging.getLogger("channelsrv")

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS
    )
    # httpx logs full request URLs at INFO — for Telegram that means the bot
    # token; keep it out of the log file
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler],
    )


def cleanup_files(base: Path, bound_ino: int | None) -> None:
    """Unlinks the socket and pidfile — but only the ones that are still ours.

    A slow-dying daemon must not delete a successor's files: the successor
    may have bound a fresh socket and written its own pid before this
    shutdown's cleanup runs (kill + immediate spawn), and deleting by path
    would orphan the survivor — hooks could no longer find it.
    """
    sock = base / "server.sock"
    try:
        if bound_ino is not None and sock.stat().st_ino == bound_ino:
            sock.unlink()
    except OSError:
        pass
    try:
        if (base / "server.pid").read_text().strip() == str(os.getpid()):
            (base / "server.pid").unlink()
    except OSError:
        pass


def stale_pids(lines: list[str], *, me: int, parent: int) -> list[int]:
    """Pids of other channelsrv processes: the uv wrapper and the daemon
    itself both match, ours and our wrapper's are excluded."""
    pids: list[int] = []
    for line in lines:
        parts = line.split()
        if not parts or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        if "channelsrv" not in line or pid in (me, parent):
            continue
        pids.append(pid)
    return pids


def kill_stale_daemons() -> None:
    """One token = one daemon, enforced in process: the youngest wins.

    A wedged elder can ignore SIGTERM for days (2026-11-17: a Monday zombie
    outlived every /restart — only the command's recipient dies — feeding
    409 polling conflicts and ghost re-streams from its stale tails). TERM
    first, KILL after a short grace.
    """
    try:
        out = subprocess.run(
            ["pgrep", "-f", "channelsrv"], capture_output=True, text=True, timeout=5, check=False
        )
        listing = subprocess.run(
            ["ps", "-o", "pid=,command="] + out.stdout.split(),
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        ).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return
    elders = stale_pids(listing, me=os.getpid(), parent=os.getppid())
    for pid in elders:
        log.warning("stale daemon %d — terminating (one token = one daemon)", pid)
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
    if not elders:
        return
    time.sleep(2.0)
    for pid in elders:
        if proc_alive(pid):
            log.warning("stale daemon %d ignored SIGTERM — killing", pid)
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)


def proc_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # signal 0: existence probe, nothing is delivered
        return True
    except OSError:
        return False


def main() -> None:
    base = config.base_dir()
    config.ensure_layout(base)
    config.apply_env_file(base)
    setup_logging(base / "logs" / "server.log")
    kill_stale_daemons()
    try:
        cfg = config.load_config(base)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    try:
        asyncio.run(serve(cfg, base))
    except KeyboardInterrupt:
        log.info("stopped by KeyboardInterrupt")


async def serve(cfg: Config, base: Path) -> None:
    stop = asyncio.Event()
    # a fatal from the polling watchdog must go through the same clean
    # shutdown path as a signal: unlink the socket and pidfile on the way out
    telegram = TelegramProvider(
        token=cfg.token, proxy_url=cfg.proxy_url, on_fatal=stop.set
    )
    # every user-bound send funnels through the outbox: enqueues are
    # instant, one worker delivers with retries — a flapping proxy delays
    # messages instead of eating them. BadRequest/Forbidden are ours
    # forever (malformed message, blocked chat): drop instead of retry.
    provider = OutboxProvider(telegram, permanent=(BadRequest, Forbidden))
    state = StateStore(base / "state.json")
    state.load()
    core = Core(config=cfg, registry=Registry(), state=state, provider=provider)
    provider.bind(core)
    register_all(core)
    # before Telegram traffic can arrive: re-attach agents from live panes
    adopted = await core.adopt_agents()

    server = JsonlServer(core, base / "server.sock")
    (base / "server.pid").write_text(str(os.getpid()))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # not everywhere (Windows)
            loop.add_signal_handler(sig, stop.set)

    server_task = asyncio.create_task(server.serve())
    typing_task = asyncio.create_task(core.typing_forever())
    feature_tasks = [asyncio.create_task(coro) for coro in background_tasks()]
    try:
        await provider.start(commands=MENU)
        await core.announce_adopted(adopted)
        log.info("ready: base %s, agents %d", base, len(core.registry.snapshot()))
        await stop.wait()
    finally:
        await server.stop()
        await provider.stop()
        server_task.cancel()
        typing_task.cancel()
        for task in feature_tasks:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task
        with contextlib.suppress(asyncio.CancelledError):
            await typing_task
        for task in feature_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        cleanup_files(base, server.bound_ino)
        log.info("stopped, socket and pidfile cleaned up")


if __name__ == "__main__":
    main()
