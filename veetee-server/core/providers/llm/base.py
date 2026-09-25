from abc import ABC, abstractmethod
from typing import Any, List, Dict, AsyncGenerator, Tuple, Optional

from core.intent import Intent
from core.turn_events import CompletedEvent, ControlEvent, SpeechSegmentEvent, TurnEvent

class BaseLLM(ABC):
    def capabilities(self) -> Dict[str, Any]:
        """Describe runtime features without probing the provider.

        Capability metadata is operational data only; semantic routing remains
        inside the model/tool contract.
        """
        return {
            "streaming": True,
            "native_tools": type(self).stream_turn is not BaseLLM.stream_turn,
            "history_summary": type(self).summarize_history is not BaseLLM.summarize_history,
            "transcript_correction": type(self).correct_transcript is not BaseLLM.correct_transcript,
        }

    def health(self) -> Dict[str, Any]:
        return {
            "available": not bool(getattr(self, "permanent_unavailable", False)),
            "provider": type(self).__name__,
            "model": str(getattr(self, "model", "") or ""),
            "capabilities": self.capabilities(),
        }

    async def correct_transcript(self, transcript: str) -> str:
        """Optionally clean up a final ASR transcript before the AI turn."""
        return transcript


    async def generate_recovery_message(self) -> str:
        """Generate one short startup-time message for cached turn recovery."""
        return ""

    async def summarize_history(
        self,
        turns: List[Dict[str, Any]],
        *,
        previous_summary: str = "",
        max_chars: int = 1200,
    ) -> str:
        """Optionally summarize older turns off the latency-critical path."""
        del turns, previous_summary, max_chars
        return ""

    async def stream_turn(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict]] = None,
        detect_end_intent: bool = True,
        tool_choice: Optional[Any] = None,
    ) -> AsyncGenerator[TurnEvent, None]:
        """Compatibility typed-turn adapter.

        Providers can override this method to support native streamed tool calls.
        The default path preserves the one-inference chat behavior and never
        invokes the legacy classifier helper.
        """
        del tools, tool_choice
        inline = getattr(self, "stream_chat_with_control", None) if detect_end_intent else None
        stream = inline(messages) if callable(inline) else self.stream_chat(messages)
        control_emitted = False
        async for item in stream:
            if isinstance(item, tuple) and len(item) == 3:
                text, emotion, should_end = item
            else:
                text, emotion = item
                should_end = False
            if not control_emitted:
                yield ControlEvent(
                    intent=Intent.END_CONVERSATION.value if should_end else Intent.CHAT.value,
                    lifecycle="end" if should_end else "continue",
                    emotion=emotion or "neutral",
                )
                control_emitted = True
            if text:
                yield SpeechSegmentEvent(str(text), emotion=emotion)
        if not control_emitted:
            yield ControlEvent()
        yield CompletedEvent()

    @abstractmethod
    async def stream_chat(
        self,
        messages: List[Dict[str, str]]
    ) -> AsyncGenerator[Tuple[str, Optional[str]], None]:
        """
        Streams generated clauses/sentences from the LLM.
        Yields (clause_text, emotion_tag_or_none).
        """
        pass
