"""Fail-closed LLM facade used while provider credentials/config are unavailable."""
from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from core.providers.llm.base import BaseLLM


class UnavailableLLM(BaseLLM):
    """Keep management/OTA online while AI runtime is unavailable.

    User turns fail explicitly instead of preventing the server from booting.
    Runtime configuration can then be repaired through the management plane.
    """

    permanent_unavailable = True

    def __init__(self, *, model: str, reason: str = "llm_unavailable", extra_models=None):
        self.model = str(model or "").strip()
        self.reason = str(reason or "llm_unavailable").strip()
        self._models = [m for m in [self.model, *(extra_models or [])] if str(m).strip()]

    def capabilities(self) -> Dict[str, Any]:
        return {
            "streaming": False,
            "native_tools": False,
            "history_summary": False,
            "transcript_correction": False,
        }

    def health(self) -> Dict[str, Any]:
        return {
            "available": False,
            "provider": "unavailable",
            "model": self.model,
            "reason": self.reason,
            "capabilities": self.capabilities(),
        }

    async def warmup(self) -> None:
        raise RuntimeError(self.reason)

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
    ) -> AsyncGenerator[Tuple[str, Optional[str]], None]:
        del messages
        raise RuntimeError(self.reason)
        yield "", None  # pragma: no cover

    async def generate_recovery_message(self) -> str:
        return ""

    def list_models(self) -> list[str]:
        return list(dict.fromkeys(str(model) for model in self._models if str(model).strip()))

    def set_model(self, model: str, persist: bool = False) -> str:
        del persist
        model = str(model or "").strip()
        if not model:
            raise ValueError("model is required")
        if model not in self._models:
            self._models.append(model)
        self.model = model
        return model

    def set_base_prompt(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def close(self) -> None:
        return None
