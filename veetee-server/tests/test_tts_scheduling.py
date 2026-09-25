import asyncio
import unittest

from core.providers.tts.scheduler import TTSAdmissionScheduler


class TTSSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_waiter_runs_before_dashboard_and_prewarm(self):
        scheduler = TTSAdmissionScheduler(aging_priority_per_second=0.1)
        holder = await scheduler.acquire("prewarm")
        dashboard_task = asyncio.create_task(scheduler.acquire("dashboard"))
        live_task = asyncio.create_task(scheduler.acquire("live"))
        await asyncio.sleep(0)
        await holder.release()

        live = await asyncio.wait_for(live_task, timeout=0.2)
        self.assertEqual(live.priority, "live")
        self.assertFalse(dashboard_task.done())
        await live.release()
        dashboard = await asyncio.wait_for(dashboard_task, timeout=0.2)
        await dashboard.release()

    async def test_first_audio_waiter_beats_earlier_live_continuation(self):
        scheduler = TTSAdmissionScheduler(aging_priority_per_second=2.0)
        holder = await scheduler.acquire("live")
        continuation_task = asyncio.create_task(scheduler.acquire("live"))
        await asyncio.sleep(0)
        first_audio_task = asyncio.create_task(scheduler.acquire("live_first"))
        await asyncio.sleep(0)

        await holder.release()
        first_audio = await asyncio.wait_for(first_audio_task, timeout=0.2)
        self.assertEqual(first_audio.priority, "live_first")
        self.assertFalse(continuation_task.done())

        await first_audio.release()
        continuation = await asyncio.wait_for(continuation_task, timeout=0.2)
        self.assertEqual(continuation.priority, "live")
        await continuation.release()

    async def test_first_audio_boost_is_runtime_configurable(self):
        scheduler = TTSAdmissionScheduler(
            aging_priority_per_second=2.0,
            first_audio_priority_boost=0.0,
        )
        self.assertEqual(
            scheduler.snapshot()["first_audio_priority_boost"],
            0.0,
        )

        holder = await scheduler.acquire("live")
        continuation_task = asyncio.create_task(scheduler.acquire("live"))
        await asyncio.sleep(0)
        first_audio_task = asyncio.create_task(scheduler.acquire("live_first"))
        await asyncio.sleep(0)
        await holder.release()

        # With no boost, equal-priority work stays FIFO.
        continuation = await asyncio.wait_for(continuation_task, timeout=0.2)
        self.assertFalse(first_audio_task.done())
        await continuation.release()
        first_audio = await asyncio.wait_for(first_audio_task, timeout=0.2)
        await first_audio.release()

        scheduler.configure(
            first_audio_priority_boost=5.0,
            aging_priority_per_second=3.0,
        )
        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot["first_audio_priority_boost"], 5.0)
        self.assertEqual(snapshot["aging_priority_per_second"], 3.0)

    async def test_cancelled_queued_job_never_acquires_engine(self):
        scheduler = TTSAdmissionScheduler()
        holder = await scheduler.acquire("live")
        cancel = asyncio.Event()
        queued = asyncio.create_task(scheduler.acquire("prewarm", cancel_event=cancel))
        await asyncio.sleep(0)
        cancel.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(queued, timeout=0.2)
        self.assertEqual(scheduler.snapshot()["waiting"]["prewarm"], 0)
        await holder.release()

    async def test_live_waiter_requests_preemption_of_non_live_holder(self):
        scheduler = TTSAdmissionScheduler()
        dashboard = await scheduler.acquire("dashboard")
        self.assertFalse(dashboard.preempted)

        live_task = asyncio.create_task(scheduler.acquire("live"))
        await asyncio.sleep(0)

        self.assertTrue(dashboard.preempted)
        self.assertEqual(scheduler.snapshot()["preemption_requests"], 1)
        self.assertFalse(live_task.done())

        await dashboard.release()
        self.assertEqual(scheduler.snapshot()["preemptions_completed"], 1)
        live = await asyncio.wait_for(live_task, timeout=0.2)
        self.assertFalse(live.preempted)
        await live.release()

    async def test_live_holder_is_never_preempted_by_another_live_waiter(self):
        scheduler = TTSAdmissionScheduler()
        first = await scheduler.acquire("live")
        second_task = asyncio.create_task(scheduler.acquire("live"))
        await asyncio.sleep(0)

        self.assertFalse(first.preempted)
        self.assertEqual(scheduler.snapshot()["preemption_requests"], 0)

        await first.release()
        second = await asyncio.wait_for(second_task, timeout=0.2)
        await second.release()


if __name__ == "__main__":
    unittest.main()
