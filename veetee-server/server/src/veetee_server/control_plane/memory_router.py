"""Tenant-scoped memory management API with explicit deletion operations."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from veetee_server.persistence import record_audit
from veetee_server.persistence.database import PostgresDatabase

from .router import AgentRepositoryDependency, CurrentUser

router = APIRouter(prefix="/api/v1/control", tags=["control-plane-memory"])


class MemoryCreate(BaseModel):
    agent_id: UUID | None = None
    kind: str = Field(pattern="^(working|episodic|profile)$")
    content: str = Field(min_length=1, max_length=12000)
    provenance: str = Field(min_length=1, max_length=500)
    confidence: float = Field(default=0.8, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryResponse(MemoryCreate):
    id: UUID


def _db(repository: AgentRepositoryDependency) -> PostgresDatabase:
    return repository.database


@router.get("/memories", response_model=list[MemoryResponse])
def list_memories(
    user_id: CurrentUser,
    repository: AgentRepositoryDependency,
    agent_id: UUID | None = None,
) -> list[dict[str, Any]]:
    query = (
        "SELECT id, agent_id, kind, content, provenance, confidence, metadata "
        "FROM veetee_memories WHERE owner_user_id = %s"
    )
    params: list[Any] = [user_id]
    if agent_id is not None:
        query += " AND agent_id = %s"
        params.append(agent_id)
    query += " ORDER BY updated_at DESC"
    with _db(repository).connection() as connection:
        rows = connection.execute(query, params).fetchall()
    return [
        {
            "id": row[0],
            "agent_id": row[1],
            "kind": row[2],
            "content": row[3],
            "provenance": row[4],
            "confidence": row[5],
            "metadata": row[6],
        }
        for row in rows
    ]


@router.post("/memories", response_model=MemoryResponse, status_code=201)
def create_memory(
    payload: MemoryCreate,
    user_id: CurrentUser,
    repository: AgentRepositoryDependency,
) -> dict[str, Any]:
    memory_id = uuid4()
    with _db(repository).connection() as connection:
        if payload.agent_id is not None and repository.get(user_id, payload.agent_id) is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        from psycopg.types.json import Jsonb

        connection.execute(
            "INSERT INTO veetee_memories "
            "(id, owner_user_id, agent_id, kind, content, provenance, confidence, metadata) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (memory_id, user_id, payload.agent_id, payload.kind, payload.content,
             payload.provenance, payload.confidence, Jsonb(payload.metadata)),
        )
    record_audit(_db(repository), user_id, "memory.create", "memory", str(memory_id))
    return {"id": memory_id, **payload.model_dump()}


@router.delete("/memories/{memory_id}", status_code=204)
def forget_memory(
    memory_id: UUID,
    user_id: CurrentUser,
    repository: AgentRepositoryDependency,
) -> None:
    with _db(repository).connection() as connection:
        result = connection.execute(
            "DELETE FROM veetee_memories WHERE id = %s AND owner_user_id = %s",
            (memory_id, user_id),
        )
    if result.rowcount != 1:
        raise HTTPException(status_code=404, detail="Memory not found")
    record_audit(_db(repository), user_id, "memory.forget", "memory", str(memory_id))


@router.delete("/memories", status_code=204)
def delete_all_memories(
    user_id: CurrentUser,
    repository: AgentRepositoryDependency,
    agent_id: UUID | None = None,
) -> None:
    query = "DELETE FROM veetee_memories WHERE owner_user_id = %s"
    params: list[Any] = [user_id]
    if agent_id is not None:
        query += " AND agent_id = %s"
        params.append(agent_id)
    with _db(repository).connection() as connection:
        connection.execute(query, params)
    record_audit(
        _db(repository), user_id, "memory.delete_all", "memory", str(agent_id or "tenant")
    )
