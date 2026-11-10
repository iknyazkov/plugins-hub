"""Paths, ``.env`` loading and startup configuration.

The real process env takes precedence over file values — the file only fills
the gaps (credentials may come from the launcher's environment).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "CHANNELSRV_HOME"
ENV_TOKEN = "TELEGRAM_BOT_TOKEN"
ENV_PROXY = "PROXY_URL"
ENV_ALLOWED = "ALLOWED_IDS"

ENV_TEMPLATE = f"""\
# channelsrv — configuration. This file is created on first start.
# Telegram bot token from @BotFather (required):
{ENV_TOKEN}=

# Outgoing proxy: socks5://user:pass@host:port or http(s)://... (optional):
# {ENV_PROXY}=socks5://127.0.0.1:1080

# Allowed Telegram user ids, comma-separated (optional; empty = allow everyone):
# {ENV_ALLOWED}=123456789
"""


class ConfigError(RuntimeError):
    """Configuration is incomplete or broken — the server must not start."""


def base_dir(environ: Mapping[str, str] | None = None) -> Path:
    """State root: ``~/.config/channelsrv``, override via CHANNELSRV_HOME."""
    env = os.environ if environ is None else environ
    return Path(env.get(ENV_HOME, str(Path.home() / ".config" / "channelsrv")))


@dataclass(frozen=True)
class Config:
    token: str
    proxy_url: str | None
    allowed_ids: frozenset[int] | None  # None = allow everyone


def load_env_file(path: Path) -> dict[str, str]:
    """Parses ``KEY=VALUE`` lines; comments and garbage are skipped."""
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return {}
    pairs: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        pairs[key.strip()] = value.strip()
    return pairs


def load_config(base: Path, environ: Mapping[str, str] | None = None) -> Config:
    """Assembles the config: the real env beats ``base/.env``."""
    env = os.environ if environ is None else environ
    from_file = load_env_file(base / ".env")

    def get(key: str) -> str | None:
        value = env.get(key) or from_file.get(key)
        return value or None

    token = get(ENV_TOKEN)
    if token is None:
        raise ConfigError(
            f"{ENV_TOKEN} is not set: put the bot token into {base / '.env'} "
            "(the template is created on first start)"
        )

    raw_ids = get(ENV_ALLOWED)
    allowed: frozenset[int] | None = None
    if raw_ids is not None:
        try:
            allowed = frozenset(int(x) for x in raw_ids.replace(" ", "").split(",") if x)
        except ValueError as exc:
            raise ConfigError(
                f"{ENV_ALLOWED} must be comma-separated numeric user ids, got: {raw_ids!r}"
            ) from exc

    return Config(token=token, proxy_url=get(ENV_PROXY), allowed_ids=allowed)


def ensure_layout(base: Path) -> None:
    """Creates directories and the ``.env`` template, touches nothing existing."""
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    (base / "logs").mkdir(exist_ok=True, mode=0o700)
    env_file = base / ".env"
    if not env_file.exists():
        env_file.write_text(ENV_TEMPLATE)
        env_file.chmod(0o600)


def apply_env_file(base: Path, environ: MutableMapping[str, str] | None = None) -> None:
    """Pushes ``base/.env`` keys into the process environment, fill-gaps style.

    Beyond the token/proxy/allowed trio this is how feature knobs reach the
    daemon (``CHANNEL_AI_AGENT``, ``CHANNEL_CRON_TIMEOUT``, …): the daemon is
    spawned from a session hook whose environment carries nothing of the sort.
    Values already present in the real environment win.
    """
    env = os.environ if environ is None else environ
    for key, value in load_env_file(base / ".env").items():
        if key not in env:
            env[key] = value
