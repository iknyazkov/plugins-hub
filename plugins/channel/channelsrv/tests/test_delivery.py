"""tmux pane scan and the hook-identity formula (delivery)."""

from channelsrv.delivery import agent_id_for, agent_name_for, parse_panes


def test_parse_panes_reads_tab_separated_rows() -> None:
    text = "%1\tclaude\t/Users/x/my-proj\tchannel\n%0\trbi\t/Users/x\tsystem\n"
    assert parse_panes(text) == [
        {"id": "%1", "command": "claude", "path": "/Users/x/my-proj", "session": "channel"},
        {"id": "%0", "command": "rbi", "path": "/Users/x", "session": "system"},
    ]


def test_parse_panes_skips_incomplete_rows() -> None:
    assert parse_panes("only-one-field\n\n%1\tclaude\t/path\tsess\n") == [
        {"id": "%1", "command": "claude", "path": "/path", "session": "sess"}
    ]


def test_parse_panes_empty_text() -> None:
    assert parse_panes("") == []


def test_agent_id_matches_hook_formula() -> None:
    # hooks/channel-hook identity(): claude-{slug}-{pane}, slug from basename(cwd)
    assert agent_id_for("/Users/x/plugins-hub", "%12") == "claude-plugins-hub-%12"
    assert agent_id_for("/Users/x/my proj", "%3") == "claude-my-proj-%3"
    assert agent_id_for("/Users/x/..weird..", "%7") == "claude-..weird..-%7"
    assert agent_id_for("/Users/x/!!proj!!", "%9") == "claude-proj-%9"


def test_agent_name_matches_hook_formula() -> None:
    # hooks/channel-hook identity(): {session}-{basename}-{pane_num}, pane number w/o '%'
    assert agent_name_for("channel", "/Users/x/plugins-hub", "%12") == "channel-plugins-hub-12"
    assert agent_name_for(None, "/Users/x/proj", "%5") == "proj-5"


def test_agent_id_of_root_path_is_still_valid() -> None:
    # basename("/") is empty -> "session" fallback, like the hook's
    assert agent_id_for("/", "%2") == "claude-session-%2"
