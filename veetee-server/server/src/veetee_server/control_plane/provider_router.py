"""Read-only provider catalog; secrets are never accepted or returned here."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .router import AgentRepositoryDependency, CurrentUser

router = APIRouter(prefix="/api/v1/control", tags=["control-plane-providers"])


@router.get("/providers")
def list_providers(
    user_id: CurrentUser,
    repository: AgentRepositoryDependency,
) -> list[dict[str, Any]]:
    del user_id, repository
    return [
        {
            "kind": "asr",
            "provider_id": "pho_whisper",
            "models": ["mad1999/pho-whisper-small-ct2"],
            "secret_configurable": False,
        },
        {
            "kind": "llm",
            "provider_id": "omniroute",
            "models": ["groq/openai/gpt-oss-120b", "groq/qwen/qwen3.6-27b"],
            "secret_configurable": False,
        },
        {
            "kind": "tts",
            "provider_id": "vieneu",
            "models": ["local"],
            "secret_configurable": False,
        },
    ]
