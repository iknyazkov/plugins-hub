"""The /cron feature: agent jobs on a schedule, run by the daemon itself.

Jobs live in ``cron.json`` next to ``state.json``. ``/cron add`` rides the
NL-command platform (``nlcmd.convert``): a throwaway tmux agent turns the
request into one ``/cron_put {...}`` line, the dispatcher hands it here,
the job is validated and stored immediately. When a job fires, the daemon
spawns its own throwaway tmux agent session (``cron_<suffix>-<n>``),
pastes the job's prompt, captures the reply as a service agent and sends
it — the full text, the provider chunks it for Telegram — to the chat that
created the job. Runs missed while the daemon was down are never replayed;
overlap and the global run cap are skipped with a chat note. Run sessions
left over from a crashed daemon are killed on startup: ``cron_<6 hex>-``
is scheduler-only naming, user sessions like ``cron`` never match.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from channelsrv import delivery, protocol
from channelsrv.config import base_dir
from channelsrv.features.nlcmd import agent_command
from channelsrv.server import Core

log = logging.getLogger(__name__)

TICK_SECONDS = 20.0  # the loop never skips a minute, it may fire up to a tick late
REGISTER_WAIT = 90.0  # tmux spawn + claude startup + SessionStart hook
DEFAULT_RUN_TIMEOUT = 600.0
ENV_RUN_TIMEOUT = "CHANNEL_CRON_TIMEOUT"
RUN_CAP = 3
JOB_FILE = "cron.json"
PROMPT_MAX = 8000

#: scheduler-owned run sessions; the 6-hex suffix is never user-chosen
ORPHAN_RE = re.compile(r"^cron_[0-9a-f]{6}-")
NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

CONVERT_PROMPT = """\
Convert the user's scheduling request into a channelsrv cron job.
Answer with EXACTLY ONE line and nothing else:
/cron_put {"name": "<english-kebab-case-name>", "cron": "<5-field cron>", "prompt": "<task>", "cwd": "<dir>", "agent": "<cmd>"}
- name: short, English, kebab-case (lowercase words joined by -)
- ONE schedule key, exactly one of the two:
  - cron: standard 5-field cron expression, digits only (no month/day names), machine-local \
