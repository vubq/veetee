"""Shared backend model catalog used by provider listings and API validation.

This is the single backend allowlist for ``model_id`` values accepted on agent
configuration. The read-only provider endpoint and input validation must both
derive from these constants so they cannot drift apart.
"""

from __future__ import annotations

# kind -> registered backend model ids. Keep in sync with the adapters actually
# wired in the pipeline factory; unknown models are rejected at the API edge.
MODEL_CATALOG: dict[str, tuple[str, ...]] = {
    "asr": ("mad1999/pho-whisper-small-ct2",),
    "llm": ("groq/openai/gpt-oss-120b", "groq/qwen/qwen3.6-27b"),
    "tts": ("local",),
}

PROVIDER_ID_BY_KIND: dict[str, str] = {
    "asr": "pho_whisper",
    "llm": "omniroute",
    "tts": "vieneu",
}


def allowed_agent_model_ids() -> frozenset[str]:
    """LLM model ids accepted by the agent ``model_id`` field."""
    return frozenset(MODEL_CATALOG["llm"])
