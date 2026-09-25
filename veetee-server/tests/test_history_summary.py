import asyncio
import unittest

from config.settings import AppConfig
from core.dialogue import DialogueContext
from core.session import ClientSession


class _Socket:
    async def send(self, payload):
        return True


class _ASR:
    async def start(self):
        return None

    async def stop(self):
        return None

    async def send_audio(self, pcm_bytes, capture_generation=None):
        return None

    async def finalize(self):
        return None

    def invalidate_capture(self, capture_generation):
        return None


class _TTS:
    async def stream_sentence_to_opus(self, text, cancel_event=None, **kwargs):
        if False:
            yield b""


class _SummaryLLM:
    def __init__(self):
        self.calls = 0
        self.cancelled = False
        self.block = False

    async def summarize_history(self, turns, *, previous_summary="", max_chars=1200):
        self.calls += 1
        if self.block:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return "Người dùng đang phát triển VeeTee và ưu tiên phản hồi nhanh."

    async def stream_chat(self, messages):
        if False:
            yield "", None


class _Session(ClientSession):
    def _create_asr(self):
        return _ASR()


def _record_turn(dialogue, index):
    dialogue.record_structured_turn(
        turn_id=f"t{index}",
        user_text=f"user {index}",
        assistant_text=f"assistant {index}",
        receipts=[],
        playback="sent",
    )


class DialogueSummaryTests(unittest.TestCase):
    def test_summary_snapshot_and_revision_guard(self):
        dialogue = DialogueContext(max_history_turns=6)
        for index in range(5):
            _record_turn(dialogue, index)

        self.assertTrue(
            dialogue.history_summary_due(
                high_water_turns=5,
                min_new_turns=3,
            )
        )
        revision, _summary_revision, turns, previous = dialogue.summary_snapshot(
            keep_recent_turns=2
        )
        self.assertEqual(len(turns), 3)
        self.assertEqual(previous, "")
        self.assertTrue(
            dialogue.commit_history_summary(
                "Tóm tắt cũ.",
                snapshot_revision=revision,
            )
        )

        _record_turn(dialogue, 6)
        self.assertFalse(
            dialogue.commit_history_summary(
                "Kết quả stale.",
                snapshot_revision=revision,
            )
        )
        self.assertEqual(dialogue.history_summary, "Tóm tắt cũ.")

    def test_summary_is_context_data_not_a_replacement_for_recent_messages(self):
        dialogue = DialogueContext(max_history_turns=6)
        dialogue.history_summary = "Người dùng thích phản hồi ngắn."
        dialogue.add_user_message("Câu hiện tại")

        messages = dialogue.get_messages_for_llm()

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("dữ liệu", messages[0]["content"])
        self.assertEqual(messages[-1], {"role": "user", "content": "Câu hiện tại"})


class SessionHistorySummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_summary_commits_without_blocking_turn_path(self):
        config = AppConfig()
        config.conversation.history_summary_enabled = True
        config.conversation.history_summary_high_water_turns = 3
        config.conversation.history_summary_min_new_turns = 1
        config.conversation.history_summary_defer_ms = 0
        llm = _SummaryLLM()
        session = _Session(_Socket(), config, _TTS(), llm)
        try:
            for index in range(4):
                _record_turn(session.dialogue, index)
            session._schedule_history_summary()
            task = session._history_summary_task
            self.assertIsNotNone(task)
            await asyncio.wait_for(task, timeout=1)
            self.assertEqual(llm.calls, 1)
            self.assertIn("VeeTee", session.dialogue.history_summary)
        finally:
            await session.close(close_transport=False)

    async def test_new_activity_can_cancel_background_summary(self):
        config = AppConfig()
        config.conversation.history_summary_enabled = True
        config.conversation.history_summary_high_water_turns = 3
        config.conversation.history_summary_min_new_turns = 1
        config.conversation.history_summary_defer_ms = 0
        llm = _SummaryLLM()
        llm.block = True
        session = _Session(_Socket(), config, _TTS(), llm)
        try:
            for index in range(4):
                _record_turn(session.dialogue, index)
            session._schedule_history_summary()
            task = session._history_summary_task
            self.assertIsNotNone(task)
            for _ in range(50):
                if llm.calls:
                    break
                await asyncio.sleep(0.001)
            session._cancel_history_summary()
            await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(llm.cancelled)
            self.assertEqual(session.dialogue.history_summary, "")
        finally:
            await session.close(close_transport=False)


if __name__ == "__main__":
    unittest.main()
