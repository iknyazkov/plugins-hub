"""Entry point test: without a token — a clear error, the .env template is created."""

from __future__ import annotations

from pathlib import Path

import pytest

from channelsrv import config
from channelsrv.main import main


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
