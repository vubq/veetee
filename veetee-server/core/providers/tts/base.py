from abc import ABC, abstractmethod
import asyncio
import inspect
from typing import AsyncGenerator, Optional

class BaseTTS(ABC):
    @abstractmethod
    async def stream_sentence_to_opus(
        self,
        text: str,
        cancel_event: Optional[asyncio.Event] = None,
        *,
        priority: str = "live",
        queue_deadline_seconds: Optional[float] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Synthesizes text and yields Opus-encoded audio frames in real time.
        """
        pass


def open_tts_stream(
    tts_engine,
    text: str,
    cancel_event: Optional[asyncio.Event],
    *,
    priority: str,
    queue_deadline_seconds: Optional[float] = None,
):
    """Call priority-aware TTS while preserving compatibility with simple test providers."""
    method = tts_engine.stream_sentence_to_opus
    try:
        parameters = inspect.signature(method).parameters.values()
        supports_kwargs = any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters)
        names = {item.name for item in parameters}
    except (TypeError, ValueError):
        supports_kwargs = False
        names = set()

    kwargs = {}
    if supports_kwargs or "priority" in names:
        kwargs["priority"] = priority
    if supports_kwargs or "queue_deadline_seconds" in names:
        kwargs["queue_deadline_seconds"] = queue_deadline_seconds
    return method(text, cancel_event, **kwargs)
