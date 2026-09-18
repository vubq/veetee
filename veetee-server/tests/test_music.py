import asyncio
import json
import math
import unittest
from unittest.mock import patch

import numpy as np

from config.settings import AppConfig, load_settings
from core.music_player import MusicPlayer, MusicTrack, ffmpeg_pcm24_frames
from core.tools.builtin.music_tool import (
    MusicToolProvider,
    ytdlp_resolve_url,
    ytdlp_search,
)


SAMPLE_JSON = {
    "entries": [
        {"id": "abc123", "title": "See Tình - Hoàng Thùy Linh",
         "uploader": "Hoa Sen", "duration": 237},
        {"id": "def456", "title": "Unrelated Video",
         "uploader": "Someone", "duration": 60},
        {"id": "", "title": "No id entry"},
    ]
}


def sine_pcm24_chunks(seconds=0.5, freq=440.0):
    async def factory(_track):
        total = int(24000 * seconds)
        wave = (0.4 * np.sin(2 * math.pi * freq * np.arange(total) / 24000))
        pcm = (wave * 32767).astype(np.int16).tobytes()
        for offset in range(0, len(pcm), 2880 * 4):
            yield pcm[offset:offset + 2880 * 4]
            await asyncio.sleep(0)
    return factory


class FakeSender:
    def __init__(self):
        self.texts = []
        self.binaries = 0
        self.binary_payloads = []

    async def send_text(self, payload):
        self.texts.append(payload if isinstance(payload, str) else payload)
        return True

    async def send_binary(self, payload):
        self.binaries += 1
        self.binary_payloads.append(payload)
        return True


def make_player(sender=None, **kwargs):
    sender = sender or FakeSender()
    return MusicPlayer(
        send_text=sender.send_text,
        send_binary=sender.send_binary,
        session_id="test-session",
        version=1,
        **kwargs,
    ), sender


def _texts_joined(sender):
    """Extract spoken/displayed text from recorded wire messages.

    make_tts_message returns a JSON *string* (ASCII-escaped), so parse it
    instead of matching raw payloads.
    """
    parts = []
    for item in sender.texts:
        data = item
        if isinstance(item, (str, bytes)):
            try:
                data = json.loads(item)
            except (ValueError, TypeError):
                parts.append(str(item))
                continue
        if isinstance(data, dict):
            if "text" in data:
                parts.append(str(data["text"]))
            elif data.get("state") == "stop":
                parts.append("stop")
        else:
            parts.append(str(data))
    return " ".join(parts)


