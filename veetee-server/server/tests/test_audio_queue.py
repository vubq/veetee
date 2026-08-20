"""Tests for bounded ingress/egress audio queues: accounting, overflow, stale
generation, close and cancellation semantics (M1.5)."""

import asyncio

import pytest

from veetee_server.audio import (
    AudioQueueItem,
    BoundedAudioQueue,
    OverflowPolicy,
    QueueClosedError,
    SlowClientQueueOverflowError,
)


def _item(payload: bytes = b"x", duration_ms: float = 60.0, generation: int = 0) -> AudioQueueItem:
    return AudioQueueItem(payload=payload, duration_ms=duration_ms, generation=generation)


async def _put_and_assert(q: BoundedAudioQueue, item: AudioQueueItem, expected: bool) -> None:
    assert await q.put(item) is expected


@pytest.mark.asyncio
async def test_queue_accounting() -> None:
    q = BoundedAudioQueue(max_items=100, max_bytes=1024, max_duration_ms=1000)
    assert (q.item_count, q.total_bytes, q.total_duration_ms) == (0, 0, 0.0)

    item = AudioQueueItem(payload=b"\x00" * 10, duration_ms=60.0)
    assert await q.put(item) is True
    assert (q.item_count, q.total_bytes, q.total_duration_ms) == (1, 10, 60.0)

    got = await q.get()
    assert got is item
    assert (q.item_count, q.total_bytes, q.total_duration_ms) == (0, 0, 0.0)


@pytest.mark.asyncio
async def test_overflow_drop_oldest() -> None:
    q = BoundedAudioQueue(max_items=2, max_bytes=1024, max_duration_ms=1000)
    assert await q.put(_item(b"a", 60.0)) is True
    assert await q.put(_item(b"b", 60.0)) is True
    # Third item overflows item count -> oldest ("a") is dropped.
    assert await q.put(_item(b"c", 60.0)) is True
    assert q.item_count == 2
    assert (await q.get()).payload == b"b"
    assert (await q.get()).payload == b"c"


@pytest.mark.asyncio
async def test_overflow_by_bytes_drop_oldest() -> None:
    q = BoundedAudioQueue(max_items=10, max_bytes=100, max_duration_ms=1000)
    assert await q.put(_item(b"x" * 60, 60.0)) is True
    assert await q.put(_item(b"y" * 60, 60.0)) is True  # total bytes 120 > 100 -> drop oldest
    assert q.item_count == 1
    assert (await q.get()).payload == b"y" * 60


@pytest.mark.asyncio
async def test_overflow_by_duration_drop_oldest() -> None:
    q = BoundedAudioQueue(max_items=10, max_bytes=1024, max_duration_ms=100.0)
    assert await q.put(_item(b"a", 60.0)) is True
    assert await q.put(_item(b"b", 60.0)) is True  # duration 120 > 100 -> drop oldest
    assert q.item_count == 1
    assert (await q.get()).payload == b"b"


@pytest.mark.asyncio
async def test_overflow_reject_new() -> None:
    q = BoundedAudioQueue(
        max_items=1, max_bytes=1024, max_duration_ms=1000, overflow_policy=OverflowPolicy.REJECT_NEW
    )
    assert await q.put(_item(b"a")) is True
    assert await q.put(_item(b"b")) is False
    assert q.item_count == 1
    assert (await q.get()).payload == b"a"


@pytest.mark.asyncio
async def test_overflow_fail_session() -> None:
    q = BoundedAudioQueue(
        max_items=1,
        max_bytes=1024,
        max_duration_ms=1000,
        overflow_policy=OverflowPolicy.FAIL_SESSION,
    )
    assert await q.put(_item(b"a")) is True
    with pytest.raises(SlowClientQueueOverflowError):
        await q.put(_item(b"b"))


@pytest.mark.asyncio
async def test_single_item_exceeding_queue_max() -> None:
    q = BoundedAudioQueue(max_items=10, max_bytes=100, max_duration_ms=1000)
    assert await q.put(_item(b"x" * 101)) is False  # DROP_OLDEST returns False for too-big item
    q2 = BoundedAudioQueue(
        max_items=10, max_bytes=100, max_duration_ms=1000, overflow_policy=OverflowPolicy.REJECT_NEW
    )
    assert await q2.put(_item(b"x" * 101)) is False
    q3 = BoundedAudioQueue(
        max_items=10,
        max_bytes=100,
        max_duration_ms=1000,
        overflow_policy=OverflowPolicy.FAIL_SESSION,
    )
    with pytest.raises(SlowClientQueueOverflowError):
        await q3.put(_item(b"x" * 101))


