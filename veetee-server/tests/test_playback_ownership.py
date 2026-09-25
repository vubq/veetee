import asyncio
import json
import unittest

from core.music_player import MusicPlayer, MusicTrack
from core.playback import PlaybackCoordinator


class PlaybackCoordinatorTests(unittest.TestCase):
    def test_new_claim_invalidates_previous_writer(self):
        coordinator = PlaybackCoordinator()
        music = coordinator.claim("music", 1)
        self.assertTrue(coordinator.is_current(music))

        assistant = coordinator.claim("assistant", 7)
        self.assertFalse(coordinator.is_current(music))
        self.assertTrue(coordinator.is_current(assistant))

        self.assertFalse(coordinator.release(music))
        self.assertTrue(coordinator.is_current(assistant))
        self.assertTrue(coordinator.release(assistant))
        self.assertIsNone(coordinator.active)

    def test_owner_scoped_invalidation_does_not_kill_other_writer(self):
        coordinator = PlaybackCoordinator()
        music = coordinator.claim("music", 2)
        self.assertFalse(coordinator.invalidate(owner="assistant"))
        self.assertTrue(coordinator.is_current(music))
        self.assertTrue(coordinator.invalidate(owner="music"))
        self.assertFalse(coordinator.is_current(music))


class MusicPlaybackOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_preempted_music_cannot_send_stale_audio_or_stop(self):
        coordinator = PlaybackCoordinator()
        texts = []
        binaries = []

        async def send_text(payload):
            texts.append(payload)
            return True

        async def send_binary(payload):
            binaries.append(payload)
            return True

        async def frames(_track):
            # Enough chunks to keep playback alive until the assistant claim.
            for _ in range(100):
                yield b"\x00\x00" * 5760
                await asyncio.sleep(0.005)

        player = MusicPlayer(
            send_text=send_text,
            send_binary=send_binary,
            session_id="s",
            frame_source_factory=frames,
            send_ahead_ms=60,
            playback=coordinator,
        )
        await player.play(MusicTrack(video_id="x", title="track", stream_url="fixture"))

        for _ in range(100):
            if binaries:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(binaries)

        before_binary = len(binaries)
        before_stop = sum(
            1
            for item in texts
            if isinstance(item, str)
            and json.loads(item).get("state") == "stop"
        )

        assistant = coordinator.claim("assistant", 9)
        await asyncio.sleep(0.15)

        self.assertEqual(len(binaries), before_binary)
        after_stop = sum(
            1
            for item in texts
            if isinstance(item, str)
            and json.loads(item).get("state") == "stop"
        )
        self.assertEqual(after_stop, before_stop)
        self.assertTrue(coordinator.is_current(assistant))
        await player.close()


if __name__ == "__main__":
    unittest.main()