class MusicPlayerTests(unittest.IsolatedAsyncioTestCase):
    async def test_play_streams_frames_and_announces_title(self):
        player, sender = make_player(
            frame_source_factory=sine_pcm24_chunks(0.5))
        try:
            result = await player.play(MusicTrack(video_id="x", title="See Tình"))
            self.assertEqual(result["status"], "playing")
            self.assertTrue(player.playing)
            await asyncio.sleep(1.2)
            self.assertGreater(sender.binaries, 3)
            self.assertIn("See Tình", _texts_joined(sender))
        finally:
            await player.stop()
        self.assertFalse(player.playing)
        self.assertEqual(player.state, "idle")

    async def test_stop_sends_tts_stop(self):
        player, sender = make_player(
            frame_source_factory=sine_pcm24_chunks(5.0))
        try:
            await player.play(MusicTrack(video_id="x", title="Long"))
            await asyncio.sleep(0.4)
            before = len(sender.texts)
        finally:
            await player.stop()
        self.assertEqual(player.state, "idle")
        self.assertIn("stop", _texts_joined(sender)[before:])

    async def test_pause_resume_closes_and_reopens_tts_envelope(self):
        player, sender = make_player(frame_source_factory=sine_pcm24_chunks(5.0))
        try:
            await player.play(MusicTrack(video_id="x", title="T"))
            for _ in range(100):
                if player._envelope_active:
                    break
                await asyncio.sleep(0.01)
            starts_before = sum(
                1 for item in sender.texts
                if isinstance(item, str)
                and json.loads(item).get("state") == "start"
            )
            self.assertEqual((await player.pause())["status"], "paused")
            self.assertIn("stop", _texts_joined(sender))
            self.assertEqual((await player.resume())["status"], "playing")
            for _ in range(100):
                starts_after = sum(
                    1 for item in sender.texts
                    if isinstance(item, str)
                    and json.loads(item).get("state") == "start"
                )
                if starts_after > starts_before:
                    break
                await asyncio.sleep(0.01)
            self.assertGreater(starts_after, starts_before)
        finally:
            await player.stop()

    async def test_protocol_version_getter_tracks_renegotiation(self):
        sender = FakeSender()
        holder = {"version": 1}
        player = MusicPlayer(
            send_text=sender.send_text,
            send_binary=sender.send_binary,
            session_id="test-session",
            version=lambda: holder["version"],
            frame_source_factory=sine_pcm24_chunks(0.25),
        )
        try:
            holder["version"] = 2
            await player.play(MusicTrack(video_id="x", title="V2"))
            for _ in range(100):
                if sender.binary_payloads:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(sender.binary_payloads)
            self.assertEqual(sender.binary_payloads[0][:2], b"\x00\x02")
        finally:
            await player.stop()

    async def test_session_music_player_uses_live_protocol_version(self):
        from core.protocol import ProtocolVersion
        from core.session import ClientSession

        class BareSession(ClientSession):
            def _create_asr(self):
                class _Asr:
                    async def stop(self):
                        return None
                return _Asr()

        class StubSocket:
            async def send(self, payload):
                return True

        session = BareSession(StubSocket(), AppConfig(), None, None)
        try:
            session.version = ProtocolVersion.V3
            self.assertEqual(session.music_player._protocol_version(), 3)
        finally:
            await session.close()

    async def test_natural_end_returns_idle(self):
        player, sender = make_player(
            frame_source_factory=sine_pcm24_chunks(0.2))
        await player.play(MusicTrack(video_id="x", title="Short"))
        for _ in range(100):
            if not player.playing:
                break
            await asyncio.sleep(0.05)
        self.assertFalse(player.playing)
        self.assertGreater(sender.binaries, 0)

    async def test_history_next_previous(self):
        player, _ = make_player(
            frame_source_factory=sine_pcm24_chunks(5.0))
        try:
            await player.play(MusicTrack(video_id="a", title="A"))
            await player.play(MusicTrack(video_id="b", title="B"))
            self.assertEqual(player.status()["title"], "B")
            result = await player.previous_track()
            self.assertEqual(result["title"], "A")
            prev_err = await player.previous_track()
            self.assertEqual(prev_err["status"], "error")
            result = await player.next_track()
            self.assertEqual(result["title"], "B")
            next_err = await player.next_track()
            self.assertEqual(next_err["status"], "error")
        finally:
            await player.stop()

    async def test_replacing_track_closes_old_tts_envelope_before_new_start(self):
        player, sender = make_player(
            frame_source_factory=sine_pcm24_chunks(5.0))
        try:
            await player.play(MusicTrack(video_id="a", title="A"))
            for _ in range(100):
                if player._envelope_active:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(player._envelope_active)

            before = len(sender.texts)
            await player.play(MusicTrack(video_id="b", title="B"))
            for _ in range(100):
                new_messages = sender.texts[before:]
                states = [json.loads(item).get("state") for item in new_messages]
                if "start" in states:
                    break
                await asyncio.sleep(0.01)

            new_messages = sender.texts[before:]
            states = [json.loads(item).get("state") for item in new_messages]
            self.assertIn("stop", states)
            self.assertIn("start", states)
            self.assertLess(states.index("stop"), states.index("start"))
        finally:
            await player.stop()

    async def test_stop_clears_pending_track(self):
        player, _ = make_player()
        player.set_pending(MusicTrack(video_id="p", title="Pending"))
        self.assertIsNotNone(player._pending_track)
        await player.stop()
        self.assertIsNone(player._pending_track)
        self.assertEqual(player.state, "idle")

    async def test_resume_reopens_audio_envelope(self):
        sender = FakeSender()
        player, _ = make_player(sender, frame_source_factory=sine_pcm24_chunks(5.0))
        try:
            await player.play(MusicTrack(video_id="x", title="DuckTrack"))
            await asyncio.sleep(0.1)
            # AI speaks: duck pauses
            await player.pause()
            self.assertEqual(player.state, "paused")
            self.assertFalse(player._envelope_active)
            # AI finishes speaking: unduck resumes
            texts_before = len(sender.texts)
            await player.resume()
            await asyncio.sleep(0.1)
            # Check that tts:start and sentence_start were sent again
            resumed_texts = " ".join(str(t) for t in sender.texts[texts_before:])
            self.assertIn("start", resumed_texts)
            self.assertIn("DuckTrack", resumed_texts)
        finally:
            await player.stop()

    async def test_failing_frame_source_marks_failed(self):
        async def failing_factory(_track, **_kwargs):
            raise RuntimeError("decoder crashed")
            yield b""  # unreachable

        player, _ = make_player(frame_source_factory=failing_factory)
        await player.play(MusicTrack(video_id="x", title="Crash"))
        await asyncio.sleep(0.1)
        self.assertEqual(player.state, "failed")
        self.assertFalse(player.playing)

    async def test_status_idle(self):
        player, _ = make_player()
        status = player.status()
        self.assertEqual(status["state"], "idle")
        self.assertEqual(status["title"], "")


