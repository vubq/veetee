"""Read-only provider catalog; secrets are never accepted or returned here."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .catalog import MODEL_CATALOG, PROVIDER_ID_BY_KIND
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
            "kind": kind,
            "provider_id": PROVIDER_ID_BY_KIND[kind],
            "models": list(MODEL_CATALOG[kind]),
            "secret_configurable": False,
        }
        for kind in ("asr", "llm", "tts")
    ]
