"""Tenant-scoped correction rules and context-provider configuration API."""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from veetee_server.correction.engine import CorrectionEngine
from veetee_server.persistence import (
    ContextProviderConfigRepository,
    CorrectionRepository,
    StoredContextProviderConfig,
    StoredCorrectionRule,
    StoredCorrectionSet,
)

from .router import CurrentUser
from .schemas import (
    ContextProviderConfigUpdate,
    CorrectionPreviewRequest,
    CorrectionRuleCreate,
    CorrectionSetCreate,
    CorrectionSetUpdate,
)

router = APIRouter(prefix="/api/v1/control", tags=["control-plane-context"])


def _correction_repository(request: Request) -> CorrectionRepository:
    repository = getattr(request.app.state, "correction_repository", None)
    if not isinstance(repository, CorrectionRepository):
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return repository


def _context_repository(request: Request) -> ContextProviderConfigRepository:
    repository = getattr(request.app.state, "context_provider_config_repository", None)
    if not isinstance(repository, ContextProviderConfigRepository):
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return repository


CorrectionDependency = Annotated[CorrectionRepository, Depends(_correction_repository)]
ContextDependency = Annotated[ContextProviderConfigRepository, Depends(_context_repository)]


def _set_dict(item: StoredCorrectionSet) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "agent_id": str(item.agent_id) if item.agent_id else None,
        "name": item.name,
        "enabled": item.enabled,
        "version": item.version,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _rule_dict(item: StoredCorrectionRule) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "set_id": str(item.set_id),
        "ordinal": item.ordinal,
        "rule_type": item.rule_type,
        "pattern": item.pattern,
        "replacement": item.replacement,
        "case_sensitive": item.case_sensitive,
        "enabled": item.enabled,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _provider_dict(item: StoredContextProviderConfig) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "agent_id": str(item.agent_id),
        "provider_type": item.provider_type,
        "enabled": item.enabled,
        "ordinal": item.ordinal,
        "timeout_ms": item.timeout_ms,
        "cache_ttl_seconds": item.cache_ttl_seconds,
        "version": item.version,
        "config": item.config,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="Resource not found")
    if isinstance(exc, ValueError) and (
        "already exists" in str(exc) or "Optimistic lock" in str(exc)
    ):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Unexpected repository failure")


@router.post("/corrections/sets", status_code=201)
def create_set(
    payload: CorrectionSetCreate,
    user_id: CurrentUser,
    repository: CorrectionDependency,
) -> dict[str, Any]:
    try:
        return _set_dict(
            repository.create_set(user_id, payload.name, payload.agent_id, payload.enabled)
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/corrections/sets")
def list_sets(
    user_id: CurrentUser, repository: CorrectionDependency
) -> list[dict[str, Any]]:
    return [_set_dict(item) for item in repository.list_sets(user_id)]


@router.patch("/corrections/sets/{set_id}")
def update_set(
    set_id: UUID,
    payload: CorrectionSetUpdate,
    user_id: CurrentUser,
    repository: CorrectionDependency,
) -> dict[str, Any]:
    try:
        return _set_dict(
            repository.update_set(
                user_id,
                set_id,
                name=payload.name,
                enabled=payload.enabled,
                expected_version=payload.expected_version,
            )
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.delete("/corrections/sets/{set_id}", status_code=204)
def delete_set(
    set_id: UUID, user_id: CurrentUser, repository: CorrectionDependency
) -> None:
    if not repository.delete_set(user_id, set_id):
        raise HTTPException(status_code=404, detail="Correction set not found")


@router.post("/corrections/sets/{set_id}/rules", status_code=201)
def add_rule(
    set_id: UUID,
    payload: CorrectionRuleCreate,
    user_id: CurrentUser,
    repository: CorrectionDependency,
) -> dict[str, Any]:
    try:
        rule = repository.add_rule(
            user_id,
            set_id,
            payload.ordinal,
            payload.rule_type,
            payload.pattern,
            payload.replacement,
            payload.case_sensitive,
            payload.enabled,
            payload.expected_set_version,
        )
        return _rule_dict(rule)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/corrections/sets/{set_id}/rules")
def list_rules(
    set_id: UUID, user_id: CurrentUser, repository: CorrectionDependency
) -> list[dict[str, Any]]:
    if repository.get_set(user_id, set_id) is None:
        raise HTTPException(status_code=404, detail="Correction set not found")
    return [_rule_dict(item) for item in repository.list_rules(user_id, set_id)]


@router.post("/corrections/sets/{set_id}/preview")
def preview_set(
    set_id: UUID,
    payload: CorrectionPreviewRequest,
    user_id: CurrentUser,
    repository: CorrectionDependency,
) -> dict[str, Any]:
    correction_set = repository.get_set(user_id, set_id)
    if correction_set is None:
        raise HTTPException(status_code=404, detail="Correction set not found")
    corrected, applied = CorrectionEngine().apply_rules(
        payload.text, repository.list_rules(user_id, set_id)
    )
    return {
        "set_id": str(set_id),
        "version": correction_set.version,
        "original_text": payload.text,
        "corrected_text": corrected,
        "applied_rules": applied,
    }


@router.delete("/corrections/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: UUID, user_id: CurrentUser, repository: CorrectionDependency
) -> None:
    if not repository.delete_rule(user_id, rule_id):
        raise HTTPException(status_code=404, detail="Correction rule not found")


@router.get("/agents/{agent_id}/context-providers")
def list_context_providers(
    agent_id: UUID, user_id: CurrentUser, repository: ContextDependency
) -> list[dict[str, Any]]:
    return [_provider_dict(item) for item in repository.list_agent_configs(user_id, agent_id)]


@router.put("/agents/{agent_id}/context-providers/{provider_type}")
def put_context_provider(
    agent_id: UUID,
    provider_type: str,
    payload: ContextProviderConfigUpdate,
    user_id: CurrentUser,
    repository: ContextDependency,
) -> dict[str, Any]:
    try:
        typed_provider = cast(
            Literal["runtime", "memory", "knowledge_fts", "weather"], provider_type
        )
        return _provider_dict(
            repository.upsert_config(
                user_id,
                agent_id,
                typed_provider,
                payload.enabled,
                payload.ordinal,
                payload.timeout_ms,
                payload.cache_ttl_seconds,
                payload.config,
            )
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.delete("/agents/{agent_id}/context-providers/{provider_type}", status_code=204)
def delete_context_provider(
    agent_id: UUID,
    provider_type: str,
    user_id: CurrentUser,
    repository: ContextDependency,
) -> None:
    if not repository.delete_config(user_id, agent_id, provider_type):
        raise HTTPException(status_code=404, detail="Context provider config not found")
