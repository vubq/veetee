"""Validated API schemas for agent configuration and local authentication."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class AgentConfig(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role_prompt: str = Field(default="", max_length=12000)
    personality: str = Field(default="", max_length=4000)
    address_style: str = Field(default="", max_length=2000)
    language: str = Field(default="vi-VN", min_length=2, max_length=32)
    detail_level: str = Field(default="adaptive", min_length=1, max_length=64)
    response_style: str = Field(default="", max_length=2000)
    model_id: str = Field(default="", max_length=160)
    voice_id: str = Field(default="", max_length=160)
    intent_strategy: Literal["direct_chat", "function_call", "intent_model"] = "function_call"
    memory_enabled: bool = True
    memory_min_confidence: float = Field(default=0.8, ge=0, le=1)
    tool_policy: dict[str, Any] = Field(default_factory=dict)
    memory_policy: dict[str, Any] = Field(default_factory=dict)


class AgentCreate(AgentConfig):
    pass


class AgentUpdate(AgentConfig):
    expected_version: int = Field(gt=0)


class AgentResponse(AgentConfig):
    id: UUID
    version: int


class AgentSummary(AgentResponse):
    device_count: int = 0
    online: bool = False
    last_conversation: str | None = None


class DeviceBindRequest(BaseModel):
    agent_id: UUID
    code: str = Field(pattern=r"^[0-9]{6}$")


class FirmwareReleaseCreate(BaseModel):
    artifact_id: UUID
    version: str = Field(min_length=1, max_length=64, pattern=r"^[0-9]+(?:\.[0-9]+)*$")
    board: str = Field(min_length=1, max_length=128)
    chip: str = Field(min_length=1, max_length=64)
    partition: str = Field(min_length=1, max_length=64)
    force: bool = False


class ProviderStateUpdate(BaseModel):
    enabled: bool | None = None
    is_default: bool | None = None
    expected_version: int = Field(gt=0)


class AgentTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    # Agent-profile config WITHOUT the per-agent name; stored as JSON and
    # validated against the current agent schema when instantiated.
    config: dict[str, Any] = Field(default_factory=dict)


class AgentFromTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class SnapshotCreate(BaseModel):
    reason: Literal["manual"] = "manual"


class SnapshotRestore(BaseModel):
    expected_agent_version: int = Field(gt=0)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=8000)
    transcript_consent: bool | None = None
    consent_version: str | None = Field(default=None, min_length=1, max_length=64)


class RetentionPurgeRequest(BaseModel):
    batch_size: int = Field(default=500, ge=1, le=10000)


class ProviderHealthStatus(BaseModel):
    status: Literal["ok", "degraded", "down", "unknown"]
    details: str = ""


class ProviderResponse(BaseModel):
    kind: str
    provider_id: str
    models: list[str]
    enabled: bool
    default: bool
    is_default: bool
    health: ProviderHealthStatus
    config_version: int
    secret_configurable: bool = False


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)


class DatasetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    status: Literal["active", "archived"] | None = None
    expected_version: int | None = Field(default=None, gt=0)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    dataset_ids: list[UUID] = Field(min_length=1, max_length=50)
    limit: int = Field(default=5, ge=1)
    max_chars: int = Field(default=4000, ge=1)


class AgentKnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1)
    max_chars: int = Field(default=4000, ge=1)


class CorrectionSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    agent_id: UUID | None = None
    enabled: bool = True


class CorrectionSetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    expected_version: int = Field(gt=0)


class CorrectionRuleCreate(BaseModel):
    ordinal: int = Field(ge=0, le=10000)
    rule_type: Literal["exact", "phrase"]
    pattern: str = Field(min_length=1, max_length=500)
    replacement: str = Field(max_length=500)
    case_sensitive: bool = False
    enabled: bool = True
    expected_set_version: int = Field(gt=0)


class CorrectionPreviewRequest(BaseModel):
    text: str = Field(max_length=4096)


class ContextProviderConfigUpdate(BaseModel):
    enabled: bool = True
    ordinal: int = Field(default=0, ge=0, le=10000)
    timeout_ms: int = Field(default=2000, ge=1, le=30000)
    cache_ttl_seconds: int = Field(default=0, ge=0, le=3600)
    config: dict[str, Any] = Field(default_factory=dict)


class ExternalEndpointCreate(BaseModel):
    """Tenant-scoped outbound MCP endpoint registration (M6.6)."""

    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=8, max_length=2048)
    auth_header_env: str | None = Field(default=None, max_length=128)
    enabled: bool = True


class ExternalEndpointUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    url: str | None = Field(default=None, min_length=8, max_length=2048)
    auth_header_env: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    expected_version: int | None = Field(default=None, gt=0)


class IntegrationPermissionUpdate(BaseModel):
    """Per-agent integration grant; absent fields fall back to server defaults."""

    can_list: bool = False
    can_call: bool = False
    rate_limit_calls: int | None = Field(default=None, ge=1, le=100000)
    rate_limit_window_seconds: int | None = Field(default=None, ge=1, le=86400)


class IntegrationToolsListRequest(BaseModel):
    agent_id: UUID


class IntegrationToolCallRequest(BaseModel):
    agent_id: UUID
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class DeviceMcpToolsListRequest(BaseModel):
    """Owner/device/session-scoped device tool discovery (no confirmation)."""

    session_id: UUID | None = None


class DeviceMcpPrepareCallRequest(BaseModel):
    """Prepares a one-time confirmation token binding exact call inputs (M6.7)."""

    session_id: UUID | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class DeviceMcpConfirmCallRequest(BaseModel):
    """Executes the prepared call; arguments come only from the stored token."""

    confirmation_token: str = Field(min_length=16, max_length=256)
