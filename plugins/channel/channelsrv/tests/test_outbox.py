"""Outbox guarantees: lanes, retries, permanent drops, refs, typing gate."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import pytest

from channelsrv.outbox import (
    FAST_DELAYS,
    SLOW_DELAY,
    STALL_WARN_AFTER,
    Outbox,
    OutboxProvider,
)
from channelsrv.providers.base import CardButton, MsgRef
from tests.fakes import FakeProvider

CHAT = 42


class Clock:
    """Fake monotonic clock + recorded sleeps the worker asked for."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    @property
    def now(self) -> float:
        return self.t

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.t += delay


@pytest.fixture
def clock() -> Clock:
    return Clock()


class FlakyProvider(FakeProvider):
    """Fails the first `failures` sends of every kind with a retryable error."""

    class Wobble(Exception):
        pass

    def __init__(self, failures: int = 0) -> None:
        super().__init__()
        self.failures = failures

    async def send_text(
        self,
        chat_id: int,
        markdown: str,
        agent_name: str | None = None,
        instant: bool = False,
    ) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise FlakyProvider.Wobble("proxy flapped")
        await super().send_text(chat_id, markdown, agent_name, instant)

    async def send_card(
        self,
        chat_id: int,
        markdown: str,
        buttons: Sequence[Sequence[CardButton]],
        instant: bool = False,
    ) -> int | MsgRef | None:
        if self.failures > 0:
            self.failures -= 1
            raise FlakyProvider.Wobble("proxy flapped")
        return await super().send_card(chat_id, markdown, buttons, instant)


class DeadProvider(FakeProvider):
    class Rejected(Exception):
        pass

    async def send_text(
        self,
        chat_id: int,
        markdown: str,
        agent_name: str | None = None,
        instant: bool = False,
    ) -> None:
        raise DeadProvider.Rejected("message is too long")


def make(
    provider: FakeProvider,
    clock: Clock,
    permanent: tuple[type[Exception], ...] = (),
) -> Outbox:
    return Outbox(
        provider, permanent=permanent, now=lambda: clock.now, sleep=clock.sleep
    )


async def drain(box: Outbox, rounds: int = 50) -> None:
    """Runs the worker loop by hand until it would idle-wait."""
    for _ in range(rounds):
        if box.empty:
            return
        item = box._pick_due()
        if item is None:
            return
        await box._deliver(item)


# ------------------------------------------------------------------- delivery


async def test_delivers_in_order_per_lane(clock: Clock) -> None:
    provider = FakeProvider()
    box = make(provider, clock)
    for n in range(5):
        box.send_text(CHAT, f"msg {n}")
    await drain(box)
    assert [t[1] for t in provider.texts] == [f"msg {n}" for n in range(5)]
    assert box.empty


async def test_instant_overtakes_backed_off_normal(clock: Clock) -> None:
    provider = FlakyProvider(failures=1)
    box = make(provider, clock)
    box.send_text(CHAT, "agent reply")  # normal lane, will fail once
    await drain(box)  # attempt 1 fails -> backoff 1s
    assert provider.texts == []

    box.send_text(CHAT, "/status answer", instant=True)
    item = box._pick_due()
    assert item is not None and item.lane == "instant"
    await box._deliver(item)
    assert [t[1] for t in provider.texts] == ["/status answer"]
    assert box.depth == 1  # the normal item still waits its backoff


# --------------------------------------------------------------------- retry


async def test_fast_backoff_sequence_then_slow(clock: Clock) -> None:
    provider = FlakyProvider(failures=99)  # never succeeds
    box = make(provider, clock)
    box.send_text(CHAT, "doomed but retried")
    waits: list[float] = []  # the clock jumps between failure and retry
    for _ in range(len(FAST_DELAYS) + 2):
        await drain(box)
        if box.empty:
            break
        item = box._lanes["normal"][0]
        waits.append(max(0.0, item.next_at - clock.now))
        clock.t += waits[-1]  # jump to the retry moment
    head = box._lanes["normal"][0]
    # five fast attempts waited 1..16s, then the 60s slow lane forever
    assert head.attempts == len(FAST_DELAYS) + 2
    assert waits[: len(FAST_DELAYS)] == list(FAST_DELAYS)
    assert all(w == SLOW_DELAY for w in waits[len(FAST_DELAYS) :])


async def test_retry_until_success_delivers(clock: Clock) -> None:
    provider = FlakyProvider(failures=3)
    box = make(provider, clock)
    box.send_text(CHAT, "survives the flap")
    while not provider.texts and box.depth:
        item = box._pick_due()
        if item is None:
            clock.t += 1.0
            continue
        await box._deliver(item)
    assert [t[1] for t in provider.texts] == ["survives the flap"]
    assert box.empty


# ------------------------------------------------------------------ permanent


