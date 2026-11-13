"""The /restart feature: spawn a successor, say goodbye, exit."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from channelsrv.config import Config
from channelsrv.features import restart
from channelsrv.features.restart import Feature as RestartFeature
from channelsrv.registry import Registry
from channelsrv.server import Core
from channelsrv.state import StateStore
from tests.fakes import FakeProvider


def make_world(
    tmp_path: Path, spawn: Callable[[], None], exiter: Callable[[int], None]
) -> tuple[Core, FakeProvider]:
    provider = FakeProvider()
    state = StateStore(tmp_path / "state.json")
    state.load()
    core = Core(
        config=Config(token="t", proxy_url=None, allowed_ids=None),
        registry=Registry(),
        state=state,
        provider=provider,
    )
    RestartFeature(spawn=spawn, exiter=exiter).register(core)
    return core, provider


async def test_restart_spawns_successor_and_exits(tmp_path: Path) -> None:
    spawns: list[int] = []
    exits: list[int] = []
    core, provider = make_world(tmp_path, lambda: spawns.append(1), exits.append)
    await core.on_command(1, "restart")
    assert spawns == [1]
    assert exits == [0]
    assert "Restarting" in provider.texts[0][1]


async def test_restart_failure_keeps_daemon_alive(tmp_path: Path) -> None:
    def boom() -> None:
        raise FileNotFoundError("uv")  # what Popen raises without uv on PATH

    exits: list[int] = []
    core, provider = make_world(tmp_path, boom, exits.append)
    await core.on_command(1, "restart")
    assert exits == []
    assert "restart failed" in provider.texts[1][1]


async def test_srv_dir_is_the_project_root() -> None:
    root = restart.srv_dir()
    assert (root / "pyproject.toml").exists()
    assert (root / "src" / "channelsrv" / "__init__.py").is_file()
