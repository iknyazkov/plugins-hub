"""Delivery to an agent living in a tmux pane: bracketed paste + Enter.

When an agent registers with ``tmux_target`` (the bridge hook passes its
``$TMUX_PANE``), incoming user messages go straight into the pane the same
way the user would type them. Each message gets its own tmux buffer so
parallel pastes never overwrite each other; one global lock serializes the
send so two messages cannot interleave keystrokes into a single pane.
Session lifecycle helpers live here too (``new_session``/``close_session``).
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import re

log = logging.getLogger(__name__)

#: Optional dedicated tmux server name (``tmux -L``); default socket otherwise.
TMUX_SOCKET_ENV = "CHANNEL_TMUX_SOCKET"

SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_lock = asyncio.Lock()
_seq = itertools.count()


def _base() -> list[str]:
    sock = os.environ.get(TMUX_SOCKET_ENV)
    return ["tmux", "-L", sock] if sock else ["tmux"]


def validate_session_name(name: str) -> str:
    if not SESSION_NAME_RE.match(name):
        raise ValueError(
            f"session name {name!r} is invalid: letters, digits, '_' and '-' only"
        )
    return name


async def session_exists(name: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        *_base(),
        "has-session",
        "-t",
        validate_session_name(name),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    return proc.returncode == 0


async def session_names() -> list[str]:
    """All tmux session names on the socket; empty when tmux is unreachable."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *_base(),
            "list-sessions",
            "-F",
            "#{session_name}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    return [line for line in out.decode().splitlines() if line]


PANE_FORMAT = "#{pane_id}\t#{pane_current_command}\t#{pane_current_path}\t#{session_name}"


def parse_panes(text: str) -> list[dict[str, str]]:
    """Parses ``list-panes`` output rows of four tab-separated fields."""
    panes: list[dict[str, str]] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 4 or not parts[0]:
            continue
        panes.append(
            {"id": parts[0], "command": parts[1], "path": parts[2], "session": parts[3]}
        )
    return panes


async def list_panes() -> list[dict[str, str]]:
    """All panes on the socket; empty when tmux is unreachable."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *_base(),
            "list-panes",
            "-a",
            "-F",
            PANE_FORMAT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    return parse_panes(out.decode())


def slugify(base: str) -> str:
    """The hook's project-slug transform (hooks/channel-hook ``identity()``)."""
    slug = "".join(c if (c.isalnum() or c in "._-") else "-" for c in base).strip("-")
    return slug or "session"


def agent_id_for(path: str, pane: str) -> str:
    """The agent id a hook in this pane would register: ``claude-{slug}-{pane}``."""
    return f"claude-{slugify(os.path.basename(path) or 'session')}-{pane}"


def agent_name_for(session: str | None, path: str, pane: str) -> str:
    """The display name a hook in this pane would register (no env overrides)."""
    base = os.path.basename(path) or "session"
    pane_num = pane.lstrip("%") or pane
    return f"{session}-{base}-{pane_num}" if session else f"{base}-{pane_num}"


async def new_session(name: str, command: str, cwd: str) -> None:
    """Spawns a detached tmux session running ``command``.

    The command goes through an interactive shell so the user's aliases
    resolve; a taken name raises before anything is spawned.
    """
    validate_session_name(name)
    if "\n" in command or not command.strip():
        raise ValueError("agent command must be a single non-empty line")
    if await session_exists(name):
        raise ValueError(f"tmux session {name!r} already exists")
    shell = os.environ.get("SHELL") or "/bin/zsh"
    await _run(["new-session", "-d", "-s", name, "-c", cwd, shell, "-ic", command])


async def close_session(name: str) -> None:
    validate_session_name(name)
    if not await session_exists(name):
        raise ValueError(f"no such tmux session {name!r}")
    await _run(["kill-session", "-t", name])


async def _run(args: list[str], stdin: bytes | None = None) -> None:
    proc = await asyncio.create_subprocess_exec(
        *_base(),
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate(stdin)
    if proc.returncode != 0:
        raise RuntimeError(f"tmux {args[0]} exited with {proc.returncode}")


def sanitize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


async def paste(target: str, text: str) -> None:
    """Pastes ``text`` into the ``target`` pane and presses Enter."""
    clean = sanitize(text)
    if not clean.strip():
        return
    buffer = f"channel-{next(_seq)}"
    async with _lock:
        # the pane check doubles as "is this tmux server reachable at all"
        await _run(["display-message", "-p", "-t", target, "#{pane_id}"])
        await _run(["load-buffer", "-b", buffer, "-"], stdin=clean.encode("utf-8"))
        await _run(["paste-buffer", "-p", "-r", "-d", "-b", buffer, "-t", target])
        await _run(["send-keys", "-t", target, "Enter"])


async def paste_safely(target: str, text: str) -> None:
    """paste() that never raises: delivery failure is logged, not fatal."""
    try:
        await paste(target, text)
    except Exception:
        log.warning("tmux delivery to %s failed", target, exc_info=True)


async def pane_alive(target: str) -> bool:
    """True when the target pane exists (tmux server reachable)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *_base(),
            "display-message",
            "-p",
            "-t",
            target,
            "#{pane_id}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
    except OSError:
        return False  # tmux binary missing or not executable — panes unreachable
    return proc.returncode == 0 and bool(out.strip())


async def capture_pane(target: str) -> str:
    """The visible content of the ``target`` pane (screen only, no history)."""
    proc = await asyncio.create_subprocess_exec(
        *_base(),
        "capture-pane",
        "-p",
        "-t",
        target,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"tmux capture-pane exited with {proc.returncode}")
    return out.decode()


async def send_keys(target: str, *keys: str) -> None:
    """Sends key names (``Enter``, ``Escape``, …) to the ``target`` pane."""
    await _run(["send-keys", "-t", target, *keys])
