"""Bot features. Each is a separate module with a Feature class and a register method.

Removing a feature = deleting its import and its line in FEATURES: the core
and the other features know nothing about each other, they talk only through
the core's hooks. Features with long-lived loops expose a ``background``
method; ``background_tasks`` collects them for the entry point to run.
"""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from channelsrv.features.approvals import Feature as ApprovalsFeature
from channelsrv.features.context_cmd import Feature as ContextFeature
from channelsrv.features.cron import Feature as CronFeature
from channelsrv.features.modes import Feature as ModesFeature
from channelsrv.features.nlcmd import Feature as NlCmdFeature
from channelsrv.features.passthrough import Feature as PassthroughFeature
from channelsrv.features.selection import Feature as SelectionFeature
from channelsrv.features.sessions import Feature as SessionsFeature
from channelsrv.features.status import Feature as StatusFeature
from channelsrv.server import Core

_NLCMD = NlCmdFeature()
FEATURES = (
    StatusFeature(),
    SelectionFeature(),
    ApprovalsFeature(),
    ModesFeature(),
    PassthroughFeature(),
    SessionsFeature(),
    ContextFeature(),
    _NLCMD,
    CronFeature(convert=_NLCMD.convert),
)

#: Advertised in the Telegram command menu (set_my_commands at startup).
#: Descriptions live here because only the feature registry knows the set.
MENU = (
    ("agents", "pick the active agent"),
    ("status", "snapshot of all agents"),
    ("mode", "modes of the active agent"),
    ("command", "run a command in the active agent"),
    ("new", "spawn a new agent session in tmux"),
    ("close", "close a tmux agent session"),
    ("context", "context usage of the active agent"),
    ("cron", "scheduled agent jobs (list/add/remove/run)"),
)


def register_all(core: Core) -> None:
    for feature in FEATURES:
        feature.register(core)


def background_tasks() -> list[Coroutine[Any, Any, None]]:
    """Feature-owned loops the entry point should run as tasks alongside its own."""
    tasks: list[Coroutine[Any, Any, None]] = []
    for feature in FEATURES:
        background = getattr(feature, "background", None)
        if callable(background):
            tasks.append(background())
    return tasks
