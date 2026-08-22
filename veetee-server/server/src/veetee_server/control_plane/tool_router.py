"""Tenant-scoped external integration endpoints, permissions and MCP test calls.

M6.6 decision (locked): the control plane can register outbound HTTPS MCP
endpoints per tenant, grant per-agent ``tools/list``/``tools/call`` permissions
(default deny), and exercise them through the guarded
:class:`ExternalMCPClient`. Every call passes the default-deny gate plus the
bounded sliding-window rate limiter; audit metadata records only identifiers
and outcome — never tool arguments or secret material.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from veetee_server.persistence import ToolingRepository
from veetee_server.tools.external_mcp import (
    ExternalMCPError,
    ExternalMCPRemoteError,
    ExternalMCPTimeoutError,
    ExternalMCPTransportError,
)
from veetee_server.tools.integrations import (
    IntegrationGate,
    IntegrationPermissionError,
    IntegrationPermissionSnapshot,
    IntegrationRateLimitError,
)
from veetee_server.tools.ssrf import ExternalUrlPolicyError

from .router import CurrentUser
from .schemas import (
    ExternalEndpointCreate,
    ExternalEndpointUpdate,
    IntegrationPermissionUpdate,
    IntegrationToolCallRequest,
    IntegrationToolsListRequest,
)

router = APIRouter(prefix="/api/v1/control", tags=["control-plane-tools"])


def _tooling_repository(request: Request) -> ToolingRepository:
    repository = getattr(request.app.state, "tooling_repository", None)
    if not isinstance(repository, ToolingRepository):
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return repository


def _integration_gate(request: Request) -> IntegrationGate:
    gate = getattr(request.app.state, "integration_gate", None)
    if not isinstance(gate, IntegrationGate):
        raise HTTPException(status_code=503, detail="Integrations are not available")
    return gate


def _external_client(request: Request) -> Any:
    client = getattr(request.app.state, "external_mcp_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="External calls are not configured")
    return client


ToolingDependency = Annotated[ToolingRepository, Depends(_tooling_repository)]
GateDependency = Annotated[IntegrationGate, Depends(_integration_gate)]


@router.get("/server-mcp/catalog")
def server_mcp_catalog(request: Request, user_id: CurrentUser) -> dict[str, Any]:
    del user_id
    registry = getattr(request.app.state, "tool_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Tool registry is unavailable")
    return {"tools": registry.to_openai_schemas()}


def _endpoint_dict(item: Any) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "name": item.name,
        "url": item.url,
        "auth_configured": item.auth_header_env is not None,
        "enabled": item.enabled,
        "version": item.version,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _permission_dict(item: Any) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "agent_id": str(item.agent_id),
        "endpoint_id": str(item.endpoint_id),
        "can_list": item.can_list,
        "can_call": item.can_call,
        "rate_limit_calls": item.rate_limit_calls,
        "rate_limit_window_seconds": item.rate_limit_window_seconds,
    }


def _map_repository_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ExternalUrlPolicyError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="Resource not found")
    if isinstance(exc, ValueError) and (
        "already exists" in str(exc) or "Optimistic lock" in str(exc)
    ):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Unexpected repository failure")


def _snapshot_from_stored(stored: Any) -> IntegrationPermissionSnapshot:
    return IntegrationPermissionSnapshot(
        can_list=stored.can_list,
        can_call=stored.can_call,
        rate_limit_calls=stored.rate_limit_calls,
        rate_limit_window_seconds=float(stored.rate_limit_window_seconds),
    )


async def _authorize_endpoint_action(
    request: Request,
    user_id: UUID,
    agent_id: UUID,
    endpoint_id: UUID,
    action: Literal["list", "call"],
) -> tuple[Any, IntegrationPermissionSnapshot]:
    """Loads the tenant-owned endpoint then runs the default-deny gate."""
    repository = _tooling_repository(request)
    endpoint = await asyncio.to_thread(repository.get_endpoint, user_id, endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    gate = _integration_gate(request)
    try:
        snapshot = await asyncio.to_thread(
            gate.authorize,
            str(user_id),
            str(agent_id),
            str(endpoint_id),
            action,
            endpoint_enabled=endpoint.enabled,
        )
    except IntegrationRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail="Integration rate limit exceeded",
            headers={"Retry-After": str(int(max(exc.retry_after_seconds, 1.0)))},
        ) from exc
    except IntegrationPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return endpoint, snapshot


def _record_call_audit(
    repository: ToolingRepository,
    user_id: UUID,
    endpoint_id: UUID,
    action: str,
    status: str,
) -> None:
    # Audit metadata is intentionally minimal: identifiers + outcome only.
    from veetee_server.persistence import record_audit

    record_audit(
        repository.database,
        user_id,
        f"integration.{action}.{status}",
        "external_endpoint",
        str(endpoint_id),
    )


@router.post("/integrations/endpoints", status_code=201)
def create_endpoint(
    payload: ExternalEndpointCreate,
    user_id: CurrentUser,
    request: Request,
    repository: ToolingDependency,
) -> dict[str, Any]:
    try:
        policy = getattr(request.app.state, "external_url_policy", None)
        if policy is None:
            raise ValueError("External URL policy is unavailable")
        policy.validate_url(payload.url)
        stored = repository.create_endpoint(
            user_id,
            payload.name,
            payload.url,
            auth_header_env=payload.auth_header_env,
            enabled=payload.enabled,
        )
    except Exception as exc:
        raise _map_repository_error(exc) from exc
    return _endpoint_dict(stored)


@router.get("/integrations/endpoints")
def list_endpoints(user_id: CurrentUser, repository: ToolingDependency) -> list[dict[str, Any]]:
    return [_endpoint_dict(item) for item in repository.list_endpoints(user_id)]


@router.get("/integrations/endpoints/{endpoint_id}")
def get_endpoint(
    endpoint_id: UUID, user_id: CurrentUser, repository: ToolingDependency
) -> dict[str, Any]:
    endpoint = repository.get_endpoint(user_id, endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    return _endpoint_dict(endpoint)


@router.patch("/integrations/endpoints/{endpoint_id}")
def update_endpoint(
    endpoint_id: UUID,
    payload: ExternalEndpointUpdate,
    user_id: CurrentUser,
    request: Request,
    repository: ToolingDependency,
) -> dict[str, Any]:
    try:
        if payload.url is not None:
            policy = getattr(request.app.state, "external_url_policy", None)
            if policy is None:
                raise ValueError("External URL policy is unavailable")
            policy.validate_url(payload.url)
        stored = repository.update_endpoint(
            user_id,
            endpoint_id,
            name=payload.name,
            url=payload.url,
            auth_header_env=payload.auth_header_env,
            enabled=payload.enabled,
            expected_version=payload.expected_version,
        )
    except Exception as exc:
        raise _map_repository_error(exc) from exc
    return _endpoint_dict(stored)


@router.delete("/integrations/endpoints/{endpoint_id}", status_code=204)
def delete_endpoint(
    endpoint_id: UUID, user_id: CurrentUser, repository: ToolingDependency
) -> None:
    if not repository.delete_endpoint(user_id, endpoint_id):
        raise HTTPException(status_code=404, detail="Endpoint not found")


@router.get("/agents/{agent_id}/integration-permissions")
def list_agent_permissions(
    agent_id: UUID, user_id: CurrentUser, repository: ToolingDependency
) -> list[dict[str, Any]]:
    return [
        _permission_dict(item)
        for item in repository.list_agent_permissions(user_id, agent_id)
    ]


@router.put("/agents/{agent_id}/integration-permissions/{endpoint_id}")
def put_permission(
    agent_id: UUID,
    endpoint_id: UUID,
    payload: IntegrationPermissionUpdate,
    user_id: CurrentUser,
    request: Request,
    repository: ToolingDependency,
) -> dict[str, Any]:
    settings = getattr(request.app.state, "settings", None)
    rate_calls = payload.rate_limit_calls
    if rate_calls is None and settings is not None:
        rate_calls = getattr(settings, "tool_integration_rate_limit_calls", 30)
    rate_window = payload.rate_limit_window_seconds
    if rate_window is None and settings is not None:
        rate_window = int(getattr(settings, "tool_integration_rate_window_seconds", 60))
    try:
        stored = repository.put_permission(
            user_id,
            agent_id,
            endpoint_id,
            can_list=payload.can_list,
            can_call=payload.can_call,
            rate_limit_calls=int(rate_calls or 30),
            rate_limit_window_seconds=int(rate_window or 60),
        )
    except Exception as exc:
        raise _map_repository_error(exc) from exc
    return _permission_dict(stored)


@router.delete(
    "/agents/{agent_id}/integration-permissions/{endpoint_id}", status_code=204
)
def delete_permission(
    agent_id: UUID,
    endpoint_id: UUID,
    user_id: CurrentUser,
    repository: ToolingDependency,
) -> None:
    if not repository.delete_permission(user_id, agent_id, endpoint_id):
        raise HTTPException(
            status_code=404, detail="Agent integration permission not found"
        )


@router.post("/integrations/endpoints/{endpoint_id}/test/list")
async def test_tools_list(
    endpoint_id: UUID,
    payload: IntegrationToolsListRequest,
    user_id: CurrentUser,
    request: Request,
    repository: ToolingDependency,
    gate: GateDependency,
) -> dict[str, Any]:
    del gate  # dependency ensures wiring; authorization happens below
    endpoint, _snapshot = await _authorize_endpoint_action(
        request, user_id, payload.agent_id, endpoint_id, "list"
    )
    client = _external_client(request)
    try:
        result = await client.list_tools(
            endpoint.url, auth_header_env=endpoint.auth_header_env
        )
    except (ExternalUrlPolicyError, ExternalMCPTransportError) as exc:
        raise HTTPException(status_code=502, detail="External call failed") from exc
    except ExternalMCPTimeoutError as exc:
        raise HTTPException(status_code=504, detail="External call timed out") from exc
    except ExternalMCPRemoteError as exc:
        raise HTTPException(status_code=502, detail=f"Remote MCP error {exc.code}") from exc
    except ExternalMCPError as exc:
        raise HTTPException(status_code=502, detail="External response was invalid") from exc

    bounded = [
        {
            "name": str(tool.get("name", "")),
            "description": str(tool.get("description", "")),
        }
        for tool in result[:50]
        if isinstance(tool, dict)
    ]
    _record_call_audit(repository, user_id, endpoint_id, "tools_list", "ok")
    return {"tools": bounded}


@router.post("/integrations/endpoints/{endpoint_id}/test/call")
async def test_tool_call(
    endpoint_id: UUID,
    payload: IntegrationToolCallRequest,
    user_id: CurrentUser,
    request: Request,
    repository: ToolingDependency,
) -> dict[str, Any]:
    endpoint, _snapshot = await _authorize_endpoint_action(
        request, user_id, payload.agent_id, endpoint_id, "call"
    )
    client = _external_client(request)

    quota_service = getattr(request.app.state, "quota_service", None)
    if quota_service is not None:
        try:
            check = await asyncio.to_thread(
                quota_service.check_and_consume, user_id, "tool_calls_minute", 1
            )
            if not check.allowed:
                raise HTTPException(
                    status_code=429,
                    detail="Quota exceeded for tool_calls_minute",
                    headers={"Retry-After": "60"},
                )
        except HTTPException:
            raise
        except Exception as exc:
            if await asyncio.to_thread(quota_service.is_quota_enabled, user_id):
                raise HTTPException(
                    status_code=503, detail="Quota enforcement unavailable"
                ) from exc

    try:
        result = await client.call_tool(
            endpoint.url,
            payload.tool_name,
            payload.arguments,
            auth_header_env=endpoint.auth_header_env,
        )
    except (ExternalUrlPolicyError, ExternalMCPTransportError) as exc:
        raise HTTPException(status_code=502, detail="External call failed") from exc
    except ExternalMCPTimeoutError as exc:
        raise HTTPException(status_code=504, detail="External call timed out") from exc
    except ExternalMCPRemoteError as exc:
        raise HTTPException(status_code=502, detail=f"Remote MCP error {exc.code}") from exc
    except ExternalMCPError as exc:
        raise HTTPException(status_code=502, detail="External response was invalid") from exc

    content = result.get("content") if isinstance(result, dict) else None
    bounded_content = content if isinstance(content, list) else []
    _record_call_audit(repository, user_id, endpoint_id, "tools_call", "ok")
    # Arguments are never echoed into logs/audit; response body stays bounded by
    # the transport max_response_bytes.
    return {"content": bounded_content, "isError": bool(result.get("isError", False))}
