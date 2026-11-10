"""Agent registry tests: registration, cursor events, liveness."""

from channelsrv import protocol
from channelsrv.registry import Agent, Registry


def make_registry(now: list[float]) -> Registry:
    return Registry(clock=lambda: now[0])


def test_register_and_get() -> None:
    reg = make_registry([100.0])
    agent = reg.register(agent_id="a1", name="claude-1", session_name="proj", modes=["auto", "plan"])
    assert agent.status == protocol.STATUS_IDLE
    assert reg.get("a1") is agent
    assert reg.get("missing") is None
    assert [a.id for a in reg.snapshot()] == ["a1"]


def test_reregister_replaces() -> None:
    reg = make_registry([100.0])
    reg.register(agent_id="a1", name="old", session_name="s", modes=[])
    first = reg.get("a1")
    assert first is not None
    first.push({"type": "user_message"})
    agent = reg.register(agent_id="a1", name="new", session_name="s", modes=[])
    assert agent.name == "new"
    assert agent.events_after(0) == []  # history from the previous life is not carried over


def test_deregister() -> None:
    reg = make_registry([100.0])
    reg.register(agent_id="a1", name="n", session_name="s", modes=[])
    assert reg.deregister("a1") is True
    assert reg.get("a1") is None
    assert reg.deregister("a1") is False


def test_push_cursors_and_backlog() -> None:
    agent = Agent(id="a", name="n", session_name="s", modes=())
    assert agent.push({"n": 1}) == 1
    assert agent.push({"n": 2}) == 2
    assert agent.last_cursor == 2
    assert [e for _, e in agent.events_after(0)] == [{"n": 1}, {"n": 2}]
    assert [e for _, e in agent.events_after(1)] == [{"n": 2}]
    assert agent.events_after(2) == []


def test_push_wakes_long_poll() -> None:
    agent = Agent(id="a", name="n", session_name="s", modes=())
    assert agent.wakeup.is_set() is False
    agent.push({"n": 1})
    assert agent.wakeup.is_set() is True


def test_push_caps_backlog() -> None:
    agent = Agent(id="a", name="n", session_name="s", modes=())
    for i in range(300):
        agent.push({"n": i})
    assert len(agent.events) == 256
    assert agent.events_after(0)[0][1] == {"n": 44}  # the oldest was pushed out


def test_sweep_offline_marks_stale_only_once() -> None:
    now = [100.0]
    reg = make_registry(now)
    reg.register(agent_id="a1", name="n", session_name="s", modes=[])
    reg.register(agent_id="a2", name="m", session_name="s", modes=[])
    now[0] = 100.0 + 31.0
    reg.touch("a2")
    assert reg.sweep_offline() == ["a1"]
    stale, fresh = reg.get("a1"), reg.get("a2")
    assert stale is not None and fresh is not None
    assert stale.status == protocol.STATUS_OFFLINE
    assert fresh.status == protocol.STATUS_IDLE
    now[0] += 10.0
    assert reg.sweep_offline() == []  # already offline — do not re-flag


def test_touch_updates_last_seen() -> None:
    now = [100.0]
    reg = make_registry(now)
    reg.register(agent_id="a1", name="n", session_name="s", modes=[])
    now[0] = 140.0
    reg.touch("a1")
    assert reg.sweep_offline() == []
