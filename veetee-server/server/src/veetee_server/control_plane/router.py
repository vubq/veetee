"""Tenant-scoped agent API with local bearer session authentication."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from veetee_server.config import Settings, get_settings
from veetee_server.persistence import (
    AgentRepository,
    UserRepository,
    hash_login_identifier,
    record_audit,
)

from .catalog import allowed_agent_model_ids
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
    settings: Settings = getattr(request.app.state, "settings", get_settings())
    result = repository.authenticate(
        payload.email,
        payload.password,
        rate_limit=settings.login_rate_limit,
        rate_window_seconds=settings.login_rate_window_seconds,
    )
    # Audit records carry only the redacted identifier hash, never the email.
    identifier_hash = hash_login_identifier(payload.email)
    if result.outcome == "rate_limited":
        record_audit(
            repository.database, None, "auth.login.rate_limited", "session", identifier_hash
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts",
            headers={"Retry-After": str(result.retry_after_seconds)},
        )
    if result.outcome == "invalid_credentials":
        record_audit(repository.database, None, "auth.login.failure", "session", identifier_hash)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if result.user_id is None or result.access_token is None:  # pragma: no cover
        raise HTTPException(status_code=500, detail="Login outcome is inconsistent")
    record_audit(
        repository.database, result.user_id, "auth.login.success", "session", identifier_hash
    )
    return LoginResponse(access_token=result.access_token)


@router.post("/auth/logout", status_code=204)
def logout(
    user_id: CurrentUser,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    repository = getattr(request.app.state, "user_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if repository.revoke_token(token):
            record_audit(repository.database, user_id, "session.logout", "session", "current")


@router.get("/agents", response_model=list[AgentResponse])
def list_agents(
    user_id: CurrentUser, repository: AgentRepositoryDependency
) -> list[dict[str, Any]]:
    return [agent.to_dict() for agent in repository.list(user_id)]


def _ensure_model_id_in_catalog(model_id: str) -> None:
    """Rejects non-empty model ids outside the shared backend catalog with 422."""
    if model_id and model_id not in allowed_agent_model_ids():
        raise HTTPException(
            status_code=422,
            detail="model_id must match the backend provider catalog",
        )


@router.post("/agents", response_model=AgentResponse, status_code=201)
def create_agent(
    payload: AgentCreate,
    user_id: CurrentUser,
    repository: AgentRepositoryDependency,
) -> dict[str, Any]:
    _ensure_model_id_in_catalog(payload.model_id)
    agent, _ = repository.create(user_id, payload.model_dump())
    if agent is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Agent name already exists")
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
    _ensure_model_id_in_catalog(payload.model_id)
    data = payload.model_dump(exclude={"expected_version"})
    agent, error = repository.update(user_id, agent_id, payload.expected_version, data)
    if agent is None:
        detail = (
            "Agent name already exists"
            if error == "duplicate_name"
            else "Agent changed or does not exist"
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
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