class MusicToolTests(unittest.IsolatedAsyncioTestCase):
    def make_provider(self, player=None):
        player = player or make_player()[0]
        return MusicToolProvider(player)

    async def test_search_returns_candidates(self):
        provider = self.make_provider()

        async def fake_search(query, max_results=5, timeout_s=20.0):
            self.assertIn("See Tình", query)
            return [{"video_id": "abc123", "title": "See Tình",
                     "channel": "Hoa Sen", "duration_s": 237}]

        with patch("core.tools.builtin.music_tool.ytdlp_search",
                   side_effect=fake_search):
            result = await provider.search_music({"query": "See Tình"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidates"][0]["video_id"], "abc123")

    async def test_search_empty_query_rejected(self):
        provider = self.make_provider()
        with self.assertRaises(ValueError):
            await provider.search_music({"query": "  "})

    async def test_search_not_found(self):
        provider = self.make_provider()

        async def fake_search(query, max_results=5, timeout_s=20.0):
            return []

        with patch("core.tools.builtin.music_tool.ytdlp_search",
                   side_effect=fake_search):
            result = await provider.search_music({"query": "zzz qqq"})
        self.assertEqual(result["status"], "not_found")

    async def test_play_needs_exact_video_id(self):
        provider = self.make_provider()
        with self.assertRaises(ValueError):
            await provider.play_music({})
        with self.assertRaises(ValueError):
            await provider.play_music({"query": "Song 1"})

    async def test_play_by_id_resolves_and_starts(self):
        sender = FakeSender()
        player, _ = make_player(sender,
                                frame_source_factory=sine_pcm24_chunks(0.3))
        provider = MusicToolProvider(player)

        async def fake_resolve(video_id, timeout_s=20.0):
            self.assertEqual(video_id, "abc123")
            return "http://example.invalid/audio.m4a"

        try:
            with patch("core.tools.builtin.music_tool.ytdlp_resolve_url",
                       side_effect=fake_resolve):
                result = await provider.play_music({"video_id": "abc123"})
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["video_id"], "abc123")
            await player.start_pending()
            self.assertTrue(player.playing)
            await asyncio.sleep(0.6)
            self.assertGreater(sender.binaries, 0)
        finally:
            await player.stop()

    async def test_search_then_play_uses_ai_selected_candidate(self):
        sender = FakeSender()
        player, _ = make_player(sender, frame_source_factory=sine_pcm24_chunks(0.3))
        provider = MusicToolProvider(player)

        async def fake_search(query, max_results=5, timeout_s=20.0):
            return [
                {"video_id": "song1", "title": "Song 1", "channel": "Artist", "duration_s": 180},
                {"video_id": "song2", "title": "Song 2", "channel": "Artist", "duration_s": 200},
            ]

        async def fake_resolve(video_id, timeout_s=20.0):
            self.assertEqual(video_id, "song2")
            return "http://example.invalid/audio.m4a"

        with patch("core.tools.builtin.music_tool.ytdlp_search", side_effect=fake_search), \
             patch("core.tools.builtin.music_tool.ytdlp_resolve_url", side_effect=fake_resolve):
            search = await provider.search_music({"query": "Song"})
            self.assertEqual(len(search["candidates"]), 2)
            result = await provider.play_music({"video_id": "song2"})
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["video_id"], "song2")
            self.assertEqual(result["title"], "Song 2")
            self.assertEqual(result["channel"], "Artist")

    async def test_control_unknown_action_rejected(self):
        provider = self.make_provider()
        with self.assertRaises(ValueError):
            await provider.control_music({"action": "dance"})

    async def test_control_status_and_stop(self):
        player, _ = make_player(frame_source_factory=sine_pcm24_chunks(5.0))
        provider = MusicToolProvider(player)
        try:
            self.assertEqual((await provider.control_music({"action": "status"}))["state"], "idle")
            await player.play(MusicTrack(video_id="x", title="T"))
            self.assertEqual((await provider.control_music({"action": "status"}))["state"], "playing")
            self.assertEqual((await provider.control_music({"action": "stop"}))["status"], "stopped")
        finally:
            await player.stop()

    def test_descriptors_register_cleanly(self):
        from core.tools.registry import ToolRegistry
        player, _ = make_player()
        registry = ToolRegistry(MusicToolProvider(player).descriptors())
        names = {d.name for d in registry.search("music", limit=10)}
        self.assertTrue({"music_search", "music_play", "music_control"} <= names)


