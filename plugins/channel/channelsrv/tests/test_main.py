"""Entry point test: without a token — a clear error, the .env template is created."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from channelsrv import config
from channelsrv.main import cleanup_files, main


def test_main_without_token_exits_with_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(config.ENV_HOME, str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err
    env_file = tmp_path / ".env"
    assert env_file.exists()
    assert config.ENV_TOKEN in env_file.read_text()
    assert (tmp_path / "logs").is_dir()


# ------------------------------------------------- shutdown cleanup ownership


def test_cleanup_removes_our_files(tmp_path: Path) -> None:
    sock = tmp_path / "server.sock"
    sock.write_text("")
    pid = tmp_path / "server.pid"
    pid.write_text(str(os.getpid()))
    cleanup_files(tmp_path, bound_ino=sock.stat().st_ino)
    assert not sock.exists() and not pid.exists()


def test_cleanup_spares_a_successors_files(tmp_path: Path) -> None:
    sock = tmp_path / "server.sock"
    sock.write_text("successor")
    pid = tmp_path / "server.pid"
    pid.write_text("999999")  # a successor's pid, not ours
    cleanup_files(tmp_path, bound_ino=None)  # we never bound (crashed early)
    assert sock.exists() and pid.exists()


def test_cleanup_spares_a_rebound_socket(tmp_path: Path) -> None:
    sock = tmp_path / "server.sock"
    sock.write_text("")
    other = tmp_path / "other"
    other.write_text("")
    pid = tmp_path / "server.pid"
    pid.write_text(str(os.getpid()))
    # a successor re-bound the path: its inode differs from ours
    cleanup_files(tmp_path, bound_ino=other.stat().st_ino)
    assert sock.exists() and not pid.exists()


def test_cleanup_survives_missing_files(tmp_path: Path) -> None:
    cleanup_files(tmp_path, bound_ino=None)  # nothing exists — no raise


# ------------------------------------------- one token = one daemon (youngest wins)


def test_stale_pids_exclude_self_parent_and_non_channelsrv() -> None:
    """The zombie incident (2026-11-17): a Monday daemon ignored SIGTERM and
    lived beside the 0.2.0 one for days — 409 polling conflicts and ghost
    re-streams from its stale tails. Startup enforcement selects exactly the
    elders: every channelsrv pid except ours and our uv wrapper parent."""
    from channelsrv.main import stale_pids

    assert stale_pids(["14133 uv run channelsrv", "14134 .../channelsrv"], me=14134, parent=14133) == []
    assert stale_pids(
        ["14133 uv run channelsrv", "14134 .../channelsrv", "37460 .../channelsrv"],
        me=37460,
        parent=37459,
    ) == [14133, 14134]
    assert stale_pids(["5001 ruff server", ""], me=37460, parent=37459) == []
