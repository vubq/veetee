"""Provider management and health endpoints for the Veetee control plane."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from veetee_server.persistence import ProviderRepository, record_audit

from .catalog import MODEL_CATALOG, PROVIDER_ID_BY_KIND
from .router import AdminActor, CurrentActor
from .schemas import ProviderResponse, ProviderStateUpdate

router = APIRouter(prefix="/api/v1/control", tags=["control-plane-providers"])


def _provider_repository(request: Request) -> ProviderRepository:
    repository = getattr(request.app.state, "provider_repository", None)
    if not isinstance(repository, ProviderRepository):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Persistence is not enabled",
        )
    return repository


ProviderRepositoryDependency = Annotated[ProviderRepository, Depends(_provider_repository)]


def _evaluate_health(request: Request, kind: str, provider_id: str) -> dict[str, Any]:
    runtime_attr = {
        "asr": "asr_runtime",
        "llm": "llm_runtime",
        "tts": "vieneu_runtime",
    }.get(kind)

    if runtime_attr:
        runtime = getattr(request.app.state, runtime_attr, None)
        if runtime is None or not hasattr(runtime, "is_ready"):
            return {"status": "unknown", "details": "Runtime is not active"}
        if not runtime.is_ready:
            return {"status": "degraded", "details": f"{kind} runtime is not ready"}
        return {"status": "ok", "details": "Runtime operational"}
    return {"status": "unknown", "details": "Active health probe is not supported"}


@router.get("/providers", response_model=list[ProviderResponse])
def list_providers(
    request: Request,
    actor: CurrentActor,
) -> list[dict[str, Any]]:
    repo = getattr(request.app.state, "provider_repository", None)
    stored_map = {}
    if repo is not None and isinstance(repo, ProviderRepository):
        for p in repo.list():
            stored_map[(p.provider_kind, p.provider_id)] = p

    result = []
    for kind in ("asr", "llm", "tts"):
        pid = PROVIDER_ID_BY_KIND[kind]
        stored = stored_map.get((kind, pid))
        enabled = stored.enabled if stored else True
        is_default = stored.is_default if stored else True
        version = stored.version if stored else 1
        health = _evaluate_health(request, kind, pid)

        result.append(
            {
                "kind": kind,
                "provider_id": pid,
                "models": list(MODEL_CATALOG[kind]),
                "enabled": enabled,
                "default": is_default,
                "is_default": is_default,
                "health": health,
                "config_version": version,
                "secret_configurable": False,
            }
        )
    return result


@router.patch("/providers/{kind}/{provider_id}", response_model=ProviderResponse)
def update_provider_state(
    kind: str,
    provider_id: str,
    payload: ProviderStateUpdate,
    request: Request,
    actor: AdminActor,
    repository: ProviderRepositoryDependency,
) -> dict[str, Any]:
    if kind not in PROVIDER_ID_BY_KIND or PROVIDER_ID_BY_KIND[kind] != provider_id:
        raise HTTPException(status_code=404, detail="Provider not found in catalog")

    updated, error = repository.update_state(
        actor_user_id=actor.user_id,
        provider_kind=kind,
        provider_id=provider_id,
        expected_version=payload.expected_version,
        enabled=payload.enabled,
        is_default=payload.is_default,
    )

    if updated is None:
        if error == "not_found":
            raise HTTPException(status_code=404, detail="Provider state not found")
        if error == "conflict":
            raise HTTPException(
                status_code=409, detail="Provider state changed or version mismatch"
            )
        if error in ("cannot_disable_default", "cannot_unset_default", "default_must_be_enabled"):
            raise HTTPException(status_code=409, detail=error.replace("_", " ").capitalize())
        raise HTTPException(status_code=400, detail="Invalid provider state modification")

    health = _evaluate_health(request, kind, provider_id)
    return {
        "kind": kind,
        "provider_id": provider_id,
        "models": list(MODEL_CATALOG[kind]),
        "enabled": updated.enabled,
        "default": updated.is_default,
        "is_default": updated.is_default,
        "health": health,
        "config_version": updated.version,
        "secret_configurable": False,
    }


@router.post("/providers/{kind}/{provider_id}/health-check")
def check_provider_health(
    kind: str,
    provider_id: str,
    request: Request,
    actor: AdminActor,
) -> dict[str, Any]:
    if kind not in PROVIDER_ID_BY_KIND or PROVIDER_ID_BY_KIND[kind] != provider_id:
        raise HTTPException(status_code=404, detail="Provider not found in catalog")

    health = _evaluate_health(request, kind, provider_id)
    database = getattr(request.app.state, "database", None)
    if database is not None:
        record_audit(
            database,
            actor.user_id,
            "provider.health_check",
            "provider",
            f"{kind}:{provider_id}",
            {"status": health["status"]},
        )
    return {
        "kind": kind,
        "provider_id": provider_id,
        "health": health,
    }