class MusicSubprocessLifecycleTests(unittest.IsolatedAsyncioTestCase):
    class TimeoutProcess:
        def __init__(self):
            self.communicate_calls = 0
            self.kill_called = False
            self.returncode = None

        async def communicate(self):
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                await asyncio.Event().wait()
            self.returncode = -9
            return b"", b""

        async def wait(self):
            self.returncode = -9
            return self.returncode

        def kill(self):
            self.kill_called = True

    async def test_ytdlp_search_timeout_kills_and_reaps_child(self):
        proc = self.TimeoutProcess()

        async def fake_spawn(*_args, **_kwargs):
            return proc

        with patch("core.tools.builtin.music_tool.asyncio.create_subprocess_exec",
                   side_effect=fake_spawn):
            with self.assertRaisesRegex(TimeoutError, "music search timed out"):
                await ytdlp_search("song", timeout_s=0.01)
        self.assertTrue(proc.kill_called)
        self.assertEqual(proc.communicate_calls, 2)

    async def test_ytdlp_resolve_timeout_kills_and_reaps_child(self):
        proc = self.TimeoutProcess()

        async def fake_spawn(*_args, **_kwargs):
            return proc

        with patch("core.tools.builtin.music_tool.asyncio.create_subprocess_exec",
                   side_effect=fake_spawn):
            with self.assertRaisesRegex(TimeoutError, "music resolve timed out"):
                await ytdlp_resolve_url("abcdefghijk", timeout_s=0.01)
        self.assertTrue(proc.kill_called)
        self.assertEqual(proc.communicate_calls, 2)

    async def test_ffmpeg_forced_kill_is_reaped(self):
        class EmptyStdout:
            async def read(self, _size):
                return b""

        class FakeProcess:
            def __init__(self):
                self.stdout = EmptyStdout()
                self.returncode = None
                self.communicate_calls = 0
                self.terminate_called = False
                self.kill_called = False

            def terminate(self):
                self.terminate_called = True

            def kill(self):
                self.kill_called = True

            async def communicate(self):
                self.communicate_calls += 1
                if self.communicate_calls == 1:
                    raise asyncio.TimeoutError
                self.returncode = -9
                return b"", b""

        proc = FakeProcess()

        async def fake_spawn(*_args, **_kwargs):
            return proc

        track = MusicTrack(stream_url="http://example.invalid/audio")
        with patch("core.music_player.asyncio.create_subprocess_exec",
                   side_effect=fake_spawn):
            with self.assertRaisesRegex(RuntimeError, "produced no audio chunks"):
                async for _ in ffmpeg_pcm24_frames(track):
                    pass
        self.assertTrue(proc.terminate_called)
        self.assertTrue(proc.kill_called)
        self.assertEqual(proc.communicate_calls, 2)


class SessionAbortHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_abort_stops_music(self):
        from core.session import ClientSession

        class BareSession(ClientSession):
            def _create_asr(self):
                class _Asr:
                    async def stop(self):
                        return None
                return _Asr()

        import json as _json

        class StubSocket:
            async def send(self, payload):
                return True

        config = AppConfig()
        session = BareSession(StubSocket(), config, None, None)
        try:
            from core.music_player import MusicTrack
            player = session.music_player
            assert player is not None
            # Synthetic audio instead of ffmpeg.
            player._frame_source_factory = sine_pcm24_chunks(5.0)
            names = {d.name for d in session.tool_registry.search("music", limit=10)}
            self.assertTrue({"music_search", "music_play", "music_control"} <= names)
            await player.play(MusicTrack(video_id="x", title="T"))
            self.assertTrue(player.playing)
            await session._handle_text_json(_json.dumps({"type": "abort", "reason": "test"}))
            self.assertFalse(player.playing)
        finally:
            await session.close()

    async def test_listen_start_stops_music_and_closes_tts_envelope(self):
        from core.session import ClientSession

        class BareSession(ClientSession):
            def _create_asr(self):
                class _Asr:
                    async def stop(self):
                        return None

                    def invalidate_capture(self, capture_generation):
                        return None
                return _Asr()

        import json as _json

        class StubSocket:
            def __init__(self):
                self.sent = []

            async def send(self, payload):
                self.sent.append(payload)
                return True

        socket = StubSocket()
        session = BareSession(socket, AppConfig(), None, None)
        try:
            player = session.music_player
            player._frame_source_factory = sine_pcm24_chunks(5.0)
            await player.play(MusicTrack(video_id="x", title="T"))
            for _ in range(20):
                if player._envelope_active:
                    break
                await asyncio.sleep(0)
            self.assertTrue(player._envelope_active)

            await session._handle_text_json(_json.dumps({
                "type": "listen", "state": "start", "mode": "auto"
            }))

            self.assertFalse(player.playing)
            messages = [
                _json.loads(item) for item in socket.sent if isinstance(item, str)
            ]
            stops = [item for item in messages
                     if item.get("type") == "tts" and item.get("state") == "stop"]
            self.assertEqual(len(stops), 1)
        finally:
            await session.close()

    async def test_wake_detect_stops_music_and_closes_tts_envelope(self):
        from core.session import ClientSession

        class BareSession(ClientSession):
            def _create_asr(self):
                class _Asr:
                    async def stop(self):
                        return None

                    def invalidate_capture(self, capture_generation):
                        return None
                return _Asr()

        import json as _json

        class StubSocket:
            def __init__(self):
                self.sent = []

            async def send(self, payload):
                self.sent.append(payload)
                return True

        socket = StubSocket()
        config = AppConfig()
        config.conversation.wake_start_wait_ms = 1000
        session = BareSession(socket, config, None, None)
        try:
            player = session.music_player
            player._frame_source_factory = sine_pcm24_chunks(5.0)
            await player.play(MusicTrack(video_id="x", title="T"))
            for _ in range(20):
                if player._envelope_active:
                    break
                await asyncio.sleep(0)
            self.assertTrue(player._envelope_active)

            await session._handle_text_json(_json.dumps({
                "type": "listen", "state": "detect", "text": "VeeTee ơi"
            }))

            self.assertFalse(player.playing)
            messages = [
                _json.loads(item) for item in socket.sent if isinstance(item, str)
            ]
            stops = [item for item in messages
                     if item.get("type") == "tts" and item.get("state") == "stop"]
            self.assertEqual(len(stops), 1)
        finally:
            await session.close()

    async def test_music_tools_absent_when_disabled(self):
        from core.session import ClientSession

        class BareSession(ClientSession):
            def _create_asr(self):
                class _Asr:
                    async def stop(self):
                        return None
                return _Asr()

        class StubSocket:
            async def send(self, payload):
                return True

        config = AppConfig()
        config.music.enabled = False
        session = BareSession(StubSocket(), config, None, None)
        self.assertIsNone(session.music_tools)
        names = {d.name for d in session.tool_registry.search("", limit=64)}
        self.assertNotIn("music_play", names)
        await session.close()


class MusicDuckTests(unittest.IsolatedAsyncioTestCase):
    async def test_duck_pauses_and_unduck_resumes(self):
        from core.session import ClientSession

        class BareSession(ClientSession):
            def _create_asr(self):
                class _Asr:
                    async def stop(self):
                        return None
                return _Asr()

        class StubSocket:
            async def send(self, payload):
                return True

        session = BareSession(StubSocket(), AppConfig(), None, None)
        try:
            player = session.music_player
            player._frame_source_factory = sine_pcm24_chunks(5.0)
            # Idle: duck is a no-op.
            await session._duck_music_for_speech()
            self.assertFalse(session._music_ducked)
            # Playing: duck pauses and flags.
            await player.play(MusicTrack(video_id="x", title="T"))
            await session._duck_music_for_speech()
            self.assertTrue(session._music_ducked)
            self.assertEqual(player.state, "paused")
            # Turn settled: resume.
            await session._unduck_music()
            self.assertFalse(session._music_ducked)
            self.assertEqual(player.state, "playing")
            # Explicit stop wins over a stale duck flag.
            await session._duck_music_for_speech()
            await player.stop(announce=False)
            await session._unduck_music()
            self.assertEqual(player.state, "idle")
        finally:
            await session.close()


class MusicConfigTests(unittest.TestCase):
    def test_music_section_defaults(self):
        config = AppConfig()
        self.assertTrue(config.music.enabled)
        self.assertEqual(config.music.search_results, 5)

    def test_music_config_invalid_rejected(self):
        import tempfile
        import os
        doc = "music:\n  enabled: true\n  search_results: 99\n"
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8")
        handle.write(doc)
        handle.close()
        try:
            with self.assertRaises(ValueError):
                load_settings(handle.name)
        finally:
            os.unlink(handle.name)


if __name__ == "__main__":
    unittest.main()
