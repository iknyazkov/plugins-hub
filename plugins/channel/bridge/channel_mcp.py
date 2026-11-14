#!/usr/bin/env python3
"""UDS client + daemon lifecycle for channelsrv, shared by the hook shim.

The session joins the daemon through the hook shim (hooks/channel-hook),
which imports this module for the transport and for ``ensure_daemon``:
healthz probe -> (under a flock) grace period for a slow daemon -> stop only
a truly wedged daemon -> spawn `uv run channelsrv`.

Daemon protocol: one UDS connection = one JSONL frame, see channelsrv/README.md.
"""

from __future__ import annotations

from datetime import datetime
import fcntl
import json
import logging
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

PROTOCOL_VERSION = 1

HEALTHZ_BUDGET = 2.5  # seconds; a healthy daemon answers in milliseconds
GRACE_BUDGET = 8.0  # a live daemon gets this long to answer before it is called wedged
EVENTS_SOCKET_TIMEOUT = 35.0  # socket budget > server long-poll
OP_TIMEOUT = 35.0  # message/approval ops may take a Telegram roundtrip
SPAWN_DEADLINE = 120.0  # how long the spawning side waits for healthz

FRAME_LIMIT = 2**20
SRV_DIR = Path(__file__).resolve().parent.parent / "channelsrv"

log = logging.getLogger("channel")


def home_dir() -> Path:
    return Path(os.environ.get("CHANNELSRV_HOME", str(Path.home() / ".config" / "channelsrv")))


def sock_path() -> Path:
    return home_dir() / "server.sock"


class BridgeError(RuntimeError):
    """A failure worth surfacing to the caller."""


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

    The hook shim and the daemon are separate processes; without this a kill
    ordered by ensure_daemon is invisible in server.log (the SIGTERM handler's
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
            # another caller is spawning right now — just wait for the result
            deadline = time.monotonic() + SPAWN_DEADLINE
            while time.monotonic() < deadline:
                if healthy(HEALTHZ_BUDGET):
                    return
                time.sleep(0.25)
            raise BridgeError(
                "another hook is starting the daemon and it did not come up "
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
