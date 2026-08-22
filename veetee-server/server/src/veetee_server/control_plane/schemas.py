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
