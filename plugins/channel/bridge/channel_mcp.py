#!/usr/bin/env python3
"""Resident MCP bridge for channelsrv: keeps the daemon alive, exposes it as tools.

Stdio MCP server (newline-delimited JSON-RPC, stdlib only). A background
thread owns the daemon lifecycle and the persistent events long-poll:
healthz probe -> (under a flock) grace period for a slow daemon -> stop only
a truly wedged daemon -> spawn `uv run channelsrv` -> register -> keep
`events` open so the server sees this agent as alive and buffers user
messages.

Daemon protocol: one UDS connection = one JSONL frame, see channelsrv/README.md.
"""

from __future__ import annotations

import atexit
import collections
from datetime import datetime
import fcntl
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

VERSION = "0.1.3"
PROTOCOL_VERSION = 1

#: Delivered to Claude as context when the channel server connects.
INSTRUCTIONS = (
    "This session is wired to the channelsrv daemon: in tmux sessions the "
    "plugin hooks paste Telegram messages straight into the pane and send "
    "your final reply back when the turn ends — no action needed from you. "
    "The channel_* tools are the fallback for sessions without tmux: "
    "channel_send talks to Telegram, channel_set_status reports progress "
    "('thinking' shows a typing indicator; set waiting_user/waiting_approval "
    "while blocked on the sender), channel_request_approval asks a yes/no "
    "question as a card, channel_wait_events drains incoming events. Ask the "
    "user questions in plain text (channel_send or your reply); NEVER use "
    "AskUserQuestion — the session hooks deny it."
)

HEALTHZ_BUDGET = 2.5  # seconds; a healthy daemon answers in milliseconds
GRACE_BUDGET = 8.0  # a live daemon gets this long to answer before it is called wedged
EVENTS_SERVER_TIMEOUT = 25.0  # the daemon holds the events frame this long
EVENTS_SOCKET_TIMEOUT = 35.0  # socket budget > server long-poll
OP_TIMEOUT = 35.0  # message/approval ops may take a Telegram roundtrip
READY_WAIT = 90.0  # how long a tool call waits for the daemon (cold uv sync)
SPAWN_DEADLINE = 120.0  # how long the spawning bridge waits for healthz

FRAME_LIMIT = 2**20
SRV_DIR = Path(__file__).resolve().parent.parent / "channelsrv"

log = logging.getLogger("channel")


def home_dir() -> Path:
    return Path(os.environ.get("CHANNELSRV_HOME", str(Path.home() / ".config" / "channelsrv")))


def sock_path() -> Path:
    return home_dir() / "server.sock"


class BridgeError(RuntimeError):
    """A failure worth surfacing to the agent as a tool error."""


class TransportError(BridgeError):
    """The daemon socket did not answer properly."""


# --------------------------------------------------------------------- client


