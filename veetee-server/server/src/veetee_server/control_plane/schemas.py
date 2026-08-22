"""Validated API schemas for agent configuration and local authentication."""

from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


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
    device_id: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=6, max_length=6)
    alias: str = Field(default="", max_length=128)
    agent_id: UUID | None = None


class DeviceProvisionRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    client_id: str | None = Field(default=None, min_length=1, max_length=128)
    ed25519_public_key: str = Field(min_length=64, max_length=64)
    board: str = Field(default="", max_length=64)
    chip: str = Field(default="", max_length=64)
    partition: str = Field(default="", max_length=64)

    @field_validator("ed25519_public_key")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        normalized = value.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise ValueError("ed25519_public_key must be exactly 32 bytes encoded as hex")
        return normalized


class DevicePatchRequest(BaseModel):
    alias: str | None = Field(default=None, max_length=128)
    agent_id: UUID | None = None
    auto_update: bool | None = None
    channel: str | None = Field(default=None, max_length=64)


class DeviceRecoveryRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)


class DeviceResponse(BaseModel):
    id: UUID
    device_id: str
    client_id: str
    alias: str
    status: str
    board: str
    chip: str
    partition: str
    current_firmware_version: str
    auto_update: bool
    channel: str
    cohort: str
    online: bool = False
    owner_user_id: UUID | None = None
    agent_id: UUID | None = None


class DeviceRecoveryResponse(BaseModel):
    device: DeviceResponse
    recovery_token: str


class ReleaseCreateRequest(BaseModel):
    version: str = Field(min_length=1, max_length=64)
    artifact_id: UUID
    board: str = Field(min_length=1, max_length=64)
    chip: str = Field(min_length=1, max_length=64)
    partition: str = Field(min_length=1, max_length=64)
    channel: str = Field(default="stable", max_length=64)
    min_current_version: str = Field(default="", max_length=64)
    provenance: str = Field(min_length=1, max_length=512)
    rollback_target_id: UUID | None = None
    is_published: Literal[False] = False


class RollbackAuthorizeRequest(BaseModel):
    scope: Literal["rollout", "cohort", "device"] = "rollout"
    device_id: str | None = Field(default=None, min_length=1, max_length=128)
    cohort: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_scope_target(self) -> RollbackAuthorizeRequest:
        if self.scope == "device" and self.device_id is None:
            raise ValueError("device_id is required for device rollback scope")
        if self.scope == "cohort" and self.cohort is None:
            raise ValueError("cohort is required for cohort rollback scope")
        if self.scope == "device" and self.cohort is not None:
            raise ValueError("device rollback scope must not include cohort")
        if self.scope == "cohort" and self.device_id is not None:
            raise ValueError("cohort rollback scope must not include device_id")
        if self.scope == "rollout" and (self.device_id is not None or self.cohort is not None):
            raise ValueError("rollout scope must not include device_id or cohort")
        return self


class OtaReportCreateRequest(BaseModel):
    event_id: UUID
    release_id: UUID | None = None
    version: str = Field(default="", max_length=64)
    stage: Literal["check", "download", "install", "boot", "rollback"]
    outcome: Literal["success", "failure", "skipped", "in_progress"]
    error_message: str = Field(default="", max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=32)

    @field_validator("metadata")
    @classmethod
    def validate_metadata_bounds(cls, value: dict[str, Any]) -> dict[str, Any]:
        def validate(node: Any, depth: int) -> None:
            if depth > 6:
                raise ValueError("metadata exceeds maximum depth")
            if isinstance(node, dict):
                if len(node) > 32:
                    raise ValueError("metadata has too many keys")
                for key, child in node.items():
                    if len(key) > 64:
                        raise ValueError("metadata key exceeds maximum length")
                    validate(child, depth + 1)
                return
            if isinstance(node, list):
                if len(node) > 32:
                    raise ValueError("metadata list has too many items")
                for child in node:
                    validate(child, depth + 1)
                return
            if isinstance(node, str) and len(node) > 512:
                raise ValueError("metadata string exceeds maximum length")
            if not isinstance(node, (str, int, float, bool, type(None))):
                raise ValueError("metadata contains unsupported value")

        validate(value, 1)
        return value

    @model_validator(mode="after")
    def require_release_for_progress(self) -> OtaReportCreateRequest:
        if self.stage in {"download", "install", "boot", "rollback"} and self.release_id is None:
            raise ValueError("release_id is required for this OTA stage")
        return self
