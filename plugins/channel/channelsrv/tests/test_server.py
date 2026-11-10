"""Core integration tests: a real UDS socket + a fake provider."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from channelsrv import protocol
from channelsrv.config import Config
from channelsrv.registry import Registry
from channelsrv.server import Core, JsonlServer
from channelsrv.state import StateStore
from tests.fakes import FakeProvider

_SOCK_SEQ = itertools.count()


def make_core(
    tmp_path: Path,
    provider: FakeProvider | None = None,
    deliver: Callable[[str, str], Coroutine[Any, Any, None]] | None = None,
    pane_alive: Callable[[str], Coroutine[Any, Any, bool]] | None = None,
) -> Core:
    provider = provider or FakeProvider()
    state = StateStore(tmp_path / "state.json")
    state.load()
    return Core(
        config=Config(token="t", proxy_url=None, allowed_ids=None),
        registry=Registry(),
        state=state,
        provider=provider,
        events_timeout=0.2,
        deliver=deliver,
        pane_alive=pane_alive,
    )


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[SimpleNamespace]:
    provider = FakeProvider()
    core = make_core(tmp_path, provider)
    # macOS AF_UNIX limit ~104 bytes — pytest tmp paths are too long
    sock = Path(f"/tmp/channelsrv-test-{next(_SOCK_SEQ)}.sock")
    server = JsonlServer(core, sock)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if sock.exists():
            break
        await asyncio.sleep(0.01)
    yield SimpleNamespace(core=core, provider=provider, sock=sock)
    await server.stop()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    with contextlib.suppress(FileNotFoundError):
        sock.unlink()


async def call(sock: Path, request: dict[str, Any] | str) -> dict[str, Any]:
    reader, writer = await asyncio.open_unix_connection(str(sock))
    raw = request if isinstance(request, str) else json.dumps(request)
    writer.write((raw + "\n").encode())
    await writer.drain()
    line = await reader.readline()
    writer.close()
    response: dict[str, Any] = json.loads(line)
    return response


def register_req(agent_id: str, name: str = "agent", modes: list[str] | None = None) -> dict[str, Any]:
    return {
        "v": 1,
        "op": "register",
        "agent_id": agent_id,
        "name": name,
        "session_name": "proj",
        "modes": modes or [],
    }


async def test_healthz(world: SimpleNamespace) -> None:
    resp = await call(world.sock, {"v": 1, "op": "healthz"})
    assert resp["ok"] is True
    assert isinstance(resp["pid"], int)


async def test_bad_version_and_garbage(world: SimpleNamespace) -> None:
    assert (await call(world.sock, {"v": 2, "op": "healthz"}))["ok"] is False
    assert (await call(world.sock, "not json"))["ok"] is False
    assert (await call(world.sock, {"v": 1, "op": "junk"}))["ok"] is False


async def test_register_validates_fields(world: SimpleNamespace) -> None:
    bad = register_req("")  # empty agent_id
    assert (await call(world.sock, bad))["ok"] is False
    bad_modes = register_req("a1")
    bad_modes["modes"] = "auto"
    assert (await call(world.sock, bad_modes))["ok"] is False
    assert (await call(world.sock, register_req("a1", modes=["auto", "plan"])))["ok"] is True


async def test_ops_of_unknown_agent_fail(world: SimpleNamespace) -> None:
    for op in ("status", "message", "events"):
        resp = await call(world.sock, {"v": 1, "op": op, "agent_id": "ghost", "status": "idle", "text": "x", "cursor": 0})
        assert resp["ok"] is False, op


async def test_status_flow_and_thinking_indicator(world: SimpleNamespace) -> None:
    await call(world.sock, register_req("a1"))
    resp = await call(world.sock, {"v": 1, "op": "status", "agent_id": "a1", "status": "thinking", "detail": "pondering"})
    assert resp["ok"] is True
    # no chats yet — nowhere to send the indicator, but no crash either
    assert world.provider.actions == []
    bad = await call(world.sock, {"v": 1, "op": "status", "agent_id": "a1", "status": "offline"})
    assert bad["ok"] is False  # offline is the server's verdict, not a bridge's


async def test_user_message_roundtrip(world: SimpleNamespace) -> None:
    await call(world.sock, register_req("a1", name="claude-1"))
    await world.core.on_text(42, "hello")  # the only agent — auto-selected
    resp = await call(world.sock, {"v": 1, "op": "events", "agent_id": "a1", "cursor": 0})
    assert resp["ok"] is True
    events = resp["events"]
    assert len(events) == 1
    assert events[0]["type"] == protocol.EVENT_USER_MESSAGE
    assert events[0]["chat_id"] == 42
    assert events[0]["text"] == "hello"
    # repeat with the current cursor — empty (0.2s timeout in the fixture)
    empty = await call(world.sock, {"v": 1, "op": "events", "agent_id": "a1", "cursor": events[0]["cursor"]})
    assert empty["events"] == []


async def test_agent_message_reaches_chat_with_name(world: SimpleNamespace) -> None:
    await call(world.sock, register_req("a1", name="claude-1"))
    await world.core.on_text(42, "hello")  # the chat became known
    resp = await call(world.sock, {"v": 1, "op": "message", "agent_id": "a1", "text": "reply"})
    assert resp["ok"] is True
    assert world.provider.texts == [(42, "reply", "claude-1")]


async def test_thinking_after_chat_known(world: SimpleNamespace) -> None:
    await call(world.sock, register_req("a1"))
    await world.core.on_text(42, "hello")
    await call(world.sock, {"v": 1, "op": "status", "agent_id": "a1", "status": "thinking"})
    assert world.provider.actions == [42]


async def test_many_agents_require_selection(world: SimpleNamespace) -> None:
    await call(world.sock, register_req("a1", name="one"))
    await call(world.sock, register_req("a2", name="two"))
    await world.core.on_text(42, "hello")
    assert len(world.provider.texts) == 1  # the hint instead of routing
    assert world.provider.texts[0][1] == world.core.select_hint
    world.core.state.set_active_agent(42, "a2")
    await world.core.on_text(42, "more")
    resp = await call(world.sock, {"v": 1, "op": "events", "agent_id": "a2", "cursor": 0})
    assert [e["text"] for e in resp["events"]] == ["more"]
    a1 = await call(world.sock, {"v": 1, "op": "events", "agent_id": "a1", "cursor": 0})
    assert a1["events"] == []


async def test_dead_selected_agent_prompts_hint(world: SimpleNamespace) -> None:
    await call(world.sock, register_req("a1"))
    await call(world.sock, register_req("a2"))
    world.core.state.set_active_agent(42, "a1")
    a1 = world.core.registry.get("a1")
    assert a1 is not None
    a1.status = protocol.STATUS_OFFLINE  # present but dead — make the user choose again
    await world.core.on_text(42, "hello")
    assert world.provider.texts[-1][1] == world.core.select_hint


async def test_stale_pin_falls_back_to_single_agent(world: SimpleNamespace) -> None:
    # a daemon restart wipes the registry: the pin points at a long-gone id,
    # the single fresh agent must be auto-selected, not greeted with a hint
    await call(world.sock, register_req("a1"))
    world.core.state.set_active_agent(42, "ghost-from-previous-run")
    await world.core.on_text(42, "hello")
    assert world.provider.texts == []  # no hint
    a1 = world.core.registry.get("a1")
    assert a1 is not None
    assert [e["text"] for _, e in a1.events_after(0)] == ["hello"]


async def test_stale_pin_falls_back_to_selection_hint_when_ambiguous(
    world: SimpleNamespace,
) -> None:
    await call(world.sock, register_req("a1"))
    await call(world.sock, register_req("a2"))
    world.core.state.set_active_agent(42, "ghost-from-previous-run")
    await world.core.on_text(42, "hello")
    assert world.provider.texts[-1][1] == world.core.select_hint


async def test_command_and_callback_dispatch(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    seen: list[tuple[str, int, str]] = []

    async def cmd(chat_id: int, rest: str) -> None:
        seen.append(("cmd", chat_id, rest))

    async def cb(chat_id: int, args: str) -> None:
        seen.append(("cb", chat_id, args))

    core.add_command("ping", cmd)
    core.add_callback("x:", cb)
    await core.on_command(7, "ping")
    await core.on_command(7, "ping", "extra args")
    await core.on_callback(7, "x:1:2")
    await core.on_command(7, "unknown")  # unknown — silently ignored
    assert seen == [("cmd", 7, ""), ("cmd", 7, "extra args"), ("cb", 7, "1:2")]


async def test_register_keeps_tmux_target(world: SimpleNamespace) -> None:
    await call(world.sock, {**register_req("a1"), "tmux_target": "%14"})
    resp = await call(world.sock, {**register_req("a2")})
    assert resp["ok"] is True
    a1, a2 = world.core.registry.get("a1"), world.core.registry.get("a2")
    assert a1 is not None and a2 is not None
    assert a1.tmux_target == "%14"
    assert a2.tmux_target is None


async def test_user_message_pastes_into_tmux_agent(tmp_path: Path) -> None:
    pasted: list[tuple[str, str]] = []

    async def deliver(target: str, text: str) -> None:
        pasted.append((target, text))

    core = make_core(tmp_path, deliver=deliver)
    core.registry.register(
        agent_id="a1", name="n", session_name="s", modes=[], tmux_target="%7"
    )
    await core.on_text(42, "hello")
    await asyncio.sleep(0)  # let the fire-and-forget delivery task run
    assert pasted == [("%7", "hello")]


async def test_user_message_skips_paste_without_tmux(tmp_path: Path) -> None:
    pasted: list[tuple[str, str]] = []

    async def deliver(target: str, text: str) -> None:
        pasted.append((target, text))

    core = make_core(tmp_path, deliver=deliver)
    core.registry.register(agent_id="a1", name="n", session_name="s", modes=[])
    await core.on_text(42, "hello")
    assert pasted == []


async def test_sweep_once_checks_tmux_panes_instead_of_silence(tmp_path: Path) -> None:
    pane_states = {"%1": True, "%2": False}

    async def pane_alive(target: str) -> bool:
        return pane_states[target]

    core = make_core(tmp_path, pane_alive=pane_alive)
    core.registry.register(
        agent_id="alive", name="n", session_name="s", modes=[], tmux_target="%1"
    )
    core.registry.register(
        agent_id="dead-pane", name="n", session_name="s", modes=[], tmux_target="%2"
    )
    await core.sweep_once()
    alive, dead = core.registry.get("alive"), core.registry.get("dead-pane")
    assert alive is not None and dead is not None
    assert alive.status == protocol.STATUS_IDLE  # silence is not death for tmux agents
    assert dead.status == protocol.STATUS_OFFLINE


async def test_sweep_once_spares_tmux_agents_from_grace(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    core.registry.register(
        agent_id="tmux-one", name="n", session_name="s", modes=[], tmux_target="%3"
    )
    async def always_alive(target: str) -> bool:
        return True

    core.pane_alive = always_alive
    tmux_agent = core.registry.get("tmux-one")
    assert tmux_agent is not None
    tmux_agent.last_seen -= 120.0  # long past the 30s grace
    await core.sweep_once()
    assert tmux_agent.status == protocol.STATUS_IDLE


async def test_typing_renews_while_thinking(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    core.registry.register(agent_id="a1", name="n", session_name="s", modes=[])
    await core.on_text(42, "hello")  # the chat becomes known
    agent = core.registry.get("a1")
    assert agent is not None
    assert agent.thinking_since is None
    await core.handle({"v": 1, "op": "status", "agent_id": "a1", "status": "thinking"})
    assert agent.thinking_since is not None
    assert core.provider.actions == [42]  # the immediate indicator
    await core.typing_once()
    assert core.provider.actions == [42, 42]
    agent.thinking_since -= 31 * 60  # past the 30-minute cap
    await core.typing_once()
    assert core.provider.actions == [42, 42]
    agent.thinking_since = time.monotonic()  # fresh again
    await core.typing_once()
    assert core.provider.actions == [42, 42, 42]
    await core.handle({"v": 1, "op": "status", "agent_id": "a1", "status": "idle"})
    assert agent.thinking_since is None
    await core.typing_once()
    assert core.provider.actions == [42, 42, 42]


async def test_typing_swallows_provider_errors(tmp_path: Path) -> None:
    class BrokenProvider(FakeProvider):
        async def chat_action(self, chat_id: int) -> None:
            raise RuntimeError("telegram down")

    provider = BrokenProvider()
    core = make_core(tmp_path, provider=provider)
    core.registry.register(agent_id="a1", name="n", session_name="s", modes=[])
    core.state.note_chat(42)
    await core.handle({"v": 1, "op": "status", "agent_id": "a1", "status": "thinking"})
    await core.typing_once()  # must not raise


async def test_service_agent_captured_not_broadcast(tmp_path: Path) -> None:
    provider = FakeProvider()
    core = make_core(tmp_path, provider=provider)
    core.registry.register(
        agent_id="svc", name="nlconv-1-proj-7", session_name="proj", modes=[], service=True
    )
    core.state.note_chat(42)
    await core.deliver_from_agent("svc", "/close work")
    assert provider.texts == []  # captured, not broadcast
    svc = core.registry.get("svc")
    assert svc is not None
    assert [e["text"] for _, e in svc.events_after(0)] == ["/close work"]


async def test_service_agents_excluded_from_selection(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    core.registry.register(
        agent_id="svc", name="nlconv-1-proj-7", session_name="proj", modes=[], service=True
    )
    core.registry.register(agent_id="a1", name="n", session_name="proj", modes=[])
    await core.on_text(42, "hello")  # the service agent must not confuse routing
    a1 = core.registry.get("a1")
    assert a1 is not None
    assert [e["text"] for _, e in a1.events_after(0)] == ["hello"]


async def test_allowed_ids_gate() -> None:
    provider = FakeProvider()
    state = StateStore(Path("/nonexistent/state.json"))
    state.load()
    core = Core(
        config=Config(token="t", proxy_url=None, allowed_ids=frozenset({10})),
        registry=Registry(),
        state=state,
        provider=provider,
    )
    assert core.is_allowed(10) is True
    assert core.is_allowed(11) is False
