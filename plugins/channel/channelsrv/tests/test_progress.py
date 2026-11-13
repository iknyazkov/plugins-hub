"""Progress chain: chunk extraction, segment growth, daemon tail wiring."""

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


class TestExtractChunks:
    def test_text_block_becomes_chunk(self) -> None:
        entry = assistant_entry({"type": "text", "text": "Понял задачу.\n"})
        assert progress.extract_chunks(entry) == ["Понял задачу."]

    def test_thinking_blocks_are_skipped(self) -> None:
        entry = assistant_entry({"type": "thinking", "thinking": "internal"})
        assert progress.extract_chunks(entry) == []

    def test_non_assistant_entries_are_skipped(self) -> None:
        assert progress.extract_chunks({"type": "user"}) == []
        assert progress.extract_chunks({"type": "assistant", "message": {}}) == []

    def test_tool_use_becomes_a_line(self) -> None:
        entry = assistant_entry(
            {"type": "tool_use", "name": "Agent", "input": {"description": "Research limits"}}
        )
        assert progress.extract_chunks(entry) == ["*⚙ Agent(Research limits)*"]


class TestToolLine:
    def test_prefers_description_over_other_keys(self) -> None:
        line = progress.tool_line("Bash", {"command": "git status", "description": "Check tree"})
        assert line == "*⚙ Bash(Check tree)*"

    def test_falls_back_to_command_then_any_string(self) -> None:
        assert progress.tool_line("Bash", {"command": "git status"}) == "*⚙ Bash(git status)*"
        assert progress.tool_line("Read", {"other": "note"}) == "*⚙ Read(note)*"

    def test_newlines_are_collapsed_and_long_input_truncated(self) -> None:
        line = progress.tool_line("Bash", {"command": "a\nb\n" + "x" * 200})
        assert "\n" not in line
        assert len(line) <= progress.TOOL_LINE_LIMIT + len("*⚙ Bash()*")

    def test_empty_input_names_the_tool_only(self) -> None:
        assert progress.tool_line("Skill", {}) == "*⚙ Skill*"


class TestChain:
    def test_appends_with_newlines(self) -> None:
        chain = progress.Chain()
        chain.add("one")
        chain.add("two")
        assert chain.text == "one\n\ntwo"

    def test_rolls_over_when_segment_overflows(self) -> None:
        chain = progress.Chain()
        chain.add("a" * 30)
        closed = chain.add("b" * progress.SEGMENT_LIMIT)
        assert closed == ["a" * 30]
        assert chain.text == "b" * progress.SEGMENT_LIMIT

    def test_oversized_chunk_is_split(self) -> None:
        chain = progress.Chain()
        closed = chain.add("x" * (progress.SEGMENT_LIMIT * 2 + 10))
        assert closed == ["x" * progress.SEGMENT_LIMIT, "x" * progress.SEGMENT_LIMIT]
        assert chain.text == "x" * 10


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
    await core.handle({"v": 1, "op": "status", "agent_id": agent_id, "status": "thinking"})


async def idle(core: Core, agent_id: str = "a1") -> None:
    await core.handle({"v": 1, "op": "status", "agent_id": agent_id, "status": "idle"})


async def test_turn_tail_opens_at_register(world: tuple[Core, FakeProvider, Path]) -> None:
    core, _, transcript = world
    agent = core.registry.get("a1")
    assert agent is not None and agent.tail is not None
    assert agent.tail.path == str(transcript)
    assert agent.tail.offset == 0
    tail = agent.tail
    await think(core)  # a thinking op must not reset a live tail
    assert agent.tail is tail


async def test_progress_sends_first_segment_as_card(world: tuple[Core, FakeProvider, Path]) -> None:
    core, provider, transcript = world
    write_entry(transcript, assistant_entry({"type": "text", "text": "Понял задачу."}))
    await core.progress_once()
    assert len(provider.cards) == 1
    chat_id, markdown, buttons = provider.cards[0]
    assert (chat_id, markdown, buttons) == (42, "Понял задачу.", ())
    agent = core.registry.get("a1")
    assert agent is not None and agent.tail is not None
    assert agent.tail.messages == {42: provider.cards and 1001}


