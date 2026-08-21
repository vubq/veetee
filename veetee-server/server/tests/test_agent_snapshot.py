"""Tests for immutable generation-scoped agent configuration."""

from uuid import uuid4

import pytest

from veetee_server.agents import snapshot_from_agent
from veetee_server.persistence.repository import StoredAgent


def test_agent_snapshot_is_immutable() -> None:
    agent = StoredAgent(
        id=uuid4(),
        owner_user_id=uuid4(),
        name="Story",
        version=3,
        profile={
            "role_prompt": "Kể chuyện",
            "personality": "Điềm tĩnh",
            "address_style": "Bạn",
            "language": "vi-VN",
            "detail_level": "adaptive",
            "response_style": "natural",
            "model_id": "model-a",
            "voice_id": "voice-a",
            "intent_strategy": "function_call",
            "memory_enabled": True,
            "memory_min_confidence": 0.8,
            "tool_policy": {},
            "memory_policy": {},
        },
    )
    snapshot = snapshot_from_agent(agent)
    assert snapshot.version == 3
    with pytest.raises(AttributeError):
        snapshot.version = 4  # type: ignore[misc]
