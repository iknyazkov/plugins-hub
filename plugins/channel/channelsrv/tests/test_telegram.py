"""Telegram provider smoke tests: command parsing and an offline build."""

from __future__ import annotations

from pathlib import Path

from channelsrv.config import Config
from channelsrv.providers.telegram import TelegramProvider, parse_command
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
