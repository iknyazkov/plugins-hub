"""Telegram provider smoke tests: command parsing, an offline build, the poll watchdog."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from channelsrv.config import Config
from channelsrv.providers.telegram import PollWatchdog, TelegramProvider, parse_command
from channelsrv.registry import Registry
from channelsrv.server import Core
from channelsrv.state import StateStore
from tests.fakes import FakeProvider


def test_parse_command() -> None:
    assert parse_command("/status") == ("status", "")
    assert parse_command("/command /reload-plugins --force") == ("command", "/reload-plugins --force")
    assert parse_command("/mode@my_bot auto") == ("mode", "auto")
    assert parse_command("hello") is None
    assert parse_command("/") is None


def test_provider_builds_offline(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.json")
    state.load()
    core = Core(
        config=Config(token="123:abc", proxy_url="socks5://127.0.0.1:1080", allowed_ids=None),
        registry=Registry(),
        state=state,
        provider=FakeProvider(),
    )
    provider = TelegramProvider(token="123:abc", proxy_url="socks5://127.0.0.1:1080")
    provider.bind(core)
    assert provider is not None
    # the polling transport is the watchdog's eyes: completed polls must poke it
    assert provider._poll_request.on_poll == provider._watchdog.poke


async def test_watchdog_restarts_then_gives_up() -> None:
    restarts = 0
    fatal: list[bool] = []

    async def restart() -> None:
        nonlocal restarts
        restarts += 1

    watchdog = PollWatchdog(
        restart=restart,
        on_fatal=lambda: fatal.append(True),
        threshold=0.01,
        interval=0.01,
        restart_limit=2,
    )
    await asyncio.wait_for(asyncio.create_task(watchdog.run()), 5.0)
    assert restarts == 2
    assert fatal == [True]


async def test_watchdog_restarts_fail_safely() -> None:
    fatal: list[bool] = []

    async def broken_restart() -> None:
        raise RuntimeError("updater refuses")

    watchdog = PollWatchdog(
        restart=broken_restart,
        on_fatal=lambda: fatal.append(True),
        threshold=0.01,
        interval=0.01,
        restart_limit=1,
    )
    await asyncio.wait_for(asyncio.create_task(watchdog.run()), 5.0)
    assert fatal == [True]


async def test_watchdog_poke_resets_the_budget() -> None:
    restarts = 0
    fatal: list[bool] = []

    async def restart() -> None:
        nonlocal restarts
        restarts += 1

    watchdog = PollWatchdog(
        restart=restart,
        on_fatal=lambda: fatal.append(True),
        threshold=0.05,
        interval=0.01,
        restart_limit=1,
    )
    task = asyncio.create_task(watchdog.run())
    for _ in range(8):
        await asyncio.sleep(0.02)
        watchdog.poke()  # polls keep completing — no restarts must happen
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert restarts == 0
    assert fatal == []
