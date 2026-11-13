"""Watchdog tests: signature presses on quiescent screens, /screen inspection."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from channelsrv.config import Config
from channelsrv.features.watchdog import Feature, Signature, load_signatures
from channelsrv.registry import Registry
from channelsrv.server import Core
from channelsrv.state import StateStore
from tests.fakes import FakeProvider

BILLING = Signature(
    id="billing",
    patterns=(re.compile(r"isn't eligible"),),
    keys=("Enter",),
)


def make_core(tmp_path: Path) -> tuple[Core, FakeProvider]:
    provider = FakeProvider()
    state = StateStore(tmp_path / "state.json")
    state.load()
    core = Core(
        config=Config(token="t", proxy_url=None, allowed_ids=None),
        registry=Registry(),
        state=state,
        provider=provider,
        deliver=None,
    )
    return core, provider


def reg_pane_agent(core: Core, agent_id: str, target: str | None) -> None:
    core.registry.register(
        agent_id=agent_id, name=agent_id, session_name="proj", modes=[], tmux_target=target
    )


class Screens:
    """Mutable fake of delivery.capture_pane: target -> current screen text."""

    def __init__(self) -> None:
        self.by_target: dict[str, str] = {}
        self.calls: list[str] = []

    async def __call__(self, target: str) -> str:
        self.calls.append(target)
        return self.by_target[target]


class Presses:
    async def __call__(self, target: str, *keys: str) -> None:
        self.log.append((target, keys))

    def __init__(self) -> None:
        self.log: list[tuple[str, tuple[str, ...]]] = []


def make_feature(
    screens: Screens | Callable[[str], Awaitable[str]],
    presses: Presses,
    signatures: tuple[Signature, ...] = (BILLING,),
) -> Feature:
    return Feature(capture=screens, send_keys=presses, signatures=signatures)


async def tick_twice(feature: Feature) -> None:
    """One tick records the screen, the second one may act on a quiescent pane."""
    await feature._tick_once()
    await feature._tick_once()


# ----------------------------------------------------------------- watchdog


async def test_first_tick_records_screen_without_pressing(tmp_path: Path) -> None:
    core, _ = make_core(tmp_path)
    reg_pane_agent(core, "a1", "%1")
    screens, presses = Screens(), Presses()
    screens.by_target["%1"] = "We're changing auto mode... isn't eligible"
    feature = make_feature(screens, presses)
    feature.register(core)

    await feature._tick_once()

    assert presses.log == []


async def test_presses_known_quiescent_screen(tmp_path: Path) -> None:
    core, _ = make_core(tmp_path)
    reg_pane_agent(core, "a1", "%1")
    screens, presses = Screens(), Presses()
    screens.by_target["%1"] = "isn't eligible"
    feature = make_feature(screens, presses)
    feature.register(core)

    await tick_twice(feature)

    assert presses.log == [("%1", ("Enter",))]


async def test_unknown_screen_is_never_pressed(tmp_path: Path) -> None:
    core, _ = make_core(tmp_path)
    reg_pane_agent(core, "a1", "%1")
    screens, presses = Screens(), Presses()
    screens.by_target["%1"] = "some other blocking screen ???"
    feature = make_feature(screens, presses)
    feature.register(core)

    for _ in range(4):
        await feature._tick_once()

    assert presses.log == []


async def test_all_patterns_must_match(tmp_path: Path) -> None:
    import re

    core, _ = make_core(tmp_path)
    reg_pane_agent(core, "a1", "%1")
    sig = Signature(id="both", patterns=(re.compile("alpha"), re.compile("beta")), keys=("Enter",))
    screens, presses = Screens(), Presses()
    screens.by_target["%1"] = "alpha only"
    feature = make_feature(screens, presses, signatures=(sig,))
    feature.register(core)

    await tick_twice(feature)

    assert presses.log == []
    screens.by_target["%1"] = "alpha beta"
    await tick_twice(feature)
    assert presses.log == [("%1", ("Enter",))]


async def test_screen_change_resets_press_budget(tmp_path: Path) -> None:
    core, _ = make_core(tmp_path)
    reg_pane_agent(core, "a1", "%1")
    screens, presses = Screens(), Presses()
    screens.by_target["%1"] = "isn't eligible"
    feature = make_feature(screens, presses)
    feature.register(core)

    await tick_twice(feature)  # press 1
    screens.by_target["%1"] = "agent is working..."  # screen changes: episode over
    await feature._tick_once()
    screens.by_target["%1"] = "isn't eligible"  # modal is back, fresh budget
    await tick_twice(feature)

    assert presses.log.count(("%1", ("Enter",))) == 2


async def test_press_budget_caps_at_three(tmp_path: Path) -> None:
    core, _ = make_core(tmp_path)
    reg_pane_agent(core, "a1", "%1")
    screens, presses = Screens(), Presses()
    screens.by_target["%1"] = "isn't eligible"
    feature = make_feature(screens, presses)
    feature.register(core)

    for _ in range(8):
        await feature._tick_once()

    assert presses.log.count(("%1", ("Enter",))) == 3


async def test_agents_without_tmux_target_are_ignored(tmp_path: Path) -> None:
    core, _ = make_core(tmp_path)
    reg_pane_agent(core, "a1", None)
    screens, presses = Screens(), Presses()
    feature = make_feature(screens, presses)
    feature.register(core)

    await tick_twice(feature)

    assert screens.calls == []
    assert presses.log == []


async def test_capture_failure_skips_agent(tmp_path: Path) -> None:
    core, _ = make_core(tmp_path)
    reg_pane_agent(core, "a1", "%1")

    async def broken(target: str) -> str:
        raise RuntimeError("tmux capture-pane exited with 1")

    presses = Presses()
    feature = make_feature(broken, presses)
    feature.register(core)

    await tick_twice(feature)

    assert presses.log == []


# ------------------------------------------------------------------ /screen


async def test_screen_shows_active_agent_pane(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    reg_pane_agent(core, "a1", "%1")
    screens, presses = Screens(), Presses()
    screens.by_target["%1"] = "Do you trust the files in this folder?"
    feature = make_feature(screens, presses)
    feature.register(core)
    core.state.set_active_agent(1, "a1")

    await core.on_command(1, "screen")

    text = provider.texts[0][1]
    assert "Do you trust" in text


async def test_screen_without_selection_hints(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    feature = make_feature(Screens(), Presses())
    feature.register(core)

    await core.on_command(1, "screen")

    assert provider.texts[0][1] == core.select_hint


async def test_screen_agent_without_pane(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    reg_pane_agent(core, "a1", None)
    feature = make_feature(Screens(), Presses())
    feature.register(core)
    core.state.set_active_agent(1, "a1")

    await core.on_command(1, "screen")

    assert "no tmux pane" in provider.texts[0][1]


async def test_screen_capture_failure_reports(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    reg_pane_agent(core, "a1", "%1")

    async def broken(target: str) -> str:
        raise RuntimeError("tmux capture-pane exited with 1")

    feature = make_feature(broken, Presses())
    feature.register(core)
    core.state.set_active_agent(1, "a1")

    await core.on_command(1, "screen")

    assert "capture failed" in provider.texts[0][1]


# ------------------------------------------------------------ signatures IO


def write_sigs(tmp_path: Path, payload: Any) -> Path:
    path = tmp_path / "signatures.json"
    path.write_text(json.dumps(payload))
    return path


def test_load_signatures_parses_and_compiles(tmp_path: Path) -> None:
    path = write_sigs(
        tmp_path,
        [{"id": "x", "match": ["a\\.b", "c"], "keys": ["Enter"]}],
    )
    sigs = load_signatures(path)
    assert len(sigs) == 1
    assert sigs[0].id == "x"
    assert sigs[0].keys == ("Enter",)
    assert sigs[0].patterns[0].search("a.b")


@pytest.mark.parametrize(
    "payload",
    [
        [],  # no entries at all
        [{"id": "", "match": ["a"], "keys": ["Enter"]}],  # empty id
        [{"id": "x", "match": [], "keys": ["Enter"]}],  # no patterns
        [{"id": "x", "match": ["("], "keys": ["Enter"]}],  # broken regex
        [{"id": "x", "match": ["a"], "keys": []}],  # no keys
        [{"match": ["a"], "keys": ["Enter"]}],  # missing id
        {"not": "a list"},  # wrong shape
    ],
)
def test_load_signatures_rejects_illegal(tmp_path: Path, payload: Any) -> None:
    with pytest.raises(ValueError):
        load_signatures(write_sigs(tmp_path, payload))


def test_packaged_signatures_load() -> None:
    sigs = load_signatures()  # the file shipped with the package
    assert any("billing" in s.id for s in sigs)
