"""Immutable, generation-scoped agent configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from veetee_server.prompt import AgentPromptProfile


@dataclass(frozen=True, slots=True)
class AgentRuntimeSnapshot:
    """A stable config snapshot: changes apply to a later generation only."""

    agent_id: UUID
    version: int
    prompt_profile: AgentPromptProfile
    model_id: str
    voice_id: str
    intent_strategy: str
    memory_enabled: bool
    memory_min_confidence: float
    tool_policy: dict[str, Any]
    memory_policy: dict[str, Any]


def snapshot_from_agent(agent: Any) -> AgentRuntimeSnapshot:
    """Builds a typed snapshot from a repository StoredAgent-like object."""
    profile = agent.profile
    return AgentRuntimeSnapshot(
        agent_id=agent.id,
        version=agent.version,
        prompt_profile=AgentPromptProfile(
            role_prompt=profile["role_prompt"],
            personality=profile["personality"],
            address_style=profile["address_style"],
            language=profile["language"],
            detail_level=profile["detail_level"],
            response_style=profile["response_style"],
        ),
        model_id=profile["model_id"],
        voice_id=profile["voice_id"],
        intent_strategy=profile["intent_strategy"],
        memory_enabled=profile["memory_enabled"],
        memory_min_confidence=profile["memory_min_confidence"],
        tool_policy=dict(profile["tool_policy"]),
        memory_policy=dict(profile["memory_policy"]),
    )
