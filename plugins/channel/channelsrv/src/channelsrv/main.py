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
import sys
from pathlib import Path

from channelsrv import config
from channelsrv.config import Config, ConfigError
from channelsrv.features import MENU, background_tasks, register_all
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


def main() -> None:
    base = config.base_dir()
    config.ensure_layout(base)
    config.apply_env_file(base)
    setup_logging(base / "logs" / "server.log")
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
    provider = TelegramProvider(token=cfg.token, proxy_url=cfg.proxy_url, on_fatal=stop.set)
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
