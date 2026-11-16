"""Outbox guarantees: lanes, retries, permanent drops, typing gate."""

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
from channelsrv.providers.base import CardButton
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
    ) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise FlakyProvider.Wobble("proxy flapped")
        await super().send_card(chat_id, markdown, buttons, instant)


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


# ---------------------------------------------------------------------- cards


async def test_card_delivered_through_the_queue(clock: Clock) -> None:
    provider = FakeProvider()
    box = make(provider, clock)
    box.send_card(CHAT, "pick one", [])
    await drain(box)
    assert provider.cards == [(CHAT, "pick one", ())]


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
    await door.send_card(CHAT, "card", [[btn]], instant=True)
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
        assert [t[1] for t in provider.texts] == [
            "Restarting the daemon — back in a few seconds."
        ]
        assert box.empty
    finally:
        provider.release.set()
        await box.stop()


# ------------------------------------------------------------- chaos delivery

import random


async def test_chaos_delivery_exactly_once_no_loss(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Seeded chaos: random lanes, flaky network (20%), permanent
    rejections (5%), clock jumps, flush/stop/start interleavings. The
    invariants: every item is delivered exactly once or dropped-as-permanent
    exactly once; nothing is lost to a retryable failure; the queue drains.
    Deterministic — same seed, same scenario."""
    rnd = random.Random(20261116)
    outcomes: list[str | None] = []
    for _ in range(4000):
        roll = rnd.random()
        outcomes.append("reject" if roll < 0.05 else "wobble" if roll < 0.25 else None)

    class ChaosProvider(FakeProvider):
        async def send_text(
            self,
            chat_id: int,
            markdown: str,
            agent_name: str | None = None,
            instant: bool = False,
        ) -> None:
            spec = outcomes.pop() if outcomes else None
            if spec == "reject":
                raise DeadProvider.Rejected("never accepted")
            if spec == "wobble":
                raise FlakyProvider.Wobble("proxy flapped")
            await super().send_text(chat_id, markdown, agent_name, instant)

    provider = ChaosProvider()
    clock = Clock()
    box = Outbox(
        provider,
        permanent=(DeadProvider.Rejected,),
        now=lambda: clock.now,
        sleep=clock.sleep,
    )
    expected: list[str] = [f"msg-{i}" for i in range(300)]

    with caplog.at_level(logging.ERROR):
        for text in expected:  # enqueue all, mixing lanes
            box.send_text(CHAT, text, instant=text.endswith(("0", "4", "8")))
        for _ in range(3000):
            roll = rnd.random()
            if roll < 0.55:
                item = box._pick_due()
                if item is not None:
                    await box._deliver(item)
            elif roll < 0.75:
                clock.t += rnd.uniform(1.0, 120.0)  # time passes
            elif roll < 0.90:
                continue
            elif roll < 0.96:  # a restart-like drain, then back to work
                await box.flush()
                await box.start()
            if box.empty:
                break
        for _ in range(20000):  # drain: retries must converge
            if box.empty:
                break
            clock.t += 61.0
            item = box._pick_due()
            if item is not None:
                await box._deliver(item)

    assert box.empty, "queue must drain under retryable failures"
    delivered = [t[1] for t in provider.texts]
    dropped = {
        m for r in caplog.records for m in expected if f"'{m}'" in r.getMessage()
    }
    assert len(delivered) == len(set(delivered)), "no message delivered twice"
    assert set(delivered) | dropped == set(expected), "nothing silently lost"
    assert not (set(delivered) & dropped), "a drop and a delivery for one item"