def call_op(payload: dict, timeout: float) -> dict:
    """One connection = one frame: send a request line, read a response line."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(str(sock_path()))
        sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                raise TransportError("daemon closed the connection without a response")
            buf += chunk
            if len(buf) > FRAME_LIMIT:
                raise TransportError("oversized response frame")
    except OSError as exc:
        raise TransportError("cannot reach the daemon socket: %s" % exc) from exc
    finally:
        sock.close()
    try:
        resp = json.loads(buf.decode("utf-8"))
    except ValueError:
        raise TransportError("malformed response frame") from None
    if not isinstance(resp, dict):
        raise TransportError("malformed response frame")
    return resp


def healthy(timeout: float) -> bool:
    try:
        return call_op({"v": PROTOCOL_VERSION, "op": "healthz"}, timeout).get("ok") is True
    except BridgeError:
        return False


# ------------------------------------------------------------------- lifecycle


def daemon_log(level: str, message: str) -> None:
    """One line into the daemon's own log, in its format — the shared trace.

    The bridge and the daemon are separate processes; without this a kill
    ordered by the bridge is invisible in server.log (the SIGTERM handler's
    last words get lost with the victim). Best effort: logging must never
    break the lifecycle.
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    line = "%s %s channelsrv.bridge: %s\n" % (stamp, level, message)
    try:
        (home_dir() / "logs").mkdir(parents=True, exist_ok=True)
        with open(home_dir() / "logs" / "server.log", "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def stop_wedged_daemon(reason: str) -> None:
    """Terminates the daemon recorded in server.pid — but only a live one.

    A dead pid (crash, manual kill) needs no signal: the next daemon unlinks
    the leftover socket itself on startup. Killing on suspicion is what
    caused the respawn storms of 2026-11-09: a slow healthz answer used to
    be indistinguishable from a dead daemon.
    """
    try:
        pid = int((home_dir() / "server.pid").read_text().strip())
    except (OSError, ValueError):
        daemon_log("WARNING", "no readable server.pid, nothing to stop (%s)" % reason)
        return
    alive = True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        alive = False
    except PermissionError:  # alive but owned by another user
        pass
    if not alive:
        daemon_log(
            "INFO",
            "daemon pid %d already dead; the next spawn cleans up after it (%s)" % (pid, reason),
        )
        return
    daemon_log("WARNING", "stopping wedged daemon pid %d (%s)" % (pid, reason))
    log.warning("stopping wedged daemon pid %d (%s)", pid, reason)
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    time.sleep(0.2)


def spawn_daemon() -> subprocess.Popen:
    try:
        return subprocess.Popen(
            ["uv", "run", "channelsrv"],
            cwd=str(SRV_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise BridgeError(
            "uv not found — install it (https://docs.astral.sh/uv/) or start the "
            "daemon manually: cd %s && uv run channelsrv" % SRV_DIR
        ) from None


def ensure_daemon() -> None:
    """Healthz -> (under a flock) grace for a slow daemon -> stop a wedged one -> spawn."""
    if healthy(HEALTHZ_BUDGET):
        return
    home = home_dir()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = open(home / "bridge.lock", "a+")
    try:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # another bridge is spawning right now — just wait for the result
            deadline = time.monotonic() + SPAWN_DEADLINE
            while time.monotonic() < deadline:
                if healthy(HEALTHZ_BUDGET):
                    return
                time.sleep(0.25)
            raise BridgeError(
                "another bridge is starting the daemon and it did not come up "
                "within %ss; see %s" % (SPAWN_DEADLINE, home / "logs" / "server.log")
            )
        # we hold the spawn lock; a second look in case a spawner just finished
        if healthy(HEALTHZ_BUDGET):
            return
        if healthy(GRACE_BUDGET):
            return  # alive, just slow — a kill/spawn cycle buys nothing
        stop_wedged_daemon(
            "no healthz answer within %.1fs + %.1fs" % (HEALTHZ_BUDGET, GRACE_BUDGET)
        )
        proc = spawn_daemon()
        deadline = time.monotonic() + SPAWN_DEADLINE
        while time.monotonic() < deadline:
            if healthy(HEALTHZ_BUDGET):
                return
            if proc.poll() is not None:
                raise BridgeError(
                    "daemon exited with code %s during startup; check "
                    "~/.config/channelsrv/.env (TELEGRAM_BOT_TOKEN) and the log: %s"
                    % (proc.returncode, home / "logs" / "server.log")
                )
            time.sleep(0.25)
        raise BridgeError(
            "daemon did not become healthy within %ss; see %s"
            % (SPAWN_DEADLINE, home / "logs" / "server.log")
        )
    finally:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally:
            lock.close()


# ------------------------------------------------------------------- channel


class Channel:
    """Daemon lifecycle + persistent events long-poll, owned by one thread."""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.buffer: collections.deque = collections.deque()
        self.cursor = 0
        self.error: str | None = None
        self.error_since = 0.0
        self.online = False
        # stdout writes are serialized across threads; the events buffer
        # feeds channel_wait_events
        self.send_lock = threading.Lock()
        self.events_lock = threading.Lock()

        cwd = os.getcwd()
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(cwd)).strip("-") or "session"
        # Inside tmux the hook shim owns registration and delivery; the bridge
        # only makes sure the daemon is up and stays out of the registry.
        self.hook_mode = bool(os.environ.get("TMUX_PANE"))
        # Stable per-project identity: re-registering the same id replaces the
        # registry entry, so the chat's active-agent pin survives plugin
        # reloads. A pid suffix here would orphan the pin on every restart.
        # Parallel sessions in the same project collide — disambiguate via
        # CHANNEL_AGENT_ID.
        self.agent_id = os.environ.get("CHANNEL_AGENT_ID") or "claude-%s" % slug
        self.name = os.environ.get("CHANNEL_AGENT_NAME") or os.path.basename(cwd) or "claude"
        self.session_name = os.environ.get("CHANNEL_SESSION_NAME") or cwd
        modes_env = os.environ.get("CHANNEL_AGENT_MODES", "")
        self.modes = [m.strip() for m in modes_env.split(",") if m.strip()]
        self.thread = threading.Thread(target=self._worker, daemon=True)

    def start(self) -> None:
        self.thread.start()

    # ------------------------------------------------------- channel delivery

    def send(self, msg: dict) -> None:
        """One serialized JSONL line to stdout; safe from any thread."""
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        with self.send_lock:
            sys.stdout.write(line)
            sys.stdout.flush()

    def _worker(self) -> None:
        while not self.stop.is_set():
            try:
                if not self.online:
                    ensure_daemon()
                    self.online = True
                    self.error = None
                    self.ready.set()
                    if self.hook_mode:
                        log.info("tmux session: hooks own the channel, bridge idles")
                    else:
                        call_op(
                            {
                                "v": PROTOCOL_VERSION,
                                "op": "register",
                                "agent_id": self.agent_id,
                                "name": self.name,
                                "session_name": self.session_name,
                                "modes": self.modes,
                            },
                            OP_TIMEOUT,
                        )
                        self.cursor = 0
                        log.info("daemon ready, registered as %s", self.agent_id)
                if self.hook_mode:
                    # KNOWN-ISSUE 1 (wiki/KNOWN_ISSUES.md): a passive sleep —
                    # after a daemon restart this bridge never re-registers
                    # the pane's agent; only a hook firing in the pane does.
                    self.stop.wait(3600.0)
                    continue
                resp = call_op(
                    {
                        "v": PROTOCOL_VERSION,
                        "op": "events",
                        "agent_id": self.agent_id,
                        "cursor": self.cursor,
                    },
                    EVENTS_SOCKET_TIMEOUT,
                )
                if resp.get("ok") is not True:
                    # the daemon restarted and lost the registry
                    if "not registered" in str(resp.get("error", "")):
                        self.online = False
                        self.ready.clear()
                    else:
                        self.stop.wait(1.0)
                    continue
                events = resp.get("events")
                if events:
                    with self.events_lock:
                        self.buffer.extend(events)
                if isinstance(resp.get("cursor"), int):
                    self.cursor = resp["cursor"]
            except Exception as exc:  # transport died — heal and keep polling
                if self.stop.is_set():
                    break
                self.online = False
                self.ready.clear()
                message = str(exc) or exc.__class__.__name__
                if message != self.error:
                    self.error = message
                    self.error_since = time.monotonic()
                self.stop.wait(2.0)

    def await_ready(self, timeout: float) -> str | None:
        """None when the daemon is up and this agent is registered, else the reason."""
        deadline = time.monotonic() + timeout
        while True:
            if self.ready.wait(0.5):
                return None
            # an error stuck for a while means startup failed for good (exited
            # daemon, spawn deadline) — bail out early instead of stalling
            if self.error is not None and time.monotonic() - self.error_since > 10.0:
                return "channel daemon is not available: %s" % self.error
            if time.monotonic() >= deadline:
                return "channel daemon is not available after %ss: %s" % (timeout, self.error or "starting up")

    def wait_events(self, timeout: float) -> list:
        deadline = time.monotonic() + timeout
        while True:
            with self.events_lock:
                if self.buffer:
                    events = list(self.buffer)
                    self.buffer.clear()
                    return events
            if self.stop.is_set() or time.monotonic() >= deadline:
                return []
            self.stop.wait(0.1)

    def shutdown(self) -> None:
        if self.stop.is_set():
            return
        self.stop.set()
        try:
            call_op(
                {"v": PROTOCOL_VERSION, "op": "deregister", "agent_id": self.agent_id}, 2.0
            )
        except Exception:
            pass


# ----------------------------------------------------------------- MCP server


def tool(name: str, description: str, properties: dict, required: list) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": properties, "required": required},
    }


TOOLS = [
    tool(
        "channel_set_status",
        "Report this agent's status to the Telegram channel. Statuses: idle, "
        "thinking (also shows a typing indicator in the chat), waiting_user "
        "(you asked the user something in the chat and are waiting for their "
        "reply), waiting_approval (an approval card is pending), done, error. "
        "`detail` is a short free-text note shown next to the status.",
        {
            "status": {"type": "string", "enum": [
                "idle", "thinking", "waiting_user", "waiting_approval", "done", "error",
            ]},
            "detail": {"type": "string"},
        },
        ["status"],
    ),
    tool(
        "channel_send",
        "Send a markdown message to the Telegram chat where the user talks to "
        "this agent. Messages are tagged with the agent name. Works only after "
        "the user has messaged the bot at least once.",
        {"text": {"type": "string"}},
        ["text"],
    ),
    tool(
        "channel_request_approval",
        "Ask the user to approve an action via a Telegram card with Yes/No "
        "buttons. Returns approval_id; the verdict arrives as an approval_result "
        "event — set status waiting_approval and wait with channel_wait_events.",
        {
            "kind": {"type": "string", "description": "action kind, e.g. bash, write, deploy"},
            "summary": {"type": "string"},
            "details": {"type": "string", "description": "optional command/log excerpt"},
        },
        ["kind", "summary"],
    ),
    tool(
        "channel_wait_events",
        "Wait for events from the Telegram channel: user_message {cursor, "
        "chat_id, text}, approval_result {cursor, approval_id, approved, "
        "chat_id}, mode_set {cursor, mode, chat_id}. Returns buffered events "
        "immediately, otherwise blocks up to `timeout` seconds. Call it when "
        "actively waiting (waiting_user/waiting_approval); when idle, incoming "
        "messages are pushed into the session automatically as <channel> tags.",
        {"timeout": {"type": "number", "description": "seconds, default 25, max 120"}},
        [],
    ),
]


def require_str(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise BridgeError("argument %r is required and must be a non-empty string" % key)
    return value


def tools_call(params: dict, channel: Channel) -> dict:
    name = params.get("name")
    args = params.get("arguments") or {}
    not_ready = channel.await_ready(READY_WAIT)
    if not_ready is not None:
        raise BridgeError(not_ready)
    if channel.hook_mode:
        raise BridgeError(
            "this tmux session is served by the channel hooks: replies flow "
            "automatically when the turn ends and incoming messages are pasted "
            "into the pane — no MCP tools needed"
        )

    if name == "channel_set_status":
        frame = {
            "v": PROTOCOL_VERSION,
            "op": "status",
            "agent_id": channel.agent_id,
            "status": require_str(args, "status"),
        }
        if isinstance(args.get("detail"), str) and args["detail"]:
            frame["detail"] = args["detail"]
        resp = call_op(frame, OP_TIMEOUT)
    elif name == "channel_send":
        resp = call_op(
            {
                "v": PROTOCOL_VERSION,
                "op": "message",
                "agent_id": channel.agent_id,
                "text": require_str(args, "text"),
            },
            OP_TIMEOUT,
        )
    elif name == "channel_request_approval":
        frame = {
            "v": PROTOCOL_VERSION,
            "op": "approval_request",
            "agent_id": channel.agent_id,
            "kind": require_str(args, "kind"),
            "summary": require_str(args, "summary"),
        }
        if isinstance(args.get("details"), str) and args["details"]:
            frame["details"] = args["details"]
        resp = call_op(frame, OP_TIMEOUT)
    elif name == "channel_wait_events":
        timeout = args.get("timeout", 25.0)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            timeout = 25.0
        events = channel.wait_events(min(max(float(timeout), 0.0), 120.0))
        return {
            "content": [{"type": "text", "text": json.dumps({"events": events}, ensure_ascii=False)}]
        }
    else:
        raise BridgeError("unknown tool: %r" % name)

    if resp.get("ok") is not True:
        raise BridgeError("daemon rejected %s: %s" % (name, resp.get("error")))
    return {"content": [{"type": "text", "text": json.dumps(resp, ensure_ascii=False)}]}


def handle_message(msg: dict, channel: Channel) -> dict | None:
    if "id" not in msg:  # notification — nothing to answer
        return None
    method = msg.get("method")
    params = msg.get("params") or {}
    try:
        if method == "initialize":
            result = {
                "protocolVersion": params.get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {}},
                "instructions": INSTRUCTIONS,
                "serverInfo": {"name": "channel", "version": VERSION},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = tools_call(params, channel)
        else:
            return {
                "jsonrpc": "2.0",
                "id": msg["id"],
                "error": {"code": -32601, "message": "method not found: %r" % method},
            }
    except Exception as exc:
        result = {
            "content": [{"type": "text", "text": "error: %s" % exc}],
            "isError": True,
        }
    return {"jsonrpc": "2.0", "id": msg["id"], "result": result}


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s channel: %(message)s")
    channel = Channel()
    channel.start()
    atexit.register(channel.shutdown)

    def on_signal(_signum: int, _frame: object) -> None:
        channel.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        out = handle_message(msg, channel)
        if out is not None:
            channel.send(out)
    channel.shutdown()


if __name__ == "__main__":
    main()
