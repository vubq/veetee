"""Immutable agent configuration snapshots used by runtime generations."""

from .snapshot import AgentRuntimeSnapshot, snapshot_from_agent

__all__ = ["AgentRuntimeSnapshot", "snapshot_from_agent"]
