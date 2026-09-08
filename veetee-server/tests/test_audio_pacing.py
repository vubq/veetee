import asyncio
import unittest

from core.audio_pacing import AudioPacer


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class AudioPacingTests(unittest.IsolatedAsyncioTestCase):
    def test_two_60ms_frames_fit_120ms_send_ahead_budget(self):
        clock = FakeClock()
        pacer = AudioPacer(60, 120, clock=clock)

        self.assertEqual(pacer.next_wait_seconds(), 0.0)
        pacer.record_frame_sent()
        self.assertEqual(pacer.next_wait_seconds(), 0.0)
        pacer.record_frame_sent()
        self.assertAlmostEqual(pacer.estimated_lead_ms(), 120.0, places=6)
        self.assertAlmostEqual(pacer.next_wait_seconds(), 0.06, places=6)

    def test_stall_resets_old_schedule_instead_of_bursting_catchup(self):
        clock = FakeClock()
        pacer = AudioPacer(60, 120, clock=clock)
        pacer.record_frame_sent()
        pacer.record_frame_sent()

        clock.advance(1.0)
        self.assertEqual(pacer.estimated_lead_ms(), 0.0)
        self.assertEqual(pacer.next_wait_seconds(), 0.0)
        pacer.record_frame_sent()
        pacer.record_frame_sent()
        self.assertAlmostEqual(pacer.next_wait_seconds(), 0.06, places=6)

    async def test_cancel_does_not_wait_for_pacing_deadline(self):
        clock = FakeClock()
        pacer = AudioPacer(60, 60, clock=clock)
        pacer.record_frame_sent()
        cancel_event = asyncio.Event()
        cancel_event.set()
        self.assertFalse(await pacer.wait_for_send(cancel_event))


if __name__ == "__main__":
    unittest.main()
