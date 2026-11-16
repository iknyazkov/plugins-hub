"""The single outgoing pipe: every user-bound message goes through here.

Producers enqueue and never wait; one worker delivers in per-lane FIFO
order. Two lanes:

* ``instant`` — replies to user actions (commands, buttons): drained first.
* ``normal``  — agent chatter: final replies, progress chains, scheduled
  cron output.

Retry policy per item: five fast attempts with a 1→2→4→8→16 s backoff,
then a slow lane retrying every 60 s forever — delivery outranks latency.
Known-permanent rejections (a message of ours the API will never accept)
are dropped with an ERROR log instead of retried. The queue is
deliberately in-memory: it dies with the daemon, and a restart loses
whatever was still waiting — accepted by design.

``typing`` (chat_action) never queues: a delayed typing indicator would
lie about "the agent is typing right now", so it is sent best-effort and
suppressed entirely while anything is waiting.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from channelsrv.providers.base import CardButton, MessageId, MsgRef, Provider

log = logging.getLogger(__name__)

#: sleeps after failed attempts 1..5; everything after is the slow lane
FAST_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)
SLOW_DELAY = 60.0
#: a non-empty queue older than this gets one warning per dry spell
STALL_WARN_AFTER = 30.0

LANE_INSTANT = "instant"
LANE_NORMAL = "normal"

Outcome = str  # "ok" | "wait" | "dead"


@dataclass(eq=False)  # identity semantics: deque.remove must hit this very item
class _Item:
    call: str  # "send_text" | "send_card" | "edit_text"
    chat_id: int
    text: str
    lane: str = LANE_NORMAL
    agent_name: str | None = None
    buttons: tuple[tuple[CardButton, ...], ...] = ()
    message_id: MessageId | None = None  # edit_text target
    ref: MsgRef | None = None  # send_card: who to tell the new id
    enqueued_at: float = 0.0  # set by _enqueue on the outbox's own clock
    attempts: int = 0
    next_at: float = 0.0


class Outbox:
    """In-memory two-lane FIFO with a single delivering worker.

    ``now``/``sleep`` are injectable so tests drive the clock instead of
    waiting on real backoffs.
    """

    def __init__(
        self,
        provider: Provider,
        *,
        permanent: tuple[type[Exception], ...] = (),
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._provider = provider
        self._permanent = permanent
        self._now = now
        self._sleep = sleep
        self._lanes: dict[str, deque[_Item]] = {
            LANE_INSTANT: deque(),
            LANE_NORMAL: deque(),
        }
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stall_warned = False

    # ------------------------------------------------------------ producers

    @property
    def depth(self) -> int:
        return sum(len(lane) for lane in self._lanes.values())

    @property
    def empty(self) -> bool:
        return self.depth == 0

    def _enqueue(self, item: _Item) -> MsgRef | None:
        item.enqueued_at = item.next_at = self._now()
        self._lanes[item.lane].append(item)
        self._wake.set()
        return item.ref

    def send_text(
        self,
        chat_id: int,
        markdown: str,
        agent_name: str | None = None,
        instant: bool = False,
    ) -> None:
        self._enqueue(
            _Item(
                call="send_text",
                chat_id=chat_id,
                text=markdown,
                lane=LANE_INSTANT if instant else LANE_NORMAL,
                agent_name=agent_name,
            )
        )

    def send_card(
        self,
        chat_id: int,
        markdown: str,
        buttons: Sequence[Sequence[CardButton]],
        instant: bool = False,
    ) -> MsgRef:
        ref = MsgRef()
        self._enqueue(
            _Item(
                call="send_card",
                chat_id=chat_id,
                text=markdown,
                lane=LANE_INSTANT if instant else LANE_NORMAL,
                buttons=tuple(tuple(row) for row in buttons),
                ref=ref,
            )
        )
        return ref

    def edit_text(
        self, chat_id: int, message_id: MessageId, markdown: str, instant: bool = False
    ) -> None:
        self._enqueue(
            _Item(
                call="edit_text",
                chat_id=chat_id,
                text=markdown,
                lane=LANE_INSTANT if instant else LANE_NORMAL,
                message_id=message_id,
            )
        )

    # ------------------------------------------------------------- worker

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="outbox")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def flush(self) -> None:
        """One immediate attempt per waiting item — the pre-exit drain.

        Used by /restart: the queue dies with the process seconds later, so
        every item gets a single no-backoff shot now; what still fails is
        lost with the restart (accepted — the queue is in-memory).
        Single deliverer, always: the worker is stopped first — flush must
        never race it for an item (2026-11-16: 'Restarting the daemon'
        arrived twice, then the worker crashed on a double _remove). The
        worker stays stopped; a live caller restarts it via start().
        """
        await self.stop()
        for lane in (LANE_INSTANT, LANE_NORMAL):
            for item in list(self._lanes[lane]):
                try:
                    outcome = await self._attempt(item)
                except Exception as exc:  # noqa: BLE001 — flush is best-effort by design
                    if self._permanent and isinstance(exc, self._permanent):
                        self._drop(
                            item,
                            f"permanent rejection: {exc.__class__.__name__}: {exc}",
                        )
                    else:
                        log.warning(
                            "outbox: flush attempt failed: %s: %s",
                            exc.__class__.__name__,
                            exc,
                        )
                    continue
                if outcome == "ok":
                    self._remove(item)
                # "wait"/"dead" stay queued — moot once the process exits

    async def _run(self) -> None:
        while True:
            item = self._pick_due()
            if item is not None:
                await self._deliver(item)
                continue
            self._check_stall()
            await self._wait_for_work()

    def _pick_due(self, ignore_delay: bool = False) -> _Item | None:
        now = self._now()
        for lane in (LANE_INSTANT, LANE_NORMAL):  # user actions outrank chatter
            items = self._lanes[lane]
            if items and (ignore_delay or items[0].next_at <= now):
                return items[0]
        return None

    async def _deliver(self, item: _Item, ignore_delay: bool = False) -> None:
        try:
            outcome = await self._attempt(item)
        except Exception as exc:  # noqa: BLE001 — any failure retries or drops, the worker lives on
            if self._permanent and isinstance(exc, self._permanent):
                self._drop(
                    item, f"permanent rejection: {exc.__class__.__name__}: {exc}"
                )
                return
            self._retry(item, exc, ignore_delay)
            return
        if outcome == "dead":
            self._remove(item)
            log.debug("outbox: edit of a dead message dropped (%s)", item.text[:60])
            return
        if outcome == "wait":  # the target create is still in flight
            item.next_at = self._now() + 1.0
            return
        self._remove(item)
        if self.empty:
            self._stall_warned = False

    async def _attempt(self, item: _Item) -> Outcome:
        if item.call == "send_text":
            await self._provider.send_text(
                item.chat_id, item.text, agent_name=item.agent_name
            )
            return "ok"
        if item.call == "send_card":
            mid = await self._provider.send_card(item.chat_id, item.text, item.buttons)
            if item.ref is not None:
                item.ref.resolve(mid if isinstance(mid, int) else None)
            return "ok"
        mid = item.message_id
        if mid is None:
            return "dead"  # an edit with no target — enqueue-side bug, drop it
        if isinstance(mid, MsgRef):
            if not mid.resolved:
                return "wait"
            value = mid.value
            if value is None:
                return "dead"  # the create was dropped — nothing to edit
            mid = value
        await self._provider.edit_text(item.chat_id, mid, item.text)
        return "ok"

    def _remove(self, item: _Item) -> None:
        # identity-based (eq=False above): exact item, head or not
        self._lanes[item.lane].remove(item)

    def _drop(self, item: _Item, why: str) -> None:
        self._remove(item)
        if item.ref is not None:
            item.ref.resolve(None)
        log.error("outbox: item dropped — %s; text: %r", why, item.text[:120])

    def _retry(self, item: _Item, exc: Exception, ignore_delay: bool) -> None:
        item.attempts += 1
        delay = (
            1.0
            if ignore_delay
            else (
                FAST_DELAYS[item.attempts - 1]
                if item.attempts <= len(FAST_DELAYS)
                else SLOW_DELAY
            )
        )
        item.next_at = self._now() + delay
        log.warning(
            "outbox: send failed (attempt %d, retry in %.0fs): %s: %s",
            item.attempts,
            delay,
            exc.__class__.__name__,
            exc,
        )

    def _check_stall(self) -> None:
        if self.empty or self._stall_warned:
            return
        heads = [lane[0] for lane in self._lanes.values() if lane]
        oldest = min(item.enqueued_at for item in heads)
        waited = self._now() - oldest
        if waited >= STALL_WARN_AFTER:
            self._stall_warned = True
            log.warning(
                "outbox stuck: %d item(s) waiting, oldest for %.0fs", self.depth, waited
            )

    async def _wait_for_work(self) -> None:
        next_due = min(
            (lane[0].next_at for lane in self._lanes.values() if lane), default=None
        )
        if next_due is None:
            self._wake.clear()
            await self._wake.wait()
            return
        timeout = max(0.0, next_due - self._now())
        self._wake.clear()
        with_suppress = asyncio.wait_for(self._wake.wait(), timeout)
        try:
            await with_suppress
        except TimeoutError:
            pass


class OutboxProvider:
    """Provider-shaped door that enqueues instead of sending.

    Everything the core and features send becomes an outbox item; the
    wrapped provider only ever talks to the real messenger from the worker.
    """

    def __init__(
        self, inner: Provider, permanent: tuple[type[Exception], ...] = ()
    ) -> None:
        self._inner = inner
        self._life = inner  # same object; typed Any for the lifecycle surface
        #: errors the messenger will reject forever — drop, don't retry
        self.outbox = Outbox(inner, permanent=permanent)

    # -- the Provider surface --------------------------------------------

    async def send_text(
        self,
        chat_id: int,
        markdown: str,
        agent_name: str | None = None,
        instant: bool = False,
    ) -> None:
        self.outbox.send_text(chat_id, markdown, agent_name=agent_name, instant=instant)

    async def send_card(
        self,
        chat_id: int,
        markdown: str,
        buttons: Sequence[Sequence[CardButton]],
        instant: bool = False,
    ) -> MsgRef:
        return self.outbox.send_card(chat_id, markdown, buttons, instant=instant)

    async def edit_text(
        self, chat_id: int, message_id: MessageId, markdown: str, instant: bool = False
    ) -> None:
        self.outbox.edit_text(chat_id, message_id, markdown, instant=instant)

    async def chat_action(self, chat_id: int) -> None:
        if not self.outbox.empty:  # a stale typing indicator would lie
            return
        try:
            await self._inner.chat_action(chat_id)
        except Exception:
            log.debug("chat action failed", exc_info=True)

    # -- lifecycle forwarded to the wrapped provider ---------------------
    # (bind/start/stop are Telegram-provider surface, not the Provider
    # protocol the core knows — hence the untyped pass-through)

    _life: Any

    def bind(self, core: Any) -> None:
        self._life.bind(core)

    async def start(self, **kwargs: Any) -> None:
        await self._life.start(**kwargs)
        await self.outbox.start()

    async def stop(self) -> None:
        await self.outbox.stop()
        await self._life.stop()
