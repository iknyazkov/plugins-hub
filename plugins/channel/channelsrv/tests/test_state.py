"""Chat state store tests (state.json)."""

from pathlib import Path

from channelsrv.state import StateStore


def test_missing_file_starts_empty(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.load()
    assert store.active_agent(42) is None
    assert store.chat_ids() == []


def test_set_and_reload(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.load()
    store.set_active_agent(42, "agent-1")
    reopened = StateStore(path)
    reopened.load()
    assert reopened.active_agent(42) == "agent-1"


def test_corrupt_file_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{broken json")
    store = StateStore(path)
    store.load()
    assert store.active_agent(1) is None


def test_chats_for_agent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.load()
    store.set_active_agent(1, "a")
    store.set_active_agent(2, "b")
    store.set_active_agent(3, "a")
    assert sorted(store.chats_for_agent("a")) == [1, 3]
    assert store.chats_for_agent("missing") == []


def test_reselect_overwrites(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.load()
    store.set_active_agent(1, "a")
    store.set_active_agent(1, "b")
    assert store.active_agent(1) == "b"
    assert store.chats_for_agent("a") == []


def test_note_chat_creates_entry_without_selection(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.load()
    store.note_chat(7)
    assert store.active_agent(7) is None
    assert store.chat_ids() == [7]
    reopened = StateStore(path)
    reopened.load()
    assert reopened.chat_ids() == [7]


def test_note_chat_keeps_existing_selection(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.load()
    store.set_active_agent(7, "a")
    store.note_chat(7)
    assert store.active_agent(7) == "a"


def test_no_tmp_leftover(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.load()
    store.set_active_agent(1, "a")
    assert list(tmp_path.iterdir()) == [tmp_path / "state.json"]
