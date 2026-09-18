"""Per-session assistant views over shared heavy AI providers."""
from __future__ import annotations

from typing import Any, Optional


class AssistantTTSView:
    def __init__(self, base_engine: Any, voice: str = ""):
        self._base = base_engine
        self.voice = (voice or getattr(base_engine, "voice", "")).strip()
        self.sample_rate = getattr(base_engine, "sample_rate", 24000)
        self.frame_duration_ms = getattr(base_engine, "frame_duration_ms", 60)

    async def stream_sentence_to_opus(self, text, cancel_event=None, *, priority="live",
                                      queue_deadline_seconds=None):
        method = getattr(self._base, "stream_sentence_to_opus")
        try:
            async for frame in method(
                text,
                cancel_event,
                priority=priority,
                queue_deadline_seconds=queue_deadline_seconds,
                voice_override=self.voice or None,
            ):
                yield frame
        except TypeError:
            # Legacy/test engines don't accept voice_override.
            async for frame in method(
                text,
                cancel_event,
                priority=priority,
                queue_deadline_seconds=queue_deadline_seconds,
            ):
                yield frame

    async def synthesize_wav(self, text: str, **kwargs):
        return await self._base.synthesize_wav(text, **kwargs)

    def __getattr__(self, name):
        return getattr(self._base, name)


def build_assistant_llm_view(base_engine: Any, assistant: Optional[dict], config) -> Any:
    """Create a lightweight LLM view sharing transport/quota but isolating model/persona."""
    if not assistant:
        return base_engine
    model = str(assistant.get("model") or getattr(base_engine, "model", "")).strip()
    prompt = str(assistant.get("base_prompt") or "").strip()
    try:
        from core.providers.llm.groq_direct import GroqDirectLLM, engine_for_model
        if isinstance(base_engine, GroqDirectLLM):
            view = engine_for_model(base_engine, model or base_engine.model)
            if prompt:
                view.set_base_prompt(
                    prompt,
                    persist=False,
                    max_bytes=config.llm.base_prompt_max_bytes,
                    max_tokens_estimate=config.llm.base_prompt_max_tokens,
                    chars_per_token=config.latency.context_chars_per_token,
                )
            return view
    except Exception:
        # Fallback below keeps compatibility with alternate/test providers.
        pass
    return base_engine


def build_assistant_tts_view(base_engine: Any, assistant: Optional[dict]) -> Any:
    if not assistant:
        return base_engine
    return AssistantTTSView(base_engine, str(assistant.get("voice") or ""))
