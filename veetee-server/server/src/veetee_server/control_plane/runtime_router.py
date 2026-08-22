"""Tenant-scoped device and conversation metadata endpoints."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter

from .router import AgentRepositoryDependency, CurrentUser

router = APIRouter(prefix="/api/v1/control", tags=["control-plane-runtime"])


@router.get("/conversations")
def list_conversations(
    user_id: CurrentUser,
    repository: AgentRepositoryDependency,
    agent_id: UUID | None = None,
) -> list[dict[str, Any]]:
    query = (
        "SELECT id, agent_id, device_id, title, summary, locale, turn_count, "
        "started_at, ended_at FROM veetee_conversations WHERE owner_user_id = %s"
    )
    params: list[Any] = [user_id]
    if agent_id is not None:
        query += " AND agent_id = %s"
        params.append(agent_id)
    query += " ORDER BY started_at DESC"
    with repository.database.connection() as connection:
        rows = connection.execute(query, params).fetchall()
    return [
        {
            "id": row[0],
            "agent_id": row[1],
            "device_id": row[2],
            "title": row[3],
            "summary": row[4],
            "locale": row[5],
            "turn_count": row[6],
            "started_at": row[7],
            "ended_at": row[8],
        }
        for row in rows
    ]
