"""Config tests: paths, .env, the template, errors."""

from pathlib import Path

import pytest

from channelsrv import config


def test_base_dir_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(config.ENV_HOME, raising=False)
    assert config.base_dir() == tmp_path / ".config" / "channelsrv"


def test_base_dir_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ENV_HOME, "/somewhere/else")
    assert config.base_dir() == Path("/somewhere/else")


def test_load_env_file_parses(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "\n"
        "TELEGRAM_BOT_TOKEN=123:abc\n"
        "PROXY_URL=socks5://127.0.0.1:1080\n"
        "not-a-pair\n"
    )
    assert config.load_env_file(env) == {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "PROXY_URL": "socks5://127.0.0.1:1080",
    }


def test_load_env_file_missing(tmp_path: Path) -> None:
    assert config.load_env_file(tmp_path / ".env") == {}


def test_real_env_beats_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=from-file\n")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-env")
    cfg = config.load_config(tmp_path)
    assert cfg.token == "from-env"


def test_token_required(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("PROXY_URL=socks5://x\n")
    with pytest.raises(config.ConfigError) as err:
        config.load_config(tmp_path)
    assert "TELEGRAM_BOT_TOKEN" in str(err.value)
    assert ".env" in str(err.value)


def test_full_config(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=t\nPROXY_URL=http://p:1\nALLOWED_IDS=1, 22,333\n"
    )
    cfg = config.load_config(tmp_path)
    assert cfg.token == "t"
    assert cfg.proxy_url == "http://p:1"
    assert cfg.allowed_ids == frozenset({1, 22, 333})


def test_allowed_ids_unset_means_everyone(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=t\n")
    assert config.load_config(tmp_path).allowed_ids is None


def test_allowed_ids_garbage(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=t\nALLOWED_IDS=me,bob\n")
    with pytest.raises(config.ConfigError, match="ALLOWED_IDS"):
        config.load_config(tmp_path)


def test_ensure_layout_creates_template(tmp_path: Path) -> None:
    base = tmp_path / "cfg"
    config.ensure_layout(base)
    env_file = base / ".env"
    assert env_file.exists()
    assert config.ENV_TOKEN in env_file.read_text()
    assert (base / "logs").is_dir()
    assert env_file.stat().st_mode & 0o777 == 0o600
    assert base.stat().st_mode & 0o777 == 0o700


def test_ensure_layout_keeps_existing_env(tmp_path: Path) -> None:
    base = tmp_path / "cfg"
    base.mkdir()
    (base / ".env").write_text("TELEGRAM_BOT_TOKEN=keep-me\n")
    config.ensure_layout(base)
    assert (base / ".env").read_text() == "TELEGRAM_BOT_TOKEN=keep-me\n"


def test_ensure_layout_idempotent(tmp_path: Path) -> None:
    base = tmp_path / "cfg"
    config.ensure_layout(base)
    before = (base / ".env").read_text()
    config.ensure_layout(base)
    assert (base / ".env").read_text() == before


def test_apply_env_file_fills_gaps(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=from-file\nCHANNEL_AI_AGENT=zaiclaude\n"
    )
    environ: dict[str, str] = {"TELEGRAM_BOT_TOKEN": "from-real-env"}
    config.apply_env_file(tmp_path, environ)
    assert environ["CHANNEL_AI_AGENT"] == "zaiclaude"  # gap filled from the file
    assert environ["TELEGRAM_BOT_TOKEN"] == "from-real-env"  # real env wins


def test_apply_env_file_missing(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    config.apply_env_file(tmp_path, environ)
    assert environ == {}