@pytest.mark.asyncio
async def test_stale_generation_purge_on_set() -> None:
    q = BoundedAudioQueue()
    assert await q.put(_item(b"old1", generation=0)) is True
    assert await q.put(_item(b"old2", generation=0)) is True
    q.set_generation(1)
    assert await q.put(_item(b"new1", generation=1)) is True
    # Old-generation items are purged immediately.
    assert q.item_count == 1
    assert (await q.get()).payload == b"new1"


@pytest.mark.asyncio
async def test_stale_generation_dropped_on_put() -> None:
    q = BoundedAudioQueue()
    q.set_generation(2)
    assert await q.put(_item(b"stale", generation=1)) is False
    assert q.item_count == 0


@pytest.mark.asyncio
async def test_generation_regression_rejected() -> None:
    q = BoundedAudioQueue()
    q.set_generation(2)
    with pytest.raises(ValueError):
        q.set_generation(1)


@pytest.mark.asyncio
async def test_get_filters_stale_items() -> None:
    q = BoundedAudioQueue()
    await q.put(_item(b"stale", generation=0))
    q.set_generation(1)
    await q.put(_item(b"fresh", generation=1))
    got = await q.get()
    assert got.payload == b"fresh"
    # Queue is now empty; get must not return stale items.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.05)


@pytest.mark.asyncio
async def test_drain_returns_only_valid() -> None:
    q = BoundedAudioQueue()
    await q.put(_item(b"stale", generation=0))
    q.set_generation(1)
    await q.put(_item(b"fresh1", generation=1))
    await q.put(_item(b"fresh2", generation=1))
    items = q.drain()
    assert [i.payload for i in items] == [b"fresh1", b"fresh2"]
    assert q.item_count == 0


@pytest.mark.asyncio
async def test_close_purges_and_wakes_get() -> None:
    q = BoundedAudioQueue()
    await q.put(_item(b"pending"))
    q.close()
    assert q.is_closed
    assert q.item_count == 0
    assert q.total_bytes == 0
    assert q.total_duration_ms == 0.0
    with pytest.raises(QueueClosedError):
        await q.put(_item(b"late"))
    with pytest.raises(QueueClosedError):
        await q.get()


@pytest.mark.asyncio
async def test_get_wakes_on_close() -> None:
    q = BoundedAudioQueue()
    task = asyncio.create_task(q.get())
    await asyncio.sleep(0.01)  # let get() start waiting
    q.close()
    await asyncio.sleep(0)  # let the notify task run
    with pytest.raises(QueueClosedError):
        await task


@pytest.mark.asyncio
async def test_get_cancellation_aware() -> None:
    q = BoundedAudioQueue()
    task = asyncio.create_task(q.get())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Queue remains usable after a cancelled waiter.
    assert await q.put(_item(b"ok")) is True
    assert (await q.get()).payload == b"ok"


@pytest.mark.asyncio
async def test_queue_limit_validation() -> None:
    with pytest.raises(ValueError):
        BoundedAudioQueue(max_items=0)
    with pytest.raises(ValueError):
        BoundedAudioQueue(max_bytes=0)
    with pytest.raises(ValueError):
        BoundedAudioQueue(max_duration_ms=0.0)


def test_queue_item_validation() -> None:
    with pytest.raises(ValueError):
        _item(b"")
    with pytest.raises(ValueError):
        _item(duration_ms=0)
    with pytest.raises(ValueError):
        _item(generation=-1)


@pytest.mark.asyncio
async def test_future_generation_requires_explicit_queue_advance() -> None:
    q = BoundedAudioQueue()
    with pytest.raises(ValueError):
        await q.put(_item(generation=1))


@pytest.mark.asyncio
async def test_queue_is_thread_safe_wrapper() -> None:
    # All mutating operations go through the asyncio condition lock; a simple
    # concurrent producer/consumer must not lose or duplicate items.
    q = BoundedAudioQueue(max_items=50, max_bytes=10**6, max_duration_ms=10**5)

    async def producer() -> None:
        for i in range(20):
            await q.put(_item(bytes([i]), 60.0))
            await asyncio.sleep(0)

    async def consumer() -> list[int]:
        seen: list[int] = []
        for _ in range(20):
            item = await q.get()
            seen.append(item.payload[0])
        return seen

    producer_task = asyncio.create_task(producer())
    consumer_task = asyncio.create_task(consumer())
    await asyncio.gather(producer_task, consumer_task)
    assert q.item_count == 0
