"""Feature tests for /cron: cron math, the job store, and the command flows."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from channelsrv import protocol
from channelsrv.config import Config
from channelsrv.features.cron import (
    CONVERT_PROMPT,
    ORPHAN_RE,
    CronJob,
    JobStore,
    cron_matches,
    next_fire,
    parse_cron,
)
from channelsrv.features.cron import Feature as CronFeature
from channelsrv.registry import Registry
from channelsrv.server import Core
from channelsrv.state import StateStore
from tests.fakes import FakeProvider

FIXED_NOW = datetime(2026, 11, 10, 9, 0).astimezone()  # a Tuesday, 09:00 local


# ------------------------------------------------------------------ cron math


async def test_parse_cron_accepts_and_rejects() -> None:
    assert parse_cron("0 9 * * *") is not None
    assert parse_cron("*/15 9-17 * * 1-5") is not None
    assert parse_cron("1,15,30 0 1,15 * 0") is not None
    assert parse_cron("* * * *") is None  # four fields
    assert parse_cron("* * * * * *") is None  # six fields
    assert parse_cron("60 * * * *") is None  # minute out of range
    assert parse_cron("* 24 * * *") is None  # hour out of range
    assert parse_cron("*/0 * * * *") is None  # zero step
    assert parse_cron("a * * * *") is None  # not numeric
    assert parse_cron("MON * * * *") is None  # names are not allowed
    assert parse_cron("9-3 * * * *") is None  # inverted range
    assert parse_cron("1,,3 * * * *") is None  # empty list item


async def test_cron_matches_minute_and_dow() -> None:
    parsed = parse_cron("0 9 * * *")
    assert parsed is not None
    assert cron_matches(parsed, datetime(2026, 11, 10, 9, 0))
    assert not cron_matches(parsed, datetime(2026, 11, 10, 9, 1))
    assert not cron_matches(parsed, datetime(2026, 11, 10, 10, 0))

    sunday7 = parse_cron("0 12 * * 7")
    sunday0 = parse_cron("0 12 * * 0")
    assert sunday7 is not None and sunday0 is not None
    assert cron_matches(sunday7, datetime(2026, 11, 15, 12, 0))  # a Sunday
    assert cron_matches(sunday0, datetime(2026, 11, 15, 12, 0))
    assert not cron_matches(sunday7, datetime(2026, 11, 10, 12, 0))  # the Tuesday


async def test_cron_matches_dom_dow_or_rule() -> None:
    both = parse_cron("0 12 13 * 5")  # 13th OR Friday
    assert both is not None
    assert cron_matches(both, datetime(2026, 11, 6, 12, 0))  # Friday, not the 13th
    assert cron_matches(both, datetime(2026, 12, 13, 12, 0))  # a Sunday the 13th
    assert not cron_matches(both, datetime(2026, 11, 10, 12, 0))  # Tuesday the 10th

    dom_only = parse_cron("0 12 13 * *")
    assert dom_only is not None
    assert cron_matches(dom_only, datetime(2026, 12, 13, 12, 0))
    assert not cron_matches(dom_only, datetime(2026, 11, 6, 12, 0))


async def test_next_fire_scans_forward() -> None:
    parsed = parse_cron("0 9 * * *")
    assert parsed is not None
    assert next_fire(parsed, FIXED_NOW) == FIXED_NOW + timedelta(days=1)

    step = parse_cron("*/15 * * * *")
    assert step is not None
    assert next_fire(step, FIXED_NOW) == FIXED_NOW + timedelta(minutes=15)

    monday = parse_cron("0 9 * * 1")
    assert monday is not None
    assert next_fire(monday, FIXED_NOW) == FIXED_NOW + timedelta(days=6)


# ------------------------------------------------------------------- the store


def _job(name: str, suffix: str, agent: str = "", at: str = "") -> CronJob:
    return CronJob(
        name=name,
        suffix=suffix,
        prompt="check the build",
        cwd="/tmp",
        chat_id=42,
        created_at="2026-11-10T09:00:00",
        cron="" if at else "0 9 * * *",
        agent=agent,
        at=at,
    )


async def test_store_roundtrip_and_garbage(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "cron.json")
    store.load()  # missing file -> empty
    assert store.jobs() == []

    job = _job("daily-report", "a1b2c3")
    store.add(job)
    store.set_last_run(job.id, {"at": "2026-11-10T09:00:00", "status": "ok", "error": None})

    again = JobStore(tmp_path / "cron.json")
    again.load()
    loaded = again.jobs()
    assert len(loaded) == 1 and loaded[0].id == job.id
    assert loaded[0].last_run is not None and loaded[0].last_run["status"] == "ok"
    assert again.remove(job.id)
    assert not again.remove(job.id)

    (tmp_path / "cron.json").write_text("{broken json")
    broken = JobStore(tmp_path / "cron.json")
    broken.load()
    assert broken.jobs() == []


# ------------------------------------------------------- feature test plumbing


async def make_feature(
    tmp_path: Path,
    *,
    convert: Callable[[int, str, str], Coroutine[Any, Any, None]] | None = None,
    new_session: Callable[[str, str, str], Coroutine[Any, Any, None]] | None = None,
    close_session: Callable[[str], Coroutine[Any, Any, None]] | None = None,
    list_sessions: Callable[[], Coroutine[Any, Any, list[str]]] | None = None,
    session_exists: Callable[[str], Coroutine[Any, Any, bool]] | None = None,
    deliver: Callable[[str, str], Coroutine[Any, Any, None]] | None = None,
    run_timeout: float = 60.0,
) -> tuple[Core, FakeProvider, CronFeature, JobStore]:
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
    store = JobStore(tmp_path / "cron.json")
    feature = CronFeature(
        convert=convert,
        new_session=new_session,
        close_session=close_session,
        list_sessions=list_sessions,
        session_exists=session_exists,
        store=store,
        now=lambda: FIXED_NOW,
        run_timeout=run_timeout,
    )
    feature.register(core)
    return core, provider, feature, store


async def _drain(feature: CronFeature) -> None:
    """Lets started run tasks finish (their waits resolve quickly in tests)."""
    while feature._runs:
        await asyncio.wait(list(feature._runs.values()), timeout=1)


# ------------------------------------------------------------------ /cron add


async def test_cron_usage_and_unknown_verb(tmp_path: Path) -> None:
    core, provider, _feature, _store = await make_feature(tmp_path)
    await core.on_command(42, "cron", "")
    assert "Usage:" in provider.texts[-1][1]
    await core.on_command(42, "cron", "frobnicate x")
    assert "Usage:" in provider.texts[-1][1]


async def test_cron_add_calls_the_converter(tmp_path: Path) -> None:
    calls: list[tuple[int, str, str]] = []

    async def convert(chat_id: int, prompt: str, text: str) -> None:
        calls.append((chat_id, prompt, text))

    core, provider, _feature, _store = await make_feature(tmp_path, convert=convert)
    await core.on_command(42, "cron", "add every morning at 9 check the build")
    assert len(calls) == 1 and calls[0][0] == 42 and calls[0][2] == "every morning at 9 check the build"
    assert calls[0][1].startswith(CONVERT_PROMPT)
    assert "Current local time" in calls[0][1]  # so relative one-shots can resolve

    await core.on_command(42, "cron", "add")
    assert "Usage:" in provider.texts[-1][1]
    assert len(calls) == 1  # the empty add never reached the converter


async def test_cron_add_without_converter(tmp_path: Path) -> None:
    core, provider, _feature, _store = await make_feature(tmp_path)
    await core.on_command(42, "cron", "add every morning at 9 check the build")
    assert "unavailable" in provider.texts[-1][1]


# ------------------------------------------------------------------- /cron_put


async def test_cron_put_rejects_garbage(tmp_path: Path) -> None:
    core, provider, _feature, store = await make_feature(tmp_path)
    rejects = [
        "not json at all",
        json.dumps(["a", "list"]),
        json.dumps({"name": "Bad_Name", "cron": "0 9 * * *", "prompt": "x"}),
        json.dumps({"name": "ok-name", "cron": "* * * *", "prompt": "x"}),
        json.dumps({"name": "ok-name", "cron": "0 9 * * *"}),
        json.dumps({"name": "ok-name", "cron": "0 9 * * *", "prompt": "x", "cwd": "relative/path"}),
        json.dumps({"name": "ok-name", "cron": "0 9 * * *", "prompt": "x", "cwd": 42}),
        json.dumps({"name": "ok-name", "cron": "0 9 * * *", "prompt": "x", "agent": 42}),
        json.dumps({"name": "ok-name", "cron": "0 9 * * *", "prompt": "x", "agent": "a\nb"}),
        json.dumps({"name": "ok-name", "cron": "0 9 * * *", "at": "2026-11-10T09:05", "prompt": "x"}),
        json.dumps({"name": "ok-name", "prompt": "x"}),  # neither schedule key
        json.dumps({"name": "ok-name", "at": "2026-11-01T09:00", "prompt": "x"}),  # in the past
        json.dumps({"name": "ok-name", "at": "not-a-date", "prompt": "x"}),
    ]
    for payload in rejects:
        await core.on_command(42, "cron_put", payload)
        assert "Job rejected" in provider.texts[-1][1], payload
    assert store.jobs() == []


async def test_cron_put_creates_and_persists(tmp_path: Path) -> None:
    core, provider, _feature, store = await make_feature(tmp_path)
    payload = json.dumps(
        {
            "name": "daily-report",
            "cron": "0 9 * * *",
            "prompt": "  check the build  ",
            "cwd": str(tmp_path),
            "agent": "zaiclaude",
        }
    )
    await core.on_command(42, "cron_put", payload)
    text = provider.texts[-1][1]
    job = store.jobs()[0]
    assert "Created" in text and job.id in text and "0 9 * * *" in text
    assert "via `zaiclaude`" in text
    assert job.prompt == "check the build"
    assert job.agent == "zaiclaude"
    assert job.chat_id == 42
    assert "next Wed 11 09:00" in text

    reloaded = JobStore(tmp_path / "cron.json")
    reloaded.load()
    assert [j.id for j in reloaded.jobs()] == [job.id]
    assert reloaded.jobs()[0].agent == "zaiclaude"


async def test_cron_put_default_cwd_is_home(tmp_path: Path) -> None:
    core, _provider, _feature, store = await make_feature(tmp_path)
    payload = json.dumps({"name": "home-job", "cron": "0 9 * * *", "prompt": "x"})
    await core.on_command(42, "cron_put", payload)
    assert store.jobs()[0].cwd == str(Path.home())
    assert store.jobs()[0].agent == ""  # no alias named -> the default stays implicit


async def test_cron_put_default_cwd_from_active_agent(tmp_path: Path) -> None:
    core, _provider, _feature, store = await make_feature(tmp_path)
    core.registry.register(
        agent_id="a1", name="claude-proj-1", session_name="/tmp/some-project", modes=[]
    )
    core.state.set_active_agent(42, "a1")
    payload = json.dumps({"name": "proj-job", "cron": "0 9 * * *", "prompt": "x"})
    await core.on_command(42, "cron_put", payload)
    assert store.jobs()[0].cwd == "/tmp/some-project"


# ---------------------------------------------------------------- list/remove


async def test_cron_list_empty_and_populated(tmp_path: Path) -> None:
    core, provider, _feature, store = await make_feature(tmp_path)
    await core.on_command(42, "cron", "list")
    assert "No jobs" in provider.texts[-1][1]

    store.add(_job("daily-report", "a1b2c3"))
    store.add(_job("aliased", "b2c3d4", agent="zaiclaude"))
    await core.on_command(42, "cron", "list")
    text = provider.texts[-1][1]
    assert "daily-report-a1b2c3" in text and "0 9 * * *" in text
    assert "via `zaiclaude`" in text  # only jobs with their own alias show it
    assert "next Wed 11 09:00" in text and "check the build" in text


async def test_cron_remove_prefix_and_ambiguity(tmp_path: Path) -> None:
    core, provider, _feature, store = await make_feature(tmp_path)
    await core.on_command(42, "cron", "remove ghost")
    assert "No job matches" in provider.texts[-1][1]

    store.add(_job("report-alpha", "111111"))
    store.add(_job("report-beta", "222222"))
    await core.on_command(42, "cron", "remove report")
    assert "Ambiguous" in provider.texts[-1][1]

    await core.on_command(42, "cron", "remove report-alpha-111111")
    assert "Removed" in provider.texts[-1][1]
    assert [j.id for j in store.jobs()] == ["report-beta-222222"]

    await core.on_command(42, "cron", "remove report-beta-2")  # unique prefix
    assert store.jobs() == []


async def test_cron_remove_cancels_a_running_job(tmp_path: Path) -> None:
    core, provider, feature, store = await make_feature(tmp_path)
    job = _job("daily-report", "a1b2c3")
    store.add(job)
    task = asyncio.create_task(asyncio.sleep(10))
    feature._runs[job.id] = task
    await core.on_command(42, "cron", "remove daily-report-a1")
    assert "run cancelled" in provider.texts[-1][1]
    assert feature._runs == {}
    with contextlib.suppress(asyncio.CancelledError):
        await task


# ----------------------------------------------------------------- manual run


async def test_cron_run_manual(tmp_path: Path, monkeypatch: Any) -> None:
    holder: dict[str, Any] = {}
    spawned: list[tuple[str, str]] = []  # (session, agent command)
    closed: list[str] = []
    pasted: list[tuple[str, str]] = []

    async def fake_new(name: str, command: str, cwd: str) -> None:
        spawned.append((name, command))
        holder["core"].registry.register(
            agent_id=f"ag-{name}",
            name=f"{name}-proj-1",
            session_name=cwd,
            modes=[],
            tmux_target="%7",
        )

    async def fake_close(name: str) -> None:
        closed.append(name)

    async def fake_deliver(target: str, text: str) -> None:
        pasted.append((target, text))
        for agent in holder["core"].registry.snapshot():
            if agent.tmux_target == target:
                agent.push({"type": protocol.EVENT_MESSAGE, "text": "build is green"})

    core, provider, feature, store = await make_feature(
        tmp_path, new_session=fake_new, close_session=fake_close, deliver=fake_deliver
    )
    holder["core"] = core
    monkeypatch.setenv("CHANNEL_AI_AGENT", "cfgcli")
    alias_job = _job("daily-report", "a1b2c3", agent="zaiclaude")
    plain_job = _job("plain-job", "d4e5f6")
    store.add(alias_job)
    store.add(plain_job)

    await core.on_command(1, "cron", "run daily-report-a1")
    assert "Started" in provider.texts[-1][1]
    await _drain(feature)
    assert spawned == [("cron_a1b2c3-1", "zaiclaude")]  # the job's own alias
    assert closed == [s[0] for s in spawned]  # the throwaway session is killed
    assert pasted and pasted[0][1] == alias_job.prompt
    text = provider.texts[-1][1]
    assert text.startswith("[cron] daily-report") and "build is green" in text
    assert feature._runs == {}
    last_run = store.jobs()[0].last_run
    assert last_run is not None and last_run["status"] == "ok"

    await core.on_command(1, "cron", "run plain-job-d4")  # no alias -> the default
    await _drain(feature)
    assert spawned[-1][1] == "cfgcli"
    assert closed == [s[0] for s in spawned]


async def test_cron_run_busy_and_cap_refusals(tmp_path: Path) -> None:
    core, provider, feature, store = await make_feature(tmp_path)
    job = _job("daily-report", "a1b2c3")
    store.add(job)

    hanging = asyncio.create_task(asyncio.sleep(10))
    feature._runs[job.id] = hanging
    await core.on_command(1, "cron", "run daily-report-a1")
    assert "already running" in provider.texts[-1][1]

    feature._runs.clear()
    for i in range(3):
        feature._runs[f"other-{i}"] = asyncio.create_task(asyncio.sleep(10))
    await core.on_command(1, "cron", "run daily-report-a1")
    assert "cap" in provider.texts[-1][1]
    for t in feature._runs.values():
        t.cancel()
    hanging.cancel()


# ------------------------------------------------------------- scheduled fire


async def test_scheduled_fire_end_to_end(tmp_path: Path) -> None:
    holder: dict[str, Any] = {}
    spawned: list[str] = []
    closed: list[str] = []
    pasted: list[tuple[str, str]] = []

    async def fake_new(name: str, command: str, cwd: str) -> None:
        spawned.append(name)
        holder["core"].registry.register(
            agent_id=f"ag-{name}",
            name=f"{name}-proj-1",
            session_name=cwd,
            modes=[],
            tmux_target="%7",
        )

    async def fake_close(name: str) -> None:
        closed.append(name)

    async def fake_deliver(target: str, text: str) -> None:
        pasted.append((target, text))
        for agent in holder["core"].registry.snapshot():
            if agent.tmux_target == target:
                agent.push({"type": protocol.EVENT_MESSAGE, "text": "build is green"})

    core, provider, feature, store = await make_feature(
        tmp_path, new_session=fake_new, close_session=fake_close, deliver=fake_deliver
    )
    holder["core"] = core
    payload = json.dumps(
        {
            "name": "daily-report",
            "cron": "0 9 * * *",
            "prompt": "check the build",
            "cwd": str(tmp_path),
        }
    )
    await core.on_command(42, "cron_put", payload)

    await feature._tick(FIXED_NOW)  # 09:00 matches "0 9 * * *"
    await _drain(feature)
    assert len(spawned) == 1 and ORPHAN_RE.match(spawned[0])
    agent = holder["core"].registry.get(f"ag-{spawned[0]}")
    assert agent is not None and agent.service is True
    assert pasted[0][1] == "check the build"
    text = provider.texts[-1][1]
    assert text.startswith("[cron] daily-report") and "build is green" in text
    assert closed == spawned
    assert feature._runs == {}
    job = store.jobs()[0]
    assert job.last_run is not None and job.last_run["status"] == "ok"

    await feature._tick(FIXED_NOW.replace(minute=30))  # not the job's minute — quiet
    assert len(spawned) == 1


async def test_scheduled_fire_skips_busy_and_cap(tmp_path: Path) -> None:
    _core, provider, feature, store = await make_feature(tmp_path)
    job = _job("daily-report", "a1b2c3")
    store.add(job)

    hanging = asyncio.create_task(asyncio.sleep(10))
    feature._runs[job.id] = hanging
    await feature._tick(FIXED_NOW)
    assert "skipped" in provider.texts[-1][1] and "still going" in provider.texts[-1][1]

    feature._runs.clear()
    for i in range(3):
        feature._runs[f"other-{i}"] = asyncio.create_task(asyncio.sleep(10))
    await feature._tick(FIXED_NOW)
    assert "skipped" in provider.texts[-1][1] and "already active" in provider.texts[-1][1]
    for t in feature._runs.values():
        t.cancel()
    hanging.cancel()


async def test_run_session_death_fails_fast(tmp_path: Path) -> None:
    holder: dict[str, Any] = {}
    closed: list[str] = []

    async def fake_new(name: str, command: str, cwd: str) -> None:
        return None  # spawns but never registers (the broken-agent-command case)

    async def fake_close(name: str) -> None:
        closed.append(name)

    async def gone(name: str) -> bool:
        return False  # the pane died right away

    async def silent_deliver(target: str, text: str) -> None:
        return None

    core, provider, feature, store = await make_feature(
        tmp_path,
        new_session=fake_new,
        close_session=fake_close,
        session_exists=gone,
        deliver=silent_deliver,
        run_timeout=30.0,
    )
    holder["core"] = core
    store.add(_job("daily-report", "a1b2c3"))

    await feature._tick(FIXED_NOW)  # must not sit out the 90 s register wait
    await _drain(feature)
    text = provider.texts[-1][1]
    assert "died immediately" in text and "CHANNEL_AI_AGENT" in text
    assert closed  # the dead session was cleaned up
    last_run = store.jobs()[0].last_run
    assert last_run is not None and last_run["status"] == "failed"


async def test_scheduled_fire_timeout(tmp_path: Path) -> None:
    holder: dict[str, Any] = {}
    spawned: list[str] = []
    closed: list[str] = []

    async def fake_new(name: str, command: str, cwd: str) -> None:
        spawned.append(name)
        holder["core"].registry.register(
            agent_id=f"ag-{name}",
            name=f"{name}-proj-1",
            session_name=cwd,
            modes=[],
            tmux_target="%7",
        )

    async def fake_close(name: str) -> None:
        closed.append(name)

    async def silent_deliver(target: str, text: str) -> None:
        return None  # the agent never answers

    core, provider, feature, store = await make_feature(
        tmp_path,
        new_session=fake_new,
        close_session=fake_close,
        deliver=silent_deliver,
        run_timeout=0.3,
    )
    holder["core"] = core
    store.add(_job("daily-report", "a1b2c3"))

    await feature._tick(FIXED_NOW)
    await _drain(feature)
    text = provider.texts[-1][1]
    assert "timeout" in text and "no reply" in text
    assert closed == spawned
    last_run = store.jobs()[0].last_run
    assert last_run is not None and last_run["status"] == "timeout"


# --------------------------------------------------------------- orphan sweep


async def test_orphan_sweep_kills_only_scheduler_sessions(tmp_path: Path) -> None:
    closed: list[str] = []

    async def fake_close(name: str) -> None:
        closed.append(name)

    async def fake_sessions() -> list[str]:
        return ["cron_a1b2c3-1", "cron", "cron-mybackup", "cron_ffffff-2", "work"]

    _core, _provider, feature, _store = await make_feature(
        tmp_path, close_session=fake_close, list_sessions=fake_sessions
    )
    await feature._kill_orphans()
    assert closed == ["cron_a1b2c3-1", "cron_ffffff-2"]


# -------------------------------------------------------------------- one-shot


async def test_cron_put_one_shot(tmp_path: Path) -> None:
    core, provider, _feature, store = await make_feature(tmp_path)
    payload = json.dumps(
        {"name": "quick-check", "at": "2026-11-10T09:05", "prompt": "peek", "agent": "zaiclaude"}
    )
    await core.on_command(42, "cron_put", payload)
    text = provider.texts[-1][1]
    job = store.jobs()[0]
    assert "one-shot" in text and "fires at" in text
    assert job.cron == "" and job.at.startswith("2026-11-10T09:05")
    assert job.agent == "zaiclaude"

    await core.on_command(42, "cron", "list")
    assert "fires" in provider.texts[-1][1]


async def test_one_shot_fires_once_and_removes_itself(tmp_path: Path) -> None:
    holder: dict[str, Any] = {}
    spawned: list[str] = []
    closed: list[str] = []

    async def fake_new(name: str, command: str, cwd: str) -> None:
        spawned.append(name)
        holder["core"].registry.register(
            agent_id=f"ag-{name}",
            name=f"{name}-proj-1",
            session_name=cwd,
            modes=[],
            tmux_target="%7",
        )

    async def fake_close(name: str) -> None:
        closed.append(name)

    async def fake_deliver(target: str, text: str) -> None:
        for agent in holder["core"].registry.snapshot():
            if agent.tmux_target == target:
                agent.push({"type": protocol.EVENT_MESSAGE, "text": "build is green"})

    core, provider, feature, store = await make_feature(
        tmp_path, new_session=fake_new, close_session=fake_close, deliver=fake_deliver
    )
    holder["core"] = core
    store.add(_job("quick-check", "a1b2c3", at="2026-11-10T09:05"))

    await feature._tick(FIXED_NOW)  # not due yet — quiet
    assert spawned == []

    await feature._tick(FIXED_NOW + timedelta(minutes=10))  # due; late is fine
    await _drain(feature)
    assert len(spawned) == 1 and ORPHAN_RE.match(spawned[0])
    text = provider.texts[-1][1]
    assert text.startswith("[cron] quick-check") and "build is green" in text
    assert store.jobs() == []  # self-removed after its single attempt
    assert closed == spawned


async def test_one_shot_waits_for_a_free_slot(tmp_path: Path) -> None:
    holder: dict[str, Any] = {}
    spawned: list[str] = []
    closed: list[str] = []

    async def fake_new(name: str, command: str, cwd: str) -> None:
        spawned.append(name)
        holder["core"].registry.register(
            agent_id=f"ag-{name}",
            name=f"{name}-proj-1",
            session_name=cwd,
            modes=[],
            tmux_target="%7",
        )

    async def fake_close(name: str) -> None:
        closed.append(name)

    async def fake_deliver(target: str, text: str) -> None:
        for agent in holder["core"].registry.snapshot():
            if agent.tmux_target == target:
                agent.push({"type": protocol.EVENT_MESSAGE, "text": "done"})

    core, provider, feature, store = await make_feature(
        tmp_path, new_session=fake_new, close_session=fake_close, deliver=fake_deliver
    )
    holder["core"] = core
    store.add(_job("quick-check", "a1b2c3", at="2026-11-10T09:05"))

    for i in range(3):
        feature._runs[f"other-{i}"] = asyncio.create_task(asyncio.sleep(10))
    await feature._tick(FIXED_NOW + timedelta(minutes=10))  # due, but the cap is full
    assert "waiting for a free slot" in provider.texts[-1][1]
    assert store.jobs() != []  # stays due instead of dying at a bad moment

    for t in feature._runs.values():
        t.cancel()
    feature._runs.clear()

    await feature._tick(FIXED_NOW + timedelta(minutes=11))  # slot free — fires now
    await _drain(feature)
    assert len(spawned) == 1
    assert store.jobs() == []
    assert closed == spawned
