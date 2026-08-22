"""Tenant-scoped agent lifecycle and conversation history API (M6.2).

All endpoints operate strictly inside the authenticated tenant. Conversation
transcripts are opt-in (versioned consent) and are never mixed with system
prompts, secrets or tokens: turns persist only the text fields the pipeline
produced plus tool metadata/provenance supplied by the server itself.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from veetee_server.persistence import (
    AgentLifecycleRepository,
    ConversationRepository,
    purge_expired_conversations,
    record_audit,
)

from .router import AdminActor, CurrentUser
from .schemas import (
    AgentConfig,
    AgentFromTemplateCreate,
    AgentTemplateCreate,
    ConversationUpdate,
    RetentionPurgeRequest,
    SnapshotCreate,
    SnapshotRestore,
    TagCreate,
)

router = APIRouter(prefix="/api/v1/control", tags=["control-plane-history"])


def _lifecycle_repository(request: Request) -> AgentLifecycleRepository:
    repository = getattr(request.app.state, "lifecycle_repository", None)
    if not isinstance(repository, AgentLifecycleRepository):
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return repository


def _conversation_repository(request: Request) -> ConversationRepository:
    repository = getattr(request.app.state, "conversation_repository", None)
    if not isinstance(repository, ConversationRepository):
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return repository


LifecycleRepositoryDependency = Annotated[
    AgentLifecycleRepository, Depends(_lifecycle_repository)
]
ConversationRepositoryDependency = Annotated[
    ConversationRepository, Depends(_conversation_repository)
]


def _database(request: Request) -> Any:
    return getattr(request.app.state, "database", None)


def _validated_template_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validates a template payload against the current agent schema.

    The template's own name is unrelated to the per-agent ``name`` field, so a
    placeholder is used during validation and stripped from the stored config.
    """
    try:
        validated = AgentConfig.model_validate({"name": "-", **config})
    except ValidationError:
        raise HTTPException(
            status_code=422,
            detail="config must match the current agent schema",
        ) from None
    dumped = validated.model_dump()
    dumped.pop("name", None)
    return dumped


def _snapshot_restore_validator(config: dict[str, Any]) -> dict[str, Any] | None:
    """Maps an immutable snapshot config through the current schema."""
    try:
        return AgentConfig.model_validate(config).model_dump()
    except ValidationError:
        return None


# ------------------------------------------------------------------ templates


@router.get("/templates")
def list_templates(
    user_id: CurrentUser, repository: LifecycleRepositoryDependency
) -> list[dict[str, Any]]:
    return [template.to_dict() for template in repository.list_templates(user_id)]


@router.post("/templates", status_code=201)
def create_template(
    payload: AgentTemplateCreate,
    user_id: CurrentUser,
    repository: LifecycleRepositoryDependency,
) -> dict[str, Any]:
    config = _validated_template_config(payload.config)
    template, error = repository.create_template(
        user_id, payload.name, payload.description, config
    )
    if template is None or error == "duplicate_name":
        raise HTTPException(status_code=409, detail="Template name already exists")
    return template.to_dict()


@router.post("/templates/{template_id}/agents", status_code=201)
def create_agent_from_template(
    template_id: UUID,
    payload: AgentFromTemplateCreate,
    user_id: CurrentUser,
    repository: LifecycleRepositoryDependency,
) -> dict[str, Any]:
    stored, error = repository.create_agent_from_template(user_id, template_id, payload.name)
    if error == "template_not_found":
        raise HTTPException(status_code=404, detail="Template not found")
    if error == "duplicate_name":
        raise HTTPException(status_code=409, detail="Agent name already exists")
    assert stored is not None
    return stored.to_dict()


# ----------------------------------------------------------------------- tags


@router.get("/tags")
def list_tags(
    user_id: CurrentUser, repository: LifecycleRepositoryDependency
) -> list[dict[str, Any]]:
    return [tag.to_dict() for tag in repository.list_tags(user_id)]


@router.post("/tags", status_code=201)
def create_tag(
    payload: TagCreate,
    user_id: CurrentUser,
    repository: LifecycleRepositoryDependency,
) -> dict[str, Any]:
    tag, error = repository.create_tag(user_id, payload.name)
    if tag is None or error == "duplicate_name":
        raise HTTPException(status_code=409, detail="Tag name already exists")
    return tag.to_dict()


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(
    tag_id: UUID, user_id: CurrentUser, repository: LifecycleRepositoryDependency
) -> None:
    if not repository.delete_tag(user_id, tag_id):
        raise HTTPException(status_code=404, detail="Tag not found")


@router.put("/agents/{agent_id}/tags/{tag_id}", status_code=204)
def attach_tag(
    agent_id: UUID,
    tag_id: UUID,
    user_id: CurrentUser,
    request: Request,
    repository: LifecycleRepositoryDependency,
) -> None:
    if not repository.attach_tag(user_id, agent_id, tag_id):
        raise HTTPException(status_code=404, detail="Tag or agent not found")
    record_audit(
        _database(request),
        user_id,
        "agent.tag.attach",
        "agent_tag",
        str(tag_id),
        {"agent_id": str(agent_id)},
    )


@router.delete("/agents/{agent_id}/tags/{tag_id}", status_code=204)
def detach_tag(
    agent_id: UUID,
    tag_id: UUID,
    user_id: CurrentUser,
    request: Request,
    repository: LifecycleRepositoryDependency,
) -> None:
    if not repository.detach_tag(user_id, agent_id, tag_id):
        raise HTTPException(status_code=404, detail="Tag link not found")
    record_audit(
        _database(request),
        user_id,
        "agent.tag.detach",
        "agent_tag",
        str(tag_id),
        {"agent_id": str(agent_id)},
    )


