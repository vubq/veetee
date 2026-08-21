"""Tests for the audio packet pacer: fake clock, drift reset, anchor reset and
cancellation semantics (M1.5)."""

import asyncio

import pytest

from veetee_server.audio import PacketPacer


class FakeClock:
    """Deterministic monotonic clock controllable by tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class RecordingSleeper:
    """Records requested positive sleeps instead of actually sleeping."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _build(
    max_drift_seconds: float = 0.1,
) -> tuple[PacketPacer, FakeClock, RecordingSleeper]:
    clock = FakeClock()
    sleeper = RecordingSleeper()
    pacer = PacketPacer(max_drift_seconds=max_drift_seconds, clock=clock, sleeper=sleeper)
    return pacer, clock, sleeper


@pytest.mark.asyncio
async def test_first_frame_does_not_sleep() -> None:
    pacer, clock, sleeper = _build()
    assert await pacer.pace(0.06) == 0.0
    assert sleeper.sleeps == []
    # Second frame exactly on schedule: no sleep required.
    clock.advance(0.06)
    assert await pacer.pace(0.06) == 0.0
    assert sleeper.sleeps == []


@pytest.mark.asyncio
async def test_pace_sleeps_until_next_slot() -> None:
    pacer, clock, sleeper = _build()
    await pacer.pace(0.06)  # anchor at 0.06
    clock.advance(0.02)  # 0.04 early -> sleep 0.04, anchor -> 0.12
    assert await pacer.pace(0.06) == pytest.approx(0.04)
    clock.advance(0.04)  # now 0.06, target 0.12 -> sleep 0.06, anchor -> 0.18
    assert await pacer.pace(0.06) == pytest.approx(0.06)
    assert sleeper.sleeps == [pytest.approx(0.04), pytest.approx(0.06)]


@pytest.mark.asyncio
async def test_zero_and_negative_duration() -> None:
    pacer, _, sleeper = _build()
    assert await pacer.pace(0.0) == 0.0
    assert sleeper.sleeps == []
    with pytest.raises(ValueError):
        await pacer.pace(-0.01)


@pytest.mark.asyncio
async def test_drift_below_max_keeps_schedule() -> None:
    pacer, clock, sleeper = _build(max_drift_seconds=0.1)
    await pacer.pace(0.06)  # anchor 0.06
    clock.advance(0.07)  # 0.01 late, within drift
    # No sleep, but the anchor keeps its schedule (0.12) instead of resetting.
    assert await pacer.pace(0.06) == 0.0
    clock.advance(0.05)  # now 0.12, exactly on the preserved schedule
    assert await pacer.pace(0.06) == 0.0
    assert sleeper.sleeps == []


@pytest.mark.asyncio
async def test_drift_exceeds_max_resets_anchor() -> None:
    pacer, clock, sleeper = _build(max_drift_seconds=0.1)
    await pacer.pace(0.06)  # anchor 0.06
    clock.advance(0.5)  # far beyond the drift budget
    # Anchor resets to now + duration; no sleep is scheduled.
    assert await pacer.pace(0.06) == 0.0
    assert sleeper.sleeps == []
    clock.advance(0.06)  # now exactly on the freshly reset anchor
    assert await pacer.pace(0.06) == 0.0
    assert sleeper.sleeps == []


@pytest.mark.asyncio
async def test_reset_reanchors_from_current_time() -> None:
    pacer, clock, sleeper = _build()
    await pacer.pace(0.06)  # anchor 0.06
    clock.advance(0.5)
    pacer.reset()
    await pacer.pace(0.06)  # re-anchor at 0.56, no sleep
    clock.advance(0.05)  # now 0.55 -> target 0.56 -> sleep 0.01
    assert await pacer.pace(0.06) == pytest.approx(0.01)
    assert sleeper.sleeps == [pytest.approx(0.01)]


@pytest.mark.asyncio
async def test_cancellation_propagates_from_sleeper() -> None:
    clock = FakeClock()
    pacer = PacketPacer(
        max_drift_seconds=0.1,
        clock=clock,
        sleeper=_CancellingSleeper(),
    )
    await pacer.pace(0.06)  # anchor
    clock.advance(0.0)
    with pytest.raises(asyncio.CancelledError):
        await pacer.pace(0.06)


class _CancellingSleeper:
    """Sleeper that immediately cancels the current task."""

    async def __call__(self, seconds: float) -> None:
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_constructor_rejects_non_positive_drift() -> None:
    with pytest.raises(ValueError):
        PacketPacer(max_drift_seconds=0.0)
    with pytest.raises(ValueError):
        PacketPacer(max_drift_seconds=-1.0)


@pytest.mark.asyncio
async def test_injectable_clock_is_used() -> None:
    clock = FakeClock(start=100.0)
    sleeper = RecordingSleeper()
    pacer = PacketPacer(max_drift_seconds=0.1, clock=clock, sleeper=sleeper)
    await pacer.pace(0.06)
    clock.advance(0.03)
    assert await pacer.pace(0.06) == pytest.approx(0.03)
    assert sleeper.sleeps == [pytest.approx(0.03)]


@pytest.mark.asyncio
async def test_pacer_metrics_drift_tracking() -> None:
    clock = FakeClock(start=0.0)
    sleeper = RecordingSleeper()
    pacer = PacketPacer(max_drift_seconds=0.1, clock=clock, sleeper=sleeper)

    # Frame 1: Initial frame anchors send time
    assert await pacer.pace(0.06) == 0.0
    metrics = pacer.metrics
    assert metrics.frames == 1
    assert metrics.drift_resets == 0
    assert metrics.max_lag_seconds == 0.0

    # Frame 2: Lag of 0.02s (within max_drift_seconds of 0.1)
    clock.advance(0.08)
    assert await pacer.pace(0.06) == 0.0
    metrics = pacer.metrics
    assert metrics.frames == 2
    assert metrics.drift_resets == 0
    assert metrics.max_lag_seconds == pytest.approx(0.02)

    # Frame 3: Lag of 0.46s (exceeds 0.1s -> triggers drift reset)
    clock.advance(0.5)
    assert await pacer.pace(0.06) == 0.0
    metrics = pacer.metrics
    assert metrics.frames == 3
    assert metrics.drift_resets == 1
    assert metrics.max_lag_seconds == pytest.approx(0.46)


@pytest.mark.asyncio
async def test_pacer_reset_interrupts_in_progress_sleep() -> None:
    sleep_started = asyncio.Event()
    sleep_cancelled = asyncio.Event()

    async def blocking_sleep(_: float) -> None:
        sleep_started.set()
        try:
            await asyncio.Future()
        finally:
            sleep_cancelled.set()

    pacer = PacketPacer(max_drift_seconds=0.1, clock=lambda: 0.0, sleeper=blocking_sleep)
    await pacer.pace(0.06)
    task = asyncio.create_task(pacer.pace(0.06))
    await sleep_started.wait()
    pacer.reset()

    assert await task == 0.0
    assert sleep_cancelled.is_set()


@pytest.mark.asyncio
async def test_pacer_pace_cancellation_cleans_up_subtasks() -> None:
    sleep_started = asyncio.Event()
    sleep_cancelled = asyncio.Event()

    async def blocking_sleep(_: float) -> None:
        sleep_started.set()
        try:
            await asyncio.Future()
        finally:
            sleep_cancelled.set()

    pacer = PacketPacer(max_drift_seconds=0.1, clock=lambda: 0.0, sleeper=blocking_sleep)
    await pacer.pace(0.06)
    task = asyncio.create_task(pacer.pace(0.06))
    await sleep_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert sleep_cancelled.is_set()