async def test_progress_drains_without_thinking_status(
    world: tuple[Core, FakeProvider, Path],
) -> None:
    """Self-heal: after a mid-turn daemon restart the agent re-registers as
    idle — the tail must still stream the rest of the turn."""
    core, provider, transcript = world
    write_entry(transcript, assistant_entry({"type": "text", "text": "after restart"}))
    await core.progress_once()  # no thinking op was ever sent
    assert provider.cards == [(42, "after restart", ())]


async def test_progress_edits_are_throttled(world: tuple[Core, FakeProvider, Path]) -> None:
    core, provider, transcript = world
    write_entry(transcript, assistant_entry({"type": "text", "text": "first"}))
    await core.progress_once()
    write_entry(transcript, assistant_entry({"type": "text", "text": "second"}))
    await core.progress_once()  # too soon after the first write
    assert provider.edits == []
    agent = core.registry.get("a1")
    assert agent is not None and agent.tail is not None
    agent.tail.last_touch -= progress.PROGRESS_THROTTLE + 0.1
    await core.progress_once()
    assert provider.edits == [(42, 1001, "first\n\nsecond")]


async def test_idle_closes_the_turn_with_a_flush(world: tuple[Core, FakeProvider, Path]) -> None:
    core, provider, transcript = world
    write_entry(transcript, assistant_entry({"type": "text", "text": "mid-turn note"}))
    await idle(core)  # flush happens on close, no throttle — first write is a card
    assert provider.cards == [(42, "mid-turn note", ())]
    agent = core.registry.get("a1")
    assert agent is not None and agent.tail is None


async def test_idle_flush_edits_the_live_message(world: tuple[Core, FakeProvider, Path]) -> None:
    core, provider, transcript = world
    write_entry(transcript, assistant_entry({"type": "text", "text": "first"}))
    await core.progress_once()
    write_entry(transcript, assistant_entry({"type": "text", "text": "second"}))
    await idle(core)  # unthrottled flush of the live message
    assert provider.edits == [(42, 1001, "first\n\nsecond")]
    agent = core.registry.get("a1")
    assert agent is not None and agent.tail is None


async def test_rollover_abandons_the_old_message(world: tuple[Core, FakeProvider, Path]) -> None:
    core, provider, transcript = world
    write_entry(transcript, assistant_entry({"type": "text", "text": "a" * 30}))
    await core.progress_once()
    write_entry(transcript, assistant_entry({"type": "text", "text": "b" * progress.SEGMENT_LIMIT}))
    agent = core.registry.get("a1")
    assert agent is not None and agent.tail is not None
    agent.tail.last_touch -= progress.PROGRESS_THROTTLE + 0.1
    await core.progress_once()
    # the closed segment is left alone; a fresh message carries the new segment
    assert len(provider.cards) == 2
    assert provider.cards[1] == (42, "b" * progress.SEGMENT_LIMIT, ())
    assert provider.edits == []


# ------------------------------------------------------- final-duplicate skip


async def test_chain_covers_the_streamed_final() -> None:
    tail = progress.TurnTail(path="/dev/null")
    tail.chain.add("first note")
    tail.chain.add("final answer text")
    assert progress.chain_covers(tail, "final answer text")
    assert not progress.chain_covers(tail, "different final")
    assert not progress.chain_covers(tail, "  ")  # blank finals are never "covered"


async def test_final_already_streamed_is_not_sent_again(
    world: tuple[Core, FakeProvider, Path],
) -> None:
    core, provider, transcript = world
    final = "Итог: всё готово."
    write_entry(transcript, assistant_entry({"type": "text", "text": final}))
    resp = await core.handle({"v": 1, "op": "message", "agent_id": "a1", "text": final})
    assert resp == {"ok": True}
    assert provider.texts == []  # the chain already shows it verbatim
    await idle(core)
    agent = core.registry.get("a1")
    assert agent is not None and agent.tail is None


async def test_unseen_final_is_delivered(world: tuple[Core, FakeProvider, Path]) -> None:
    core, provider, _ = world
    resp = await core.handle(
        {"v": 1, "op": "message", "agent_id": "a1", "text": "Финал, которого нет в цепочке"}
    )
    assert resp == {"ok": True}
    assert provider.texts == [(42, "Финал, которого нет в цепочке", "agent")]
