"""Progress: prose tail (text blocks -> messages) and the ⚙ line format."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from channelsrv import progress
from channelsrv.server import Core
from tests.fakes import FakeProvider
from tests.test_server import make_core


def assistant_entry(*blocks: dict[str, object]) -> dict[str, object]:
    return {"type": "assistant", "message": {"id": "m1", "content": list(blocks)}}


# --------------------------------------------------------------- pure helpers


class TestExtractTexts:
    def test_text_block_is_extracted(self) -> None:
        entry = assistant_entry({"type": "text", "text": "Понял задачу.\n"})
        assert progress.extract_texts(entry) == ["Понял задачу."]

    def test_thinking_tool_and_results_are_skipped(self) -> None:
        entry = assistant_entry(
            {"type": "thinking", "thinking": "internal"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
        )
        assert progress.extract_texts(entry) == []

    def test_non_assistant_entries_are_skipped(self) -> None:
        assert progress.extract_texts({"type": "user"}) == []
        assert progress.extract_texts({"type": "assistant", "message": {}}) == []
        assert progress.extract_texts({"type": "assistant", "message": None}) == []


class TestToolLine:
    def test_wraps_the_hook_provided_detail(self) -> None:
        assert progress.tool_line("Bash", "git status") == "*⚙ Bash(git status)*"

    def test_empty_detail_names_the_tool_only(self) -> None:
        assert progress.tool_line("Skill", "") == "*⚙ Skill*"


# ------------------------------------------------------------- daemon wiring


def write_entry(path: Path, entry: dict[str, object]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[tuple[Core, FakeProvider, Path]]:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("")
    provider = FakeProvider()
    core = make_core(tmp_path, provider)
    core.state.note_chat(42)
    await core.handle(
        {
            "v": 1,
            "op": "register",
            "agent_id": "a1",
            "name": "agent",
            "session_name": "s",
            "modes": [],
            "transcript_path": str(transcript),
        }
    )
    yield core, provider, transcript


async def think(core: Core, agent_id: str = "a1") -> None:
    await core.handle(
        {"v": 1, "op": "status", "agent_id": agent_id, "status": "thinking"}
    )


async def idle(core: Core, agent_id: str = "a1") -> None:
    await core.handle({"v": 1, "op": "status", "agent_id": agent_id, "status": "idle"})


async def test_turn_tail_opens_at_register(
    world: tuple[Core, FakeProvider, Path],
) -> None:
    core, _, transcript = world
    agent = core.registry.get("a1")
    assert agent is not None and agent.tail is not None
    assert agent.tail.path == str(transcript)
    assert agent.tail.offset == 0
    tail = agent.tail
    await think(core)  # a thinking op must not reset a live tail
    assert agent.tail is tail


async def test_prose_block_goes_out_as_its_own_message(
    world: tuple[Core, FakeProvider, Path],
) -> None:
    core, provider, transcript = world
    write_entry(transcript, assistant_entry({"type": "text", "text": "Понял задачу."}))
    await core.progress_once()
    assert provider.texts == [(42, "Понял задачу.", "agent")]


async def test_prose_drains_without_thinking_status(
    world: tuple[Core, FakeProvider, Path],
) -> None:
    """Self-heal: after a mid-turn daemon restart the agent re-registers as
    idle — the tail must still deliver the rest of the turn."""
    core, provider, transcript = world
    write_entry(transcript, assistant_entry({"type": "text", "text": "after restart"}))
    await core.progress_once()  # no thinking op was ever sent
    assert provider.texts == [(42, "after restart", "agent")]


async def test_each_block_is_a_separate_message(
    world: tuple[Core, FakeProvider, Path],
) -> None:
    core, provider, transcript = world
    write_entry(transcript, assistant_entry({"type": "text", "text": "first"}))
    write_entry(transcript, assistant_entry({"type": "text", "text": "second"}))
    await core.progress_once()
    assert [t[1] for t in provider.texts] == ["first", "second"]


async def test_idle_closes_the_turn_with_a_flush(
    world: tuple[Core, FakeProvider, Path],
) -> None:
    core, provider, transcript = world
    write_entry(transcript, assistant_entry({"type": "text", "text": "mid-turn note"}))
    await idle(core)
    assert [t[1] for t in provider.texts] == ["mid-turn note"]
    agent = core.registry.get("a1")
    assert agent is not None and agent.tail is None


# ------------------------------------- final replies are always fresh messages


async def test_final_already_streamed_is_still_sent_as_message(
    world: tuple[Core, FakeProvider, Path],
) -> None:
    """ADR-0015: folding the final into a silent edit lost replies
    (2026-11-15 incident); the final goes out as its own message even when
    the turn's stream carried it verbatim."""
    core, provider, transcript = world
    final = "Итог: всё готово."
    write_entry(transcript, assistant_entry({"type": "text", "text": final}))
    resp = await core.handle({"v": 1, "op": "message", "agent_id": "a1", "text": final})
    assert resp == {"ok": True}
    assert provider.texts == [(42, final, "agent")]  # the prose copy is skipped
    await idle(core)
    agent = core.registry.get("a1")
    assert agent is not None and agent.tail is None