# ------------------------------------------------------------------ snapshots


@router.get("/agents/{agent_id}/snapshots")
def list_snapshots(
    agent_id: UUID,
    user_id: CurrentUser,
    repository: LifecycleRepositoryDependency,
) -> list[dict[str, Any]]:
    return [snapshot.to_dict() for snapshot in repository.list_snapshots(user_id, agent_id)]


@router.post("/agents/{agent_id}/snapshots", status_code=201)
def create_snapshot(
    agent_id: UUID,
    payload: SnapshotCreate,
    user_id: CurrentUser,
    repository: LifecycleRepositoryDependency,
) -> dict[str, Any]:
    snapshot = repository.create_snapshot(
        user_id, agent_id, reason=payload.reason, created_by=user_id
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return snapshot.to_dict()


@router.post("/agents/{agent_id}/snapshots/{snapshot_id}/restore")
def restore_snapshot(
    agent_id: UUID,
    snapshot_id: UUID,
    payload: SnapshotRestore,
    user_id: CurrentUser,
    request: Request,
    repository: LifecycleRepositoryDependency,
) -> dict[str, Any]:
    restored, error = repository.restore_snapshot(
        user_id,
        agent_id,
        snapshot_id,
        payload.expected_agent_version,
        _snapshot_restore_validator,
        actor_user_id=user_id,
    )
    if error in {"agent_not_found", "snapshot_not_found"}:
        raise HTTPException(status_code=404, detail=error.replace("_", " ").capitalize())
    if error == "stale_version":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent changed or version mismatch",
        )
    if error == "invalid_config":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Snapshot config does not match the current agent schema",
        )
    if error == "duplicate_name":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Agent name already exists"
        )
    assert restored is not None
    return restored.to_dict()


# -------------------------------------------------------------- conversations


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: UUID,
    user_id: CurrentUser,
    repository: ConversationRepositoryDependency,
) -> dict[str, Any]:
    conversation = repository.get(user_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation.to_dict()


@router.get("/conversations/{conversation_id}/turns")
def list_turns(
    conversation_id: UUID,
    user_id: CurrentUser,
    repository: ConversationRepositoryDependency,
) -> list[dict[str, Any]]:
    if repository.get(user_id, conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return [turn.to_dict() for turn in repository.list_turns(user_id, conversation_id)]


@router.patch("/conversations/{conversation_id}")
def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    user_id: CurrentUser,
    request: Request,
    repository: ConversationRepositoryDependency,
) -> dict[str, Any]:
    if payload.transcript_consent and payload.consent_version is None:
        raise HTTPException(
            status_code=422,
            detail="consent_version is required when enabling transcript consent",
        )
    updated = repository.update(
        user_id,
        conversation_id,
        title=payload.title,
        summary=payload.summary,
        transcript_consent=payload.transcript_consent,
        consent_version=payload.consent_version,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if payload.transcript_consent is not None or payload.consent_version is not None:
        record_audit(
            _database(request),
            user_id,
            "conversation.consent_update",
            "conversation",
            str(conversation_id),
            {
                "transcript_consent": updated.transcript_consent,
                "consent_version": updated.consent_version,
            },
        )
    return updated.to_dict()


EXPORT_SCHEMA_ID = "veetee.conversation.export.v1"


def _export_stream(
    repository: ConversationRepository,
    owner_user_id: UUID,
    conversation_id: UUID,
    conversation_json: str,
) -> Iterator[str]:
    exported_at = datetime.now(UTC).isoformat()
    yield (
        f'{{"schema":"{EXPORT_SCHEMA_ID}","exported_at":"{exported_at}",'
        f'"conversation":{conversation_json},"turns":['
    )
    first = True
    for turn in repository.list_turns(owner_user_id, conversation_id):
        prefix = "" if first else ","
        yield f"{prefix}{json.dumps(turn.to_dict(), ensure_ascii=False)}"
        first = False
    yield "]}"


@router.get("/conversations/{conversation_id}/export")
def export_conversation(
    conversation_id: UUID,
    user_id: CurrentUser,
    repository: ConversationRepositoryDependency,
) -> StreamingResponse:
    conversation = repository.get(user_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conversation_json = json.dumps(conversation.to_dict(), ensure_ascii=False)
    return StreamingResponse(
        _export_stream(repository, user_id, conversation_id, conversation_json),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="conversation-{conversation_id}.json"'
        },
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: UUID,
    user_id: CurrentUser,
    request: Request,
    repository: ConversationRepositoryDependency,
) -> None:
    deleted, _turns_removed = repository.hard_delete(user_id, conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # hard_delete already wrote the redacted audit event in the same transaction.


@router.post("/conversations/retention/purge")
def purge_retention(
    payload: RetentionPurgeRequest,
    admin: AdminActor,
    request: Request,
    repository: ConversationRepositoryDependency,
) -> dict[str, int]:
    database = _database(request)
    if database is None:
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    deleted = purge_expired_conversations(database, batch_size=payload.batch_size)
    record_audit(
        database,
        admin.user_id,
        "conversation.retention_purge.requested",
        "conversation_batch",
        f"{deleted}",
        {"deleted": deleted},
    )
    return {"deleted": deleted}
