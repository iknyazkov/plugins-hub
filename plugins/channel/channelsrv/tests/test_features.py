"""Feature tests: status, agent selection, approvals, modes, passthrough — on a fake provider."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from channelsrv import protocol
from channelsrv.config import Config
from channelsrv.features import register_all
from channelsrv.registry import Registry
from channelsrv.server import Core
from channelsrv.state import StateStore
from tests.fakes import FakeProvider


def make_core(
    tmp_path: Path,
    deliver: Callable[[str, str], Coroutine[Any, Any, None]] | None = None,
) -> tuple[Core, FakeProvider]:
    provider = FakeProvider()
    state = StateStore(tmp_path / "state.json")
    state.load()
    core = Core(
        config=Config(token="t", proxy_url=None, allowed_ids=None),
        registry=Registry(),
        state=state,
        provider=provider,
        deliver=deliver,
    )
    register_all(core)
    return core, provider


def reg_agent(core: Core, agent_id: str, name: str = "agent", modes: list[str] | None = None) -> None:
    core.registry.register(
        agent_id=agent_id, name=name, session_name="proj", modes=modes or []
    )


async def req(core: Core, op: str, **payload: Any) -> dict[str, Any]:
    return await core.handle({"v": 1, "op": op, **payload})


# --------------------------------------------------------------------- status


async def test_status_empty(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    await core.on_command(1, "status")
    assert "No agents" in provider.texts[0][1]


async def test_status_lists_agents(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    reg_agent(core, "a1", name="claude-1")
    agent = core.registry.get("a1")
    assert agent is not None
    agent.status = protocol.STATUS_THINKING
    agent.mode = "auto"
    agent.detail = "pondering"
    await core.on_command(1, "status")
    text = provider.texts[0][1]
    assert "claude-1" in text and "proj" in text and "thinking (pondering)" in text and "auto" in text


# ------------------------------------------------------------------ selection


async def test_selection_hint_and_card(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    assert "/agents" in core.select_hint
    reg_agent(core, "a1", name="one")
    reg_agent(core, "a2", name="two")
    await core.on_text(42, "hello")  # ambiguous -> the hint
    assert provider.texts[0][1] == core.select_hint
    await core.on_command(42, "agents")
    chat_id, _card_text, buttons = provider.cards[0]
    assert chat_id == 42
    assert [b.callback_data for row in buttons for b in row] == ["sel:a1", "sel:a2"]


async def test_selection_pick_persists_and_routes(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    reg_agent(core, "a1", name="one")
    reg_agent(core, "a2", name="two")
    await core.on_callback(42, "sel:a2")
    assert core.state.active_agent(42) == "a2"
    assert "two" in provider.texts[-1][1]
    await core.on_text(42, "your move")
    agent = core.registry.get("a2")
    assert agent is not None
    assert agent.events_after(0)[0][1]["text"] == "your move"


async def test_selection_pick_dead_agent(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    await core.on_callback(42, "sel:ghost")
    assert "gone" in provider.texts[-1][1]


# ------------------------------------------------------------------ approvals


async def test_approval_flow(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    reg_agent(core, "a1", name="claude-1")
    await core.on_text(42, "hello")  # the chat became known
    resp = await req(
        core,
        protocol.OP_APPROVAL_REQUEST,
        agent_id="a1",
        kind="bash",
        summary="rm -rf /tmp/x",
        details="$ rm -rf /tmp/x",
    )
    assert resp["ok"] is True
    approval_id = resp["approval_id"]
    _, card_text, buttons = provider.cards[0]
    assert "claude-1" in card_text and "rm -rf /tmp/x" in card_text
    flat = [b.callback_data for row in buttons for b in row]
    assert flat == [f"ap:{approval_id}:y", f"ap:{approval_id}:n"]

    await core.on_callback(42, f"ap:{approval_id}:y")
    agent = core.registry.get("a1")
    assert agent is not None
    events = agent.events_after(0)
    assert events[-1][1]["type"] == protocol.EVENT_APPROVAL_RESULT
    assert events[-1][1]["approved"] is True
    chat_id, message_id, edit_text = provider.edits[0]
    assert chat_id == 42 and message_id == 1001  # the card is edited, not duplicated
    assert edit_text.startswith("✅ Approved")


async def test_approval_agent_died(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    reg_agent(core, "a1")
    await core.on_text(42, "hello")
    resp = await req(core, protocol.OP_APPROVAL_REQUEST, agent_id="a1", kind="k", summary="s")
    approval_id = resp["approval_id"]
    core.registry.deregister("a1")
    await core.on_callback(42, f"ap:{approval_id}:n")
    assert "Expired" in provider.edits[0][2]


async def test_approval_double_press_ignored(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    reg_agent(core, "a1")
    await core.on_text(42, "hello")
    resp = await req(core, protocol.OP_APPROVAL_REQUEST, agent_id="a1", kind="k", summary="s")
    approval_id = resp["approval_id"]
    await core.on_callback(42, f"ap:{approval_id}:y")
    await core.on_callback(42, f"ap:{approval_id}:y")  # a repeat — no crash, no duplicate
    assert len(provider.edits) == 1


async def test_approval_without_chats_fails(tmp_path: Path) -> None:
    core, _provider = make_core(tmp_path)
    reg_agent(core, "a1")
    resp = await req(core, protocol.OP_APPROVAL_REQUEST, agent_id="a1", kind="k", summary="s")
    assert resp["ok"] is False
    assert "chat" in resp["error"]


# ---------------------------------------------------------------------- modes


async def test_modes_card_and_set(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    reg_agent(core, "a1", name="claude-1", modes=["auto", "plan"])
    core.state.set_active_agent(42, "a1")
    await core.on_command(42, "mode")
    _, card_text, buttons = provider.cards[0]
    assert "claude-1" in card_text
    assert [b.callback_data for row in buttons for b in row] == ["mode:auto", "mode:plan"]
    await core.on_callback(42, "mode:plan")
    agent = core.registry.get("a1")
    assert agent is not None
    event = agent.events_after(0)[0][1]
    assert event["type"] == protocol.EVENT_MODE_SET and event["mode"] == "plan"


async def test_modes_without_modes(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    reg_agent(core, "a1", modes=[])
    core.state.set_active_agent(42, "a1")
    await core.on_command(42, "mode")
    assert "no modes" in provider.texts[0][1]


# ---------------------------------------------------------------- passthrough


async def test_passthrough_pastes_rest_into_pane(tmp_path: Path) -> None:
    pasted: list[tuple[str, str]] = []

    async def deliver(target: str, text: str) -> None:
        pasted.append((target, text))

    core, provider = make_core(tmp_path, deliver=deliver)
    reg_agent(core, "a1", name="one")
    agent = core.registry.get("a1")
    assert agent is not None
    agent.tmux_target = "%9"
    await core.on_command(42, "command", "/reload-plugins --force")
    assert pasted == [("%9", "/reload-plugins --force")]
    assert provider.texts == []  # no chatter, just the paste


async def test_passthrough_usage_and_dead_ends(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    reg_agent(core, "a1")  # no tmux_target — nothing to paste into
    await core.on_command(1, "command", "   ")
    assert "Usage:" in provider.texts[-1][1]
    await core.on_command(1, "command", "ls -la")
    assert "no pane" in provider.texts[-1][1]

    reg_agent(core, "a2")  # two live agents, no selection — ambiguous
    await core.on_command(42, "command", "x")
    assert provider.texts[-1][1] == core.select_hint


# ------------------------------------------------------------------- sessions


async def test_sessions_new_and_close(tmp_path: Path) -> None:
    spawned: list[tuple[str, str, str]] = []
    closed: list[str] = []

    async def fake_new(name: str, command: str, cwd: str) -> None:
        spawned.append((name, command, cwd))

    async def fake_close(name: str) -> None:
        closed.append(name)

    core, provider = make_core(tmp_path)
    from channelsrv.features.sessions import Feature as SessionsFeature

    SessionsFeature(new_session=fake_new, close_session=fake_close).register(core)
    reg_agent(core, "a1")

    await core.on_command(42, "new", f"claude work {tmp_path}")
    assert spawned == [("work", "claude", str(tmp_path))]
    assert "Spawned" in provider.texts[-1][1]

    await core.on_command(42, "new", "claude work")  # missing path
    assert "Usage:" in provider.texts[-1][1] and "Example:" in provider.texts[-1][1]
    await core.on_command(42, "new", "")  # bare
    assert "Usage:" in provider.texts[-1][1]
    assert len(spawned) == 1

    await core.on_command(42, "new", "claude work relative/path")
    assert "absolute" in provider.texts[-1][1]
    await core.on_command(42, "new", "claude work /nonexistent/dir/xyz")
    assert "No such directory" in provider.texts[-1][1]
    assert len(spawned) == 1

    async def taken(name: str, command: str, cwd: str) -> None:
        raise ValueError(f"tmux session '{name}' already exists")

    core2, provider2 = make_core(tmp_path)
    SessionsFeature(new_session=taken, close_session=fake_close).register(core2)
    reg_agent(core2, "a2")
    await core2.on_command(42, "new", f"claude work {tmp_path}")
    assert "already exists" in provider2.texts[-1][1]

    await core.on_command(42, "close", "work")
    assert closed == ["work"]
    assert "Closed" in provider.texts[-1][1]

    async def missing(name: str) -> None:
        raise ValueError(f"no such tmux session '{name}'")

    core3, provider3 = make_core(tmp_path)
    SessionsFeature(new_session=fake_new, close_session=missing).register(core3)
    reg_agent(core3, "a3")
    await core3.on_command(42, "close", "ghost")
    assert "no such tmux session" in provider3.texts[-1][1]

    await core3.on_command(42, "close", "")
    assert "Usage:" in provider3.texts[-1][1] and "Example:" in provider3.texts[-1][1]


# -------------------------------------------------------------------- context


async def test_context_reports_last_turn_usage(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    reg_agent(core, "a1")
    await core.on_text(42, "hello")  # the chat becomes known

    await core.on_command(42, "context", "")
    assert "No context data" in provider.texts[-1][1]

    resp = await core.handle(
        {"v": 1, "op": "context", "agent_id": "a1", "input": 148234, "output": 2468}
    )
    assert resp["ok"] is True
    await core.on_command(42, "context", "")
    assert provider.texts[-1][1] == "ctx 148.2k in · 2.5k out (last turn)"

    bad = await core.handle(
        {"v": 1, "op": "context", "agent_id": "a1", "input": "lots", "output": 1}
    )
    assert bad["ok"] is False


async def test_context_formatting_edges(tmp_path: Path) -> None:
    from channelsrv.features.context_cmd import _fmt_tokens

    assert _fmt_tokens(999) == "999"
    assert _fmt_tokens(1000) == "1k"
    assert _fmt_tokens(148234) == "148.2k"
    assert _fmt_tokens(250000) == "250k"


# ------------------------------------------------------------------- nl / ai


async def test_ai_converts_and_executes(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("CHANNEL_AI_TIMEOUT", "5")
    spawned: list[tuple[str, str, str]] = []
    closed: list[str] = []
    pasted: list[tuple[str, str]] = []

    async def fake_close(name: str) -> None:
        closed.append(name)

    async def deliver(target: str, text: str) -> None:
        pasted.append((target, text))

    core, provider = make_core(tmp_path, deliver=deliver)

    async def fake_new(name: str, command: str, cwd: str) -> None:
        spawned.append((name, command, cwd))
        # the throwaway session's hook registers it the moment claude starts
        core.registry.register(
            agent_id="conv", name=f"{name}-proj-7", session_name="proj", modes=[], tmux_target="%5"
        )

    from channelsrv.features.nlcmd import Feature

    feature = Feature(
        commands={"ai": "convert the request into one daemon command line"}, new_session=fake_new, close_session=fake_close
    )
    feature.register(core)

    assert core.registry.get("conv") is None  # not registered until the spawn happens

    async def reply_soon() -> None:
        await asyncio.sleep(0.3)
        conv = core.registry.get("conv")
        assert conv is not None
        conv.push({"type": "message", "text": "/status"})

    task = asyncio.create_task(reply_soon())
    await core.on_command(42, "ai", "show me the agents")
    await task

    assert spawned == [("nlconv-1", "claude", str(Path.home()))]  # no active agent → HOME
    converter = core.registry.get("conv")
    assert converter is not None and converter.service is True
    assert pasted and pasted[-1][0] == "%5" and "show me the agents" in pasted[-1][1]
    assert closed == ["nlconv-1"]  # the throwaway session is killed
    assert "Agents:" in provider.texts[-1][1]  # the converted /status executed
    assert feature._busy is False


async def test_ai_passes_through_non_command_reply(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("CHANNEL_AI_TIMEOUT", "5")
    closed: list[str] = []

    async def fake_new(name: str, command: str, cwd: str) -> None:
        pass

    async def fake_close(name: str) -> None:
        closed.append(name)

    async def deliver(target: str, text: str) -> None:
        pass

    core, provider = make_core(tmp_path, deliver=deliver)
    from channelsrv.features.nlcmd import Feature

    Feature(
        commands={"ai": "convert the request into one daemon command line"}, new_session=fake_new, close_session=fake_close
    ).register(core)
    converter = core.registry.register(
        agent_id="conv", name="nlconv-1-proj-7", session_name="proj", modes=[], tmux_target="%5"
    )

    async def reply_soon() -> None:
        await asyncio.sleep(0.2)
        converter.push({"type": "message", "text": "Which repo do you mean?"})

    task = asyncio.create_task(reply_soon())
    await core.on_command(42, "ai", "close it")
    await task

    assert provider.texts[-1][1] == "Which repo do you mean?"
    assert closed == ["nlconv-1"]


async def test_ai_busy_and_usage(tmp_path: Path) -> None:
    core, provider = make_core(tmp_path)
    from channelsrv.features.nlcmd import Feature

    feature = Feature(commands={"ai": "convert"})
    feature.register(core)
    assert "ai" in core._commands
    feature._busy = True
    await core.on_command(42, "ai", "anything")
    assert "Busy" in provider.texts[-1][1]
    feature._busy = False
    await core.on_command(42, "ai", "   ")
    assert "Usage:" in provider.texts[-1][1]


async def test_ai_converter_death_fails_fast(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("CHANNEL_AI_TIMEOUT", "5")
    closed: list[str] = []

    async def fake_new(name: str, command: str, cwd: str) -> None:
        pass  # spawns but never registers (the broken-agent-command case)

    async def fake_close(name: str) -> None:
        closed.append(name)

    async def gone(name: str) -> bool:
        return False  # the pane died right away

    async def deliver(target: str, text: str) -> None:
        pass

    core, provider = make_core(tmp_path, deliver=deliver)
    from channelsrv.features.nlcmd import Feature

    Feature(
        commands={"ai": "convert"},
        new_session=fake_new,
        close_session=fake_close,
        session_exists=gone,
    ).register(core)

    await core.on_command(42, "ai", "anything")  # must not sit out the 90 s wait
    assert "died immediately" in provider.texts[-1][1]
    assert "CHANNEL_AI_AGENT" in provider.texts[-1][1]
    assert closed == ["nlconv-1"]


async def test_features_survive_partial_registration(tmp_path: Path) -> None:
    """Removing a feature does not break the others: only status is registered."""
    provider = FakeProvider()
    state = StateStore(tmp_path / "s2.json")
    state.load()
    core = Core(
        config=Config(token="t", proxy_url=None, allowed_ids=None),
        registry=Registry(),
        state=state,
        provider=provider,
    )
    from channelsrv.features.status import Feature as StatusFeature

    StatusFeature().register(core)
    reg_agent(core, "a1")
    await core.on_command(1, "status")
    assert "a1" in provider.texts[0][1] or "agent" in provider.texts[0][1]
    await core.on_callback(1, "mode:auto")  # no modes feature — simply ignored
    await core.on_text(1, "hello")  # routing is alive
