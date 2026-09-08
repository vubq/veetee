from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from core.intent import Intent
from core.turn_events import (
    CompletedEvent,
    ControlEvent,
    FailedEvent,
    SpeechSegmentEvent,
    ToolCallReadyEvent,
    TurnEvent,
)


class TurnRunner:
    """Adapt provider streams to one typed turn-event contract.

    The compatibility adapter never launches a second classifier call. Providers
    without inline control simply run as a normal continuing chat turn.
    """

    def __init__(self, llm):
        self.llm = llm

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict]] = None,
        detect_end_intent: bool = True,
        tool_choice: Optional[str] = None,
        first_event_timeout_ms: Optional[int] = None,
        total_timeout_ms: Optional[int] = None,
    ) -> AsyncGenerator[TurnEvent, None]:
        native = getattr(self.llm, "stream_turn", None)
        if callable(native):
            kwargs = {
                "tools": tools or [],
                "detect_end_intent": detect_end_intent,
            }
            if tool_choice is not None:
                try:
                    parameters = inspect.signature(native).parameters.values()
                    supports_kwargs = any(
                        item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters
                    )
                    names = {item.name for item in parameters}
                except (TypeError, ValueError):
                    supports_kwargs = False
                    names = set()
                if supports_kwargs or "tool_choice" in names:
                    kwargs["tool_choice"] = tool_choice
            source = native(messages, **kwargs)
            async for event in self._with_deadlines(
                source,
                first_event_timeout_ms=first_event_timeout_ms,
                total_timeout_ms=total_timeout_ms,
                first_event_predicate=lambda event: isinstance(
                    event,
                    (SpeechSegmentEvent, ToolCallReadyEvent, CompletedEvent, FailedEvent),
                ),
            ):
                yield event
            return

        inline = getattr(self.llm, "stream_chat_with_control", None) if detect_end_intent else None
        stream = inline(messages) if callable(inline) else self.llm.stream_chat(messages)
        control_emitted = False
        async for item in self._with_deadlines(
            stream,
            first_event_timeout_ms=first_event_timeout_ms,
            total_timeout_ms=total_timeout_ms,
        ):
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

    @staticmethod
    async def _with_deadlines(
        stream,
        *,
        first_event_timeout_ms: Optional[int],
        total_timeout_ms: Optional[int],
        first_event_predicate=None,
    ):
        """Iterate an async stream with cancellable first-event/total deadlines."""
        iterator = stream.__aiter__()
        started = time.monotonic()
        awaiting_first = first_event_timeout_ms is not None
        first_deadline = (
            started + max(0.001, first_event_timeout_ms / 1000.0)
            if first_event_timeout_ms is not None
            else None
        )
        try:
            while True:
                timeout: Optional[float] = None
                now = time.monotonic()
                if total_timeout_ms is not None:
                    remaining = total_timeout_ms / 1000.0 - (now - started)
                    if remaining <= 0:
                        raise asyncio.TimeoutError("LLM total turn timeout")
                    timeout = remaining
                if awaiting_first and first_deadline is not None:
                    first_remaining = first_deadline - now
                    if first_remaining <= 0:
                        raise asyncio.TimeoutError("LLM first usable event timeout")
                    timeout = first_remaining if timeout is None else min(timeout, first_remaining)
                try:
                    if timeout is None:
                        event = await anext(iterator)
                    else:
                        event = await asyncio.wait_for(anext(iterator), timeout=timeout)
                except StopAsyncIteration:
                    return
                except asyncio.TimeoutError as exc:
                    label = "first usable event" if awaiting_first else "total turn"
                    raise asyncio.TimeoutError(f"LLM {label} timeout") from exc
                if awaiting_first:
                    usable = (
                        True
                        if first_event_predicate is None
                        else bool(first_event_predicate(event))
                    )
                    if usable:
                        awaiting_first = False
                yield event
        finally:
            aclose = getattr(iterator, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except RuntimeError:
                    pass
