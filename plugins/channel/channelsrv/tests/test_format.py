"""Formatting tests: splitting and conversion to entities."""

import pytest
import telegram

from channelsrv import formatting


def test_short_text_single_chunk() -> None:
    assert formatting.split_markdown("hello") == ["hello"]


def test_empty_text_no_chunks() -> None:
    assert formatting.split_markdown("") == []


def test_split_on_paragraph_boundaries() -> None:
    paras = [f"para{i} " + "x" * 900 for i in range(6)]
    text = "\n\n".join(paras)
    chunks = formatting.split_markdown(text, limit=2000)
    assert len(chunks) == 3
    assert all(len(c) <= 2000 for c in chunks)
    assert chunks[0].startswith("para0")
    assert chunks[1].startswith("para2")
    assert chunks[2].startswith("para4")


def test_hard_split_giant_paragraph() -> None:
    chunks = formatting.split_markdown("y" * 8000, limit=3500)
    assert [len(c) for c in chunks] == [3500, 3500, 1000]


def test_no_split_when_fits() -> None:
    text = "a" * 3500
    assert formatting.split_markdown(text) == [text]


def test_render_returns_ptb_entities() -> None:
    text, entities = formatting.render("**bold** and `code`")
    assert "*" not in text and "`" not in text
    assert "bold" in text
    assert any(e.type == "bold" for e in entities)
    assert any(e.type == "code" for e in entities)
    assert all(isinstance(e, telegram.MessageEntity) for e in entities)


def test_render_keeps_plain_text_verbatim() -> None:
    text, _ = formatting.render("period. exclamation! hyphen - slash /")
    assert "period. exclamation! hyphen - slash /" in text


def test_render_falls_back_to_plain_on_converter_blowup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import telegramify_markdown

    def boom(_s: str) -> tuple[str, list[object]]:
        raise RuntimeError("converter blew up")

    monkeypatch.setattr(telegramify_markdown, "convert", boom)
    # the formatting module holds a reference to the module, not the function — the patch applies
    text, entities = formatting.render("# fail")
    assert text == "# fail"
    assert entities == []


def test_with_prefix_marks_first_chunk() -> None:
    chunks = formatting.with_prefix("claude-1", ["body", "tail"])
    assert chunks[0] == "**[claude-1]**\n\nbody"
    assert chunks[1] == "tail"


def test_with_prefix_empty() -> None:
    assert formatting.with_prefix("x", []) == []


def test_with_prefix_truncates_huge_name() -> None:
    chunks = formatting.with_prefix("n" * 300, ["body"])
    assert len(chunks[0]) < 100
