"""Tenant-scoped agent API with local bearer session authentication."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from veetee_server.persistence import AgentRepository, UserRepository, record_audit

from .schemas import AgentCreate, AgentResponse, AgentUpdate, LoginRequest, LoginResponse

router = APIRouter(prefix="/api/v1/control", tags=["control-plane"])


def _repository(request: Request) -> AgentRepository:
    repository = getattr(request.app.state, "agent_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    if not isinstance(repository, AgentRepository):
        raise HTTPException(status_code=503, detail="Persistence repository is invalid")
    return repository


def current_user(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> UUID:
    user_repository = getattr(request.app.state, "user_repository", None)
    if user_repository is None:
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    if not isinstance(user_repository, UserRepository):
        raise HTTPException(status_code=503, detail="Persistence repository is invalid")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    user_id = user_repository.resolve_token(authorization[7:].strip())
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )
    return user_id


CurrentUser = Annotated[UUID, Depends(current_user)]
AgentRepositoryDependency = Annotated[AgentRepository, Depends(_repository)]


@router.post("/auth/login", response_model=LoginResponse)
def login(request: Request, payload: LoginRequest) -> LoginResponse:
    repository = getattr(request.app.state, "user_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    result = repository.authenticate(payload.email, payload.password)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return LoginResponse(access_token=result[1])


@router.get("/agents", response_model=list[AgentResponse])
def list_agents(
    user_id: CurrentUser, repository: AgentRepositoryDependency
) -> list[dict[str, Any]]:
    return [agent.to_dict() for agent in repository.list(user_id)]


@router.post("/agents", response_model=AgentResponse, status_code=201)
def create_agent(
    payload: AgentCreate,
    user_id: CurrentUser,
    repository: AgentRepositoryDependency,
) -> dict[str, Any]:
    agent = repository.create(user_id, payload.model_dump())
    record_audit(repository.database, user_id, "agent.create", "agent", str(agent.id))
    return agent.to_dict()


@router.get("/agents/{agent_id}", response_model=AgentResponse)
def get_agent(
    agent_id: UUID,
    user_id: CurrentUser,
    repository: AgentRepositoryDependency,
) -> dict[str, Any]:
    agent = repository.get(user_id, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.to_dict()


@router.put("/agents/{agent_id}", response_model=AgentResponse)
def update_agent(
    agent_id: UUID,
    payload: AgentUpdate,
    user_id: CurrentUser,
    repository: AgentRepositoryDependency,
) -> dict[str, Any]:
    data = payload.model_dump(exclude={"expected_version"})
    agent = repository.update(user_id, agent_id, payload.expected_version, data)
    if agent is None:
        raise HTTPException(status_code=409, detail="Agent changed or does not exist")
    record_audit(repository.database, user_id, "agent.update", "agent", str(agent.id))
    return agent.to_dict()


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(
    agent_id: UUID,
    user_id: CurrentUser,
    repository: AgentRepositoryDependency,
) -> None:
    if not repository.delete(user_id, agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    record_audit(repository.database, user_id, "agent.delete", "agent", str(agent_id))
