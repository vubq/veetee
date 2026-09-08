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


if __name__ == "__main__":
    unittest.main()
