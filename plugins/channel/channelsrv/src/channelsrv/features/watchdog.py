"""The pane watchdog: presses the keys that blocking CC screens wait for.

Every tick each registered tmux agent's visible screen is captured. A screen
is acted on only when it is quiescent (unchanged since the previous tick —
the agent is not mid-output) and matches a signature from ``signatures.json``:
all of the signature's regexes hit and the signature's keys are pressed.
Screens with no matching signature are never touched — the watchdog does not
guess. Repeated presses on one unchanged screen are capped so a wrong
signature cannot loop forever; any screen change starts a fresh episode.

``/screen`` shows the active agent's visible pane in the chat, so a stuck
screen can be identified from Telegram and added to the registry by hand.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from channelsrv import delivery
from channelsrv.server import Core

log = logging.getLogger(__name__)

#: Seconds between watchdog ticks; also the quiescence window (a screen must
#: survive one unchanged tick before keys are pressed on it).
TICK = 5.0

#: Max presses on one unchanged screen — a wrong signature must not loop.
MAX_PRESSES = 3

#: The registry shipped with the package; entries are added by hand as new
#: blocking screens are observed in real sessions.
SIGNATURES_FILE = Path(__file__).resolve().parent.parent / "signatures.json"


@dataclass(frozen=True)
class Signature:
    """One blocking screen: all ``patterns`` must match to press ``keys``."""

    id: str
    patterns: tuple[re.Pattern[str], ...]
    keys: tuple[str, ...]


def load_signatures(path: Path | None = None) -> tuple[Signature, ...]:
    """Parses the registry once at startup; malformed entries abort the start."""
    raw: Any = json.loads((path or SIGNATURES_FILE).read_text())
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{SIGNATURES_FILE.name}: expected a non-empty list of entries")
    signatures: list[Signature] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise TypeError(f"{SIGNATURES_FILE.name}: entry is not an object: {entry!r}")
        sig_id = entry.get("id")
        matches, keys = entry.get("match"), entry.get("keys")
        if not sig_id or not isinstance(matches, list) or not matches:
            raise ValueError(f"{SIGNATURES_FILE.name}: {sig_id!r} needs id and non-empty match")
        if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
            raise ValueError(f"{SIGNATURES_FILE.name}: {sig_id!r} needs non-empty string keys")
        try:
            patterns = tuple(re.compile(m) for m in matches if isinstance(m, str))
        except re.error as exc:
            raise ValueError(f"{SIGNATURES_FILE.name}: {sig_id!r} has a broken regex: {exc}") from exc
        signatures.append(Signature(id=sig_id, patterns=patterns, keys=tuple(keys)))
    return tuple(signatures)


class Feature:
    def __init__(
        self,
        capture: Callable[[str], Awaitable[str]] | None = None,
        send_keys: Callable[..., Awaitable[None]] | None = None,
        signatures: tuple[Signature, ...] | None = None,
    ) -> None:
        self._capture = capture or delivery.capture_pane
        self._press = send_keys or delivery.send_keys
        self._signatures = signatures
        self._core: Core
        self._prev: dict[str, str] = {}  # tmux_target -> last seen screen
        self._presses: dict[str, int] = {}  # tmux_target -> presses on this unchanged screen

    def register(self, core: Core) -> None:
        self._core = core
        if self._signatures is None:
            self._signatures = load_signatures()
        core.add_command("screen", self._screen_command)

    def background(self) -> Coroutine[Any, Any, None]:
        """The watchdog loop, run as a task by the entry point (main.py)."""
        return self._loop_forever()

    async def _loop_forever(self) -> None:
        while True:
            try:
                await self._tick_once()
            except Exception:
                log.warning("watchdog tick failed", exc_info=True)
            await asyncio.sleep(TICK)

    async def _tick_once(self) -> None:
        for agent in self._core.registry.snapshot():
            target = agent.tmux_target
            if target is None:
                continue
            try:
                screen = await self._capture(target)
            except Exception:  # noqa: BLE001 — unreachable pane; retry next tick
                self._prev.pop(target, None)
                continue
            if self._prev.get(target) != screen:
                self._prev[target] = screen
                self._presses.pop(target, None)
                continue  # first sight of this screen: quiescence not proven yet
            self._presses[target] = self._presses.get(target, 0)
            if self._presses[target] >= MAX_PRESSES:
                continue
            signature = self._match(screen)
            if signature is None:
                continue
            self._presses[target] += 1
            log.info("watchdog: %s on %s (%s)", signature.id, target, agent.id)
            await self._press(target, *signature.keys)

    def _match(self, screen: str) -> Signature | None:
        assert self._signatures is not None  # set in register()
        for signature in self._signatures:
            if all(p.search(screen) for p in signature.patterns):
                return signature
        return None

    async def _screen_command(self, chat_id: int, rest: str) -> None:
        agent = self._core.active_agent(chat_id)
        if agent is None:
            await self._core.provider.send_text(chat_id, self._core.select_hint)
            return
        if agent.tmux_target is None:
            await self._core.provider.send_text(chat_id, f"`{agent.name}` has no tmux pane.")
            return
        try:
            screen = await self._capture(agent.tmux_target)
        except Exception as exc:  # noqa: BLE001 — tmux failures are answerable, not fatal
            await self._core.provider.send_text(chat_id, f"capture failed: {exc}")
            return
        await self._core.provider.send_text(chat_id, f"```\n{screen}\n```")
