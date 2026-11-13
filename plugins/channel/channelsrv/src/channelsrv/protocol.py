"""Bridge-to-server protocol: JSONL frames, ops, statuses, events.

Request frame: ``{"v": 1, "op": "...", ...}`` — a single line of JSON.
Response frame: ``{"ok": true, ...}`` or ``{"ok": false, "error": "..."}``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PROTOCOL_VERSION = 1

OP_HEALTHZ = "healthz"
OP_REGISTER = "register"
OP_DEREGISTER = "deregister"
OP_STATUS = "status"
OP_MESSAGE = "message"
OP_APPROVAL_REQUEST = "approval_request"
OP_CONTEXT = "context"
OP_EVENTS = "events"

STATUS_IDLE = "idle"
STATUS_THINKING = "thinking"
STATUS_WAITING_USER = "waiting_user"
STATUS_WAITING_APPROVAL = "waiting_approval"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_OFFLINE = "offline"  # set by the server only (liveness), never by a bridge

STATUSES = frozenset(
    {
        STATUS_IDLE,
        STATUS_THINKING,
        STATUS_WAITING_USER,
        STATUS_WAITING_APPROVAL,
        STATUS_DONE,
        STATUS_ERROR,
        STATUS_OFFLINE,
    }
)
#: What a bridge may report about itself: offline is the server's verdict.
AGENT_STATUSES = STATUSES - {STATUS_OFFLINE}
#: Statuses that mean the turn itself is over — the progress tail closes on these.
#: waiting_* mid-turn states keep the tail: the turn continues after the wait.
TURN_END_STATUSES = frozenset({STATUS_IDLE, STATUS_DONE, STATUS_ERROR})

EVENT_USER_MESSAGE = "user_message"
EVENT_APPROVAL_RESULT = "approval_result"
EVENT_MODE_SET = "mode_set"
EVENT_MESSAGE = "message"  # a service agent's captured reply


class ProtocolError(ValueError):
    """Malformed request: the server answers ``{"ok": false, "error": ...}``."""


def ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def fail(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def check_version(request: Mapping[str, Any]) -> None:
    version = request.get("v")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"protocol v{PROTOCOL_VERSION}, got v={version!r}")


def req_str(request: Mapping[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"field {key!r} is required and must be a non-empty string")
    return value


def req_int(request: Mapping[str, Any], key: str) -> int:
    value = request.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"field {key!r} is required and must be an integer")
    return value


def opt_str(request: Mapping[str, Any], key: str) -> str | None:
    value = request.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProtocolError(f"field {key!r} must be a string")
    return value