time — for recurring requests
  - at: ISO local datetime "YYYY-MM-DDTHH:MM" — for one-shot requests (relative like \
"через 5 минут" or absolute like "сегодня в 18:00"); resolve them against the current local \
time given after this prompt. Never use cron for one-shot requests.
- prompt: the complete task for the agent that will run it. The job starts a fresh agent \
session that knows nothing: spell out everything needed (what to do, where, what to report). \
Keep the user's language in the prompt.
- cwd: absolute directory of the named project — include ONLY if the request names one; \
otherwise omit the key
- agent: the launch command or shell alias for the agent binary — include ONLY if the \
request names one (e.g. "alias запуска zaiclaude", "launch via zclaude"); otherwise omit \
the key
If the schedule or the task is ambiguous, reply with a short clarifying question in plain \
words instead (it goes to the user as is).\
"""

#: one parsed cron field: allowed values + whether the field restricts the day (``*`` = not)
Field = tuple[set[int], bool]
ParsedCron = tuple[Field, Field, Field, Field, Field]

CRON_RANGES: tuple[tuple[int, int], ...] = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


# ------------------------------------------------------------------- cron math


def _parse_field(raw: str, lo: int, hi: int) -> Field | None:
    """One cron field -> allowed values + is-it-restricted; None = invalid."""
    values: set[int] = set()
    for part in raw.split(","):
        base, slash, step_raw = part.partition("/")
        if slash and not step_raw.isdigit():
            return None
        step = int(step_raw) if slash else 1
        if step < 1:
            return None
        if base == "*":
            start, end = lo, hi
        elif base.isdigit():
            start = end = int(base)
        elif "-" in base:
            a, _, b = base.partition("-")
            if not (a.isdigit() and b.isdigit()):
                return None
            start, end = int(a), int(b)
            if start > end:
                return None
        else:
            return None
        if start < lo or end > hi:
            return None
        values.update(range(start, end + 1, step))
    return values, "*" not in raw


def parse_cron(expr: str) -> ParsedCron | None:
    """A 5-field numeric cron expression, None if invalid. Day-of-week 7 == 0."""
    fields = expr.split()
    if len(fields) != 5:
        return None
    out: list[Field] = []
    for raw, (lo, hi) in zip(fields, CRON_RANGES, strict=True):
        field = _parse_field(raw, lo, hi)
        if field is None:
            return None
        values, restricted = field
        if hi == 7:  # day of week: 0 and 7 both mean Sunday
            values = {0 if v == 7 else v for v in values}
        out.append((values, restricted))
    return out[0], out[1], out[2], out[3], out[4]


def cron_matches(parsed: ParsedCron, now: datetime) -> bool:
    """Classic cron semantics: dom/dow both restricted -> either may match."""
    (minutes, _), (hours, _), (doms, dom_rest), (months, _), (dows, dow_rest) = parsed
    if now.minute not in minutes or now.hour not in hours or now.month not in months:
        return False
    dom_ok = now.day in doms
    dow_ok = (now.weekday() + 1) % 7 in dows
    if dom_rest and dow_rest:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def next_fire(parsed: ParsedCron, now: datetime) -> datetime | None:
    """The next matching minute strictly after ``now`` (scan day by day)."""
    (minutes, _), (hours, _), (doms, dom_rest), (months, _), (dows, dow_rest) = parsed
    for offset in range(1461):  # four years, leap days included
        day = now.date() + timedelta(days=offset)
        if day.month not in months:
            continue
        dom_ok = day.day in doms
        dow_ok = (day.weekday() + 1) % 7 in dows
        day_ok = (dom_ok or dow_ok) if (dom_rest and dow_rest) else (dom_ok and dow_ok)
        if not day_ok:
            continue
        for hour in sorted(hours):
            if offset == 0 and hour < now.hour:
                continue
            for minute in sorted(minutes):
                if offset == 0 and hour == now.hour and minute <= now.minute:
                    continue
                return datetime(
                    day.year, day.month, day.day, hour, minute, tzinfo=now.tzinfo
                )
    return None


def _local_now() -> datetime:
    return datetime.now().astimezone()


# ------------------------------------------------------------------- job model


@dataclass
class CronJob:
    name: str  # kebab-case slug from the converter
    suffix: str  # 6 hex chars; id = name-suffix, unique by construction
    prompt: str
    cwd: str
    chat_id: int
    created_at: str
    cron: str = ""  # 5-field expression; empty for one-shot jobs
    last_run: dict[str, Any] | None = None  # {"at": iso, "status": ..., "error": ...}
    agent: str = ""  # launch command/alias for runs; empty = CHANNEL_AI_AGENT default
    at: str = ""  # one-shot ISO local datetime; empty = recurring (cron)

    @property
    def id(self) -> str:
        return f"{self.name}-{self.suffix}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "suffix": self.suffix,
            "cron": self.cron,
            "prompt": self.prompt,
            "cwd": self.cwd,
            "chat_id": self.chat_id,
            "created_at": self.created_at,
            "last_run": self.last_run,
            "agent": self.agent,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> CronJob | None:
        """Tolerant read: anything malformed yields None and the entry is skipped."""
        if not isinstance(data, dict):
            return None
        if not all(
            isinstance(data.get(key), str)
            for key in ("name", "suffix", "cron", "prompt", "cwd", "created_at")
        ):
            return None
        chat_id = data.get("chat_id")
        if not isinstance(chat_id, int):
            return None
        last_run = data.get("last_run")
        if last_run is not None and not isinstance(last_run, dict):
            return None
        agent = data.get("agent", "")
        at = data.get("at", "")
        if not isinstance(agent, str) or not isinstance(at, str):
            return None
        return cls(
            name=data["name"],
            suffix=data["suffix"],
            cron=data["cron"],
            prompt=data["prompt"],
            cwd=data["cwd"],
            chat_id=chat_id,
            created_at=data["created_at"],
            last_run=last_run,
            agent=agent,
            at=at,
        )


class JobStore:
    """Jobs in one JSON file; atomic writes; a broken file starts empty."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._jobs: dict[str, CronJob] = {}

    def load(self) -> None:
        try:
            raw = json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        entries = raw.get("jobs") if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            return
        for key, entry in entries.items():
            job = CronJob.from_dict(entry)
            if job is not None and job.id == key:
                self._jobs[key] = job

    def jobs(self) -> list[CronJob]:
        return sorted(self._jobs.values(), key=lambda job: job.id)

    def add(self, job: CronJob) -> None:
        self._jobs[job.id] = job
        self._save()

    def remove(self, job_id: str) -> bool:
        if self._jobs.pop(job_id, None) is None:
            return False
        self._save()
        return True

    def set_last_run(self, job_id: str, entry: dict[str, Any]) -> None:
        job = self._jobs.get(job_id)
        if job is not None:
            job.last_run = entry
            self._save()

    def _save(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        payload = {job.id: job.to_dict() for job in self._jobs.values()}
        tmp.write_text(json.dumps({"jobs": payload}, ensure_ascii=False, indent=2))
        tmp.replace(self._path)


# -------------------------------------------------------------------- feature


class Feature:
    """Registers ``/cron`` (list/add/remove/run) and the internal ``cron_put``."""

    def __init__(
        self,
        convert: Callable[[int, str, str], Awaitable[None]] | None = None,
        new_session: Callable[[str, str, str], Awaitable[None]] | None = None,
        close_session: Callable[[str], Awaitable[None]] | None = None,
        list_sessions: Callable[[], Awaitable[list[str]]] | None = None,
        session_exists: Callable[[str], Awaitable[bool]] | None = None,
        store: JobStore | None = None,
        now: Callable[[], datetime] | None = None,
        run_timeout: float | None = None,
    ) -> None:
        self._convert = convert  # nlcmd's NL -> /cron_put converter (features/__init__)
        self._new = new_session or delivery.new_session
        self._close = close_session or delivery.close_session
        self._sessions = list_sessions or delivery.session_names
        self._exists = session_exists or delivery.session_exists
        self._store = store
        self._now = now or _local_now
        self._timeout_override = run_timeout
        self._seq = itertools.count(1)
        self._runs: dict[str, asyncio.Task[None]] = {}  # job id -> its live run
        self._core: Core

    @property
    def _timeout(self) -> float:
        """Lazy so a ``.env``-sourced CHANNEL_CRON_TIMEOUT (applied at startup) wins."""
        if self._timeout_override is not None:
            return self._timeout_override
        return _timeout_from_env()

    def register(self, core: Core) -> None:
        self._core = core
        if self._store is None:
            self._store = JobStore(base_dir() / JOB_FILE)
        self._store.load()
        core.add_command("cron", self._command)
        core.add_command("cron_put", self._cron_put)

    def background(self) -> Coroutine[Any, Any, None]:
        """The scheduler loop, run as a task by the entry point (main.py)."""
        return self._loop_forever()

    # ------------------------------------------------------------- commands

    async def _command(self, chat_id: int, rest: str) -> None:
        verb, _, arg = rest.strip().partition(" ")
        verb, arg = verb.lower(), arg.strip()
        if verb == "list":
            await self._list(chat_id)
        elif verb == "add":
            await self._add(chat_id, arg)
        elif verb == "remove":
            await self._remove(chat_id, arg)
        elif verb == "run":
            await self._run_cmd(chat_id, arg)
        else:
            await self._usage(chat_id)

    async def _usage(self, chat_id: int) -> None:
        await self._core.provider.send_text(
            chat_id,
            "Usage:\n"
            "/cron list — all jobs\n"
            "/cron add <what the agent should do and when, plain words>\n"
            "/cron remove <id or its unambiguous prefix>\n"
            "/cron run <id or its unambiguous prefix>\n\n"
            "A job fires in a throwaway tmux agent session; the full reply lands here.\n"
            "Recurring requests get a cron schedule; one-shot ones («через 5 минут»,\n"
            "«сегодня в 18:00») run once and remove themselves.\n"
            "Example:\n\n"
            "/cron add every morning at 9 check the build in ~/work/api and report",
        )

    async def _add(self, chat_id: int, arg: str) -> None:
        if not arg:
            await self._core.provider.send_text(
                chat_id,
                "Usage: /cron add <what the agent should do and how often>\n\n"
                "Example:\n/cron add every workday at 18 summarize today's commits",
            )
            return
        if self._convert is None:
            await self._core.provider.send_text(chat_id, "NL conversion is unavailable.")
            return
        now = self._now()
        await self._convert(
            chat_id,
            f"{CONVERT_PROMPT}\n\nCurrent local time: {now:%Y-%m-%d %H:%M %Z} ({now:%A})",
            arg,
        )

    async def _cron_put(self, chat_id: int, rest: str) -> None:
        """The converter's structured reply; validates and stores the job."""
        core = self._core
        try:
            data = json.loads(rest)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            await core.provider.send_text(
                chat_id,
                f"Job rejected: converter reply is not valid JSON.\nIt said: `{rest[:300]}`",
            )
            return

        name = data.get("name")
        if not isinstance(name, str) or len(name) > 40 or not NAME_RE.fullmatch(name):
            await self._reject(chat_id, rest, "name must be kebab-case, latin, <= 40 chars")
            return
        raw_cron = data.get("cron")
        raw_at = data.get("at")
        if raw_cron is None and raw_at is None:
            await self._reject(chat_id, rest, "exactly one of cron or at is required")
            return
        if raw_cron is not None and raw_at is not None:
            await self._reject(chat_id, rest, "cron and at are mutually exclusive")
            return
        parsed: ParsedCron | None = None
        at_dt: datetime | None = None
        cron = ""
        if raw_cron is not None:
            if not isinstance(raw_cron, str) or parse_cron(raw_cron) is None:
                await self._reject(chat_id, rest, "cron must be a valid 5-field numeric expression")
                return
            cron = raw_cron
            parsed = parse_cron(cron)
            assert parsed is not None
        else:
            if not isinstance(raw_at, str) or _parse_iso(raw_at.strip()) is None:
                await self._reject(
                    chat_id, rest, 'at must be an ISO local datetime "YYYY-MM-DDTHH:MM"'
                )
                return
            at_dt = _parse_iso(raw_at.strip())
            assert at_dt is not None
            if at_dt < self._now() - timedelta(seconds=60):
                await self._reject(chat_id, rest, f"at is in the past: {raw_at!r}")
                return
        prompt = data.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            await self._reject(chat_id, rest, "prompt must be a non-empty string")
            return
        if len(prompt) > PROMPT_MAX:
            await self._reject(chat_id, rest, f"prompt exceeds {PROMPT_MAX} chars")
            return
        raw_cwd = data.get("cwd")
        if raw_cwd is None:
            cwd = self._default_cwd(chat_id)
        elif isinstance(raw_cwd, str):
            cwd = os.path.expanduser(raw_cwd)
            if not os.path.isabs(cwd) or not os.path.isdir(cwd):
                await self._reject(chat_id, rest, f"cwd is not an existing absolute dir: {raw_cwd!r}")
                return
        else:
            await self._reject(chat_id, rest, "cwd must be a string")
            return
        raw_agent = data.get("agent")
        agent = ""
        if raw_agent is not None:
            if not isinstance(raw_agent, str):
                await self._reject(chat_id, rest, "agent must be a string")
                return
            agent = raw_agent.strip()
            if not agent:
                agent = ""
            elif "\n" in agent or len(agent) > 200:
                await self._reject(chat_id, rest, "agent must be a single line, <= 200 chars")
                return

        now = self._now()
        job = CronJob(
            name=name,
            suffix=os.urandom(3).hex(),
            prompt=prompt.strip(),
            cwd=cwd,
            chat_id=chat_id,
            created_at=now.isoformat(timespec="seconds"),
            cron=cron,
            agent=agent,
            at=at_dt.isoformat(timespec="minutes") if at_dt is not None else "",
        )
        assert self._store is not None
        self._store.add(job)
        agent_text = f" · via `{job.agent}`" if job.agent else ""
        if job.at:
            when_text = f"fires at {at_dt:%a %d %H:%M}" if at_dt is not None else "one-shot"
            head = f"at `{job.at}`"
        else:
            nxt = next_fire(parsed, now) if parsed is not None else None
            when_text = f"next {nxt:%a %d %H:%M}" if nxt is not None else "next never"
            head = f"cron `{job.cron}`"
        kind = "one-shot" if job.at else "recurring"
        await core.provider.send_text(
            chat_id,
            f"Created `{job.id}` ({kind})\n"
            f"{head}{agent_text} · {when_text} · cwd `{job.cwd}`\n"
            f"prompt: {job.prompt[:200]}{'…' if len(job.prompt) > 200 else ''}",
        )

    async def _reject(self, chat_id: int, rest: str, reason: str) -> None:
        await self._core.provider.send_text(
            chat_id, f"Job rejected: {reason}.\nConverter said: `{rest[:300]}`"
        )

    def _default_cwd(self, chat_id: int) -> str:
        """The active agent's project dir when there is one, HOME otherwise."""
        agent = self._core.active_agent(chat_id)
        if agent is not None and agent.session_name and not agent.service:
            return agent.session_name
        return str(Path.home())

    async def _list(self, chat_id: int) -> None:
        assert self._store is not None
        jobs = self._store.jobs()
        if not jobs:
            await self._core.provider.send_text(
                chat_id, "No jobs. Add one: /cron add <what and how often, plain words>"
            )
            return
        now = self._now()
        blocks: list[str] = []
        for job in jobs:
            agent_text = f" · via `{job.agent}`" if job.agent else ""
            if job.at:
                at_dt = _parse_iso(job.at)
                when_text = f"fires {at_dt:%a %d %H:%M}" if at_dt is not None else f"fires {job.at}"
                head = f"`{job.id}` ({when_text}){agent_text}"
            else:
                parsed = parse_cron(job.cron)
                nxt = next_fire(parsed, now) if parsed is not None else None
                nxt_text = f"{nxt:%a %d %H:%M}" if nxt is not None else "never"
                head = f"`{job.id}` · `{job.cron}`{agent_text} · next {nxt_text}"
            if job.last_run is not None:
                at = _parse_iso(job.last_run.get("at"))
                at_text = f"{at:%d %H:%M}" if at is not None else "?"
                status = str(job.last_run.get("status", "?"))
                last_text = f"last {status} {at_text}"
            else:
                last_text = "last —"
            preview = job.prompt[:60] + ("…" if len(job.prompt) > 60 else "")
            blocks.append(f"{head} · {last_text}\n{preview}")
        await self._core.provider.send_text(chat_id, f"{len(jobs)} job(s):\n\n" + "\n\n".join(blocks))

    async def _remove(self, chat_id: int, arg: str) -> None:
        if not arg:
            await self._core.provider.send_text(chat_id, "Usage: /cron remove <id or prefix>")
            return
        job, error = self._resolve(arg)
        if job is None:
            await self._core.provider.send_text(chat_id, error)
            return
        task = self._runs.pop(job.id, None)
        if task is not None:
            task.cancel()
        assert self._store is not None
        self._store.remove(job.id)
        suffix = " (run cancelled)" if task is not None else ""
        await self._core.provider.send_text(chat_id, f"Removed `{job.id}`{suffix}.")

    async def _run_cmd(self, chat_id: int, arg: str) -> None:
        if not arg:
            await self._core.provider.send_text(chat_id, "Usage: /cron run <id or prefix>")
            return
        job, error = self._resolve(arg)
        if job is None:
            await self._core.provider.send_text(chat_id, error)
            return
        if job.id in self._runs:
            await self._core.provider.send_text(chat_id, f"`{job.id}` is already running.")
            return
        if len(self._runs) >= RUN_CAP:
            await self._core.provider.send_text(
                chat_id, f"Run cap reached ({RUN_CAP} active) — try again later."
            )
            return
        self._start(job)
        await self._core.provider.send_text(
            chat_id, f"Started `{job.id}` — the result lands in the job's chat."
        )

    # ------------------------------------------------------------- scheduler

    async def _loop_forever(self) -> None:
        await self._kill_orphans()
        last_minute = ""
        while True:
            await asyncio.sleep(TICK_SECONDS)
            now = self._now()
            key = f"{now:%Y%m%d%H%M}"
            if key == last_minute:
                continue
            last_minute = key
            try:
                await self._tick(now)
            except Exception:  # the loop must survive a bad tick
                log.exception("cron tick failed")

    async def _tick(self, now: datetime) -> None:
        assert self._store is not None
        for job in self._store.jobs():
            if job.at:
                at_dt = _parse_iso(job.at)
                if at_dt is None or now < at_dt:
                    continue  # one-shot not due yet
            else:
                parsed = parse_cron(job.cron)
                if parsed is None or not cron_matches(parsed, now):
                    continue
            if job.id in self._runs:
                if job.at:
                    continue  # already running — it removes itself when done
                note = f"[cron] {job.name}: skipped this occurrence — previous run still going"
            elif len(self._runs) >= RUN_CAP:
                if job.at:
                    note = (
                        f"[cron] {job.name}: waiting for a free slot "
                        f"({RUN_CAP} runs active) — one-shot stays due"
                    )
                else:
                    note = f"[cron] {job.name}: skipped this occurrence — {RUN_CAP} runs already active"
            else:
                self._start(job)
                continue
            with contextlib.suppress(Exception):
                await self._core.provider.send_text(job.chat_id, note)

    def _start(self, job: CronJob) -> None:
        self._runs[job.id] = asyncio.create_task(self._run_once(job))

    async def _run_once(self, job: CronJob) -> None:
        core = self._core
        session = f"cron_{job.suffix}-{next(self._seq)}"
        started = time.monotonic()
        session_up = False
        try:
            try:
                await self._new(session, job.agent or agent_command(), job.cwd)
                session_up = True
            except Exception as exc:  # noqa: BLE001 — answerable, not fatal
                await self._report(job, "failed", error=f"spawn failed: {exc}", started=started)
                return
            agent = await self._wait_registered(session)
            if agent is None or not agent.tmux_target:
                error = "run session did not register in time"
                if agent is None and not await self._exists(session):
                    error = (
                        "run session died immediately — the agent command is "
                        "failing (check CHANNEL_AI_AGENT)"
                    )
                await self._report(job, "failed", error=error, started=started)
                return
            agent.service = True
            await core.deliver(agent.tmux_target, job.prompt)
            reply = await self._wait_reply(agent)
            if reply is None:
                await self._report(
                    job,
                    "timeout",
                    error=f"no reply in {_fmt_duration(self._timeout)} — session killed",
                    started=started,
                )
                return
            await self._report(job, "ok", reply=reply, started=started)
        finally:
            self._runs.pop(job.id, None)
            if session_up:
                with contextlib.suppress(Exception):
                    await self._close(session)
            if job.at:
                assert self._store is not None
                self._store.remove(job.id)  # one-shot: gone after its single attempt

    async def _report(
        self,
        job: CronJob,
        status: str,
        *,
        reply: str | None = None,
        error: str | None = None,
        started: float,
    ) -> None:
        duration = _fmt_duration(time.monotonic() - started)
        assert self._store is not None
        self._store.set_last_run(
            job.id, {"at": self._now().isoformat(timespec="seconds"), "status": status, "error": error}
        )
        if reply is not None:
            text = f"[cron] {job.name} · {status} · {duration}\n\n{reply}"
        else:
            text = f"[cron] {job.name} · {status} · {duration}\n{error or ''}"
        with contextlib.suppress(Exception):  # delivery is best-effort; last_run is kept
            await self._core.provider.send_text(job.chat_id, text)

    async def _wait_registered(self, session: str) -> Any:
        """Waits for the spawned session's hook to register (name starts with the session)."""
        deadline = time.monotonic() + REGISTER_WAIT
        while time.monotonic() < deadline:
            for agent in self._core.registry.snapshot():
                if agent.name.startswith(session + "-") and agent.tmux_target:
                    return agent
            if not await self._exists(session):
                return None  # the pane died instantly — no point waiting out the clock
            await asyncio.sleep(0.5)
        return None

    async def _wait_reply(self, agent: Any) -> str | None:
        deadline = time.monotonic() + self._timeout
        cursor = 0
        while time.monotonic() < deadline:
            for event_cursor, event in agent.events_after(cursor):
                cursor = max(cursor, int(event_cursor))
                if event.get("type") == protocol.EVENT_MESSAGE:
                    return str(event.get("text", ""))
            agent.wakeup.clear()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(agent.wakeup.wait(), timeout=remaining)
            except TimeoutError:
                return None
        return None

    async def _kill_orphans(self) -> None:
        """Run sessions from a previous daemon life are orphans by definition."""
        try:
            names = await self._sessions()
        except Exception:  # no tmux reachable: nothing to sweep
            log.debug("cron orphan sweep skipped", exc_info=True)
            return
        for name in names:
            if not ORPHAN_RE.match(name):
                continue
            try:
                await self._close(name)
                log.info("cron: killed orphaned run session %s", name)
            except Exception:  # a failed kill is logged; the pane dies eventually
                log.debug("cron: orphan close failed for %s", name, exc_info=True)

    # --------------------------------------------------------------- helpers

    def _resolve(self, arg: str) -> tuple[CronJob | None, str]:
        """An id or its unique prefix -> (job, "") or (None, error message)."""
        assert self._store is not None
        matches = [job for job in self._store.jobs() if job.id.startswith(arg)]
        if not matches:
            return None, f"No job matches `{arg}`."
        if len(matches) > 1:
            shown = ", ".join(f"`{job.id}`" for job in matches[:6])
            more = "…" if len(matches) > 6 else ""
            return None, f"Ambiguous prefix `{arg}`: {shown}{more}"
        return matches[0], ""


def _timeout_from_env() -> float:
    raw = os.environ.get(ENV_RUN_TIMEOUT)
    if raw:
        with contextlib.suppress(ValueError):
            return float(raw)
    return DEFAULT_RUN_TIMEOUT


def _fmt_duration(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    return f"{total // 60}m{total % 60:02d}s"


def _parse_iso(raw: Any) -> datetime | None:
    """Parses ISO local datetime; naive values are read as machine-local time."""
    if not isinstance(raw, str):
        return None
    with contextlib.suppress(ValueError):
        dt = datetime.fromisoformat(raw)
        return dt.astimezone() if dt.tzinfo is None else dt
    return None