async def test_unseen_final_is_delivered(
    world: tuple[Core, FakeProvider, Path],
) -> None:
    core, provider, _ = world
    resp = await core.handle(
        {
            "v": 1,
            "op": "message",
            "agent_id": "a1",
            "text": "Финал, которого нет в стриме",
        }
    )
    assert resp == {"ok": True}
    assert provider.texts == [(42, "Финал, которого нет в стриме", "agent")]


async def test_late_message_after_idle_is_delivered(
    world: tuple[Core, FakeProvider, Path],
) -> None:
    """The old race guard (turn-end before the message op -> skip) died with
    ADR-0015: a Stop reply is always worth its own message."""
    core, provider, transcript = world
    final = "Уже отстримлено."
    write_entry(transcript, assistant_entry({"type": "text", "text": final}))
    await idle(core)  # close_turn ran first: the prose copy already went out
    resp = await core.handle({"v": 1, "op": "message", "agent_id": "a1", "text": final})
    assert resp == {"ok": True}
    # the rare visible double (ADR-0015's accepted cost): prose, then final
    assert provider.texts == [(42, final, "agent"), (42, final, "agent")]


async def test_new_message_after_idle_is_delivered(
    world: tuple[Core, FakeProvider, Path],
) -> None:
    core, provider, transcript = world
    write_entry(transcript, assistant_entry({"type": "text", "text": "turn text"}))
    await idle(core)  # the turn's prose went out at close
    await core.handle(
        {"v": 1, "op": "message", "agent_id": "a1", "text": "свежий текст"}
    )
    assert provider.texts == [(42, "turn text", "agent"), (42, "свежий текст", "agent")]


# ------------------------------------------- the ⚙ op (PostToolUse hook events)


async def test_tool_op_sends_a_gear_line(
    world: tuple[Core, FakeProvider, Path],
) -> None:
    core, provider, _ = world
    resp = await core.handle(
        {"v": 1, "op": "tool", "agent_id": "a1", "name": "Bash", "detail": "git status"}
    )
    assert resp == {"ok": True}
    assert provider.texts == [(42, "*⚙ Bash(git status)*", "agent")]


# ------------------------------------------------- transcript rewrite (compaction)


async def test_shrunk_transcript_is_skipped_not_reread(tmp_path: Path) -> None:
    """Incident 2026-11-16: the harness rewrites/compacts the session file;
    the old `size < offset -> read from zero` re-streamed the whole history
    — the user saw a "ghost" duplicate of an old reply, prefixless. On
    shrink, skip to the new end: rewritten history is old by definition."""
    transcript = tmp_path / "t.jsonl"
    write_entry(
        transcript, assistant_entry({"type": "text", "text": "Нашёл. Диагноз точный…"})
    )
    tail = progress.TurnTail(path=str(transcript))
    assert progress.drain_texts(tail) == ["Нашёл. Диагноз точный…"]

    # the harness compacts: the file is rewritten smaller, with fresh content
    transcript.write_text(
        json.dumps(assistant_entry({"type": "text", "text": "новая жизнь"})) + "\n"
    )
    assert progress.drain_texts(tail) == []  # the rewritten past is not progress
    assert tail.offset == transcript.stat().st_size  # fast-forwarded to EOF