async def test_permanent_rejection_drops_item(
    clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    provider = DeadProvider()
    box = make(provider, clock, permanent=(DeadProvider.Rejected,))
    box.send_text(CHAT, "too long to live")
    with caplog.at_level(logging.ERROR):
        await drain(box)
    assert provider.texts == []
    assert box.empty
    assert "dropped" in caplog.text


async def test_unclassified_error_is_retried_not_dropped(clock: Clock) -> None:
    provider = DeadProvider()  # raises, but not registered as permanent
    box = make(provider, clock)
    box.send_text(CHAT, "wobbled")
    await drain(box)
    assert box.depth == 1  # still queued for retry, not dropped


# ----------------------------------------------------------------------- refs


async def test_send_card_ref_resolves_and_edit_follows(clock: Clock) -> None:
    provider = FakeProvider()
    box = make(provider, clock)
    ref = box.send_card(CHAT, "chain start", [])
    box.edit_text(CHAT, ref, "chain update")
    await drain(box)
    assert isinstance(ref, MsgRef) and ref.value == provider._next_id
    assert provider.edits == [(CHAT, provider._next_id, "chain update")]


async def test_edit_of_dead_create_is_dropped(clock: Clock) -> None:
    class CardDies(FlakyProvider):
        async def send_card(
            self,
            chat_id: int,
            markdown: str,
            buttons: Sequence[Sequence[CardButton]],
            instant: bool = False,
        ) -> int | MsgRef | None:
            raise DeadProvider.Rejected("no card for you")

    provider = CardDies()
    box = make(provider, clock, permanent=(DeadProvider.Rejected,))
    ref = box.send_card(CHAT, "never born", [])
    box.edit_text(CHAT, ref, "pointless edit")
    await drain(box)
    assert ref.value is None
    assert provider.edits == []
    assert box.empty


# --------------------------------------------------------------------- typing


async def test_typing_suppressed_while_queue_nonempty(clock: Clock) -> None:
    provider = FakeProvider()
    door = OutboxProvider(provider)
    await door.send_text(CHAT, "queued reply")  # enqueue is sync-fast
    await door.chat_action(CHAT)
    assert provider.actions == []  # a stale indicator would lie
    await drain(door.outbox)
    await door.chat_action(CHAT)
    assert provider.actions == [CHAT]


# --------------------------------------------------------------------- stall


async def test_stall_warning_after_30s_once(
    clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    provider = FlakyProvider(failures=99)
    box = make(provider, clock)
    box.send_text(CHAT, "stuck")
    await drain(box)  # first failure
    clock.t += STALL_WARN_AFTER
    with caplog.at_level(logging.WARNING):
        box._check_stall()
        box._check_stall()
    warnings = [r.getMessage() for r in caplog.records if "stuck" in r.getMessage()]
    assert len(warnings) == 1
    assert "oldest" in warnings[0]


# --------------------------------------------------------------------- flush


async def test_flush_oneshot_despite_backoff(clock: Clock) -> None:
    provider = FlakyProvider(failures=1)
    box = make(provider, clock)
    box.send_text(CHAT, "goodbye before restart")
    await drain(box)  # fails once, backs off 1s
    await box.flush()  # one immediate attempt per item, backoff ignored
    assert [t[1] for t in provider.texts] == ["goodbye before restart"]
    assert box.empty


async def test_flush_keeps_failing_item_queued(clock: Clock) -> None:
    provider = FlakyProvider(failures=99)  # nothing goes out, ever
    box = make(provider, clock)
    box.send_text(CHAT, "undeliverable goodbye")
    await box.flush()
    assert box.depth == 1  # one shot only — no hammering a dead network


# ------------------------------------------------------------------ provider


async def test_outbox_provider_shape() -> None:
    provider = FakeProvider()
    door = OutboxProvider(provider)
    assert isinstance(door.outbox, Outbox)
    assert door.outbox.empty
    btn = CardButton("Approve", "yes")
    ref = await door.send_card(CHAT, "card", [[btn]], instant=True)
    assert isinstance(ref, MsgRef)
    assert door.outbox.depth == 1  # enqueued, not sent
    item = door.outbox._lanes["instant"][0]
    assert item.lane == "instant"


# ------------------------------------------------- flush vs worker (the race)


class GatedProvider(FakeProvider):
    """send_text blocks mid-flight until the test releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def send_text(
        self,
        chat_id: int,
        markdown: str,
        agent_name: str | None = None,
        instant: bool = False,
    ) -> None:
        self.entered.set()
        await self.release.wait()
        await super().send_text(chat_id, markdown, agent_name, instant)


async def test_flush_is_the_only_deliverer_no_duplicate() -> None:
    """Incident 2026-11-16 00:37: 'Restarting the daemon' arrived twice —
    flush() iterated an item the worker was already mid-sending. flush must
    make itself the single deliverer: stop the worker first, then drain.
    Fails on the pre-fix code (two sends)."""
    provider = GatedProvider()
    box = Outbox(provider)
    await box.start()
    try:
        box.send_text(CHAT, "Restarting the daemon — back in a few seconds.")
        await provider.entered.wait()  # the worker holds the item mid-flight
        flusher = asyncio.create_task(box.flush())
        await asyncio.sleep(0.05)  # flush runs while the worker is stuck
        provider.release.set()
        await flusher
        assert [t[1] for t in provider.texts] == ["Restarting the daemon — back in a few seconds."]
        assert box.empty
    finally:
        provider.release.set()
        await box.stop()
