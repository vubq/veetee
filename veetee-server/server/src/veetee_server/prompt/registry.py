"""Prompt registry, component enumeration, versioning, and checksum verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PromptComponent(StrEnum):
    """The 8 ordered components of the Veetee base prompt assembly."""

    PLATFORM_POLICY = "platform_policy"
    AGENT_ROLE = "agent_role"
    CONVERSATION_POLICY = "conversation_policy"
    RUNTIME_CONTEXT = "runtime_context"
    MEMORY_CONTEXT = "memory_context"
    TOOL_CONTRACT = "tool_contract"
    DIALOGUE_HISTORY = "dialogue_history"
    USER_TURN = "user_turn"


class PromptRegistryError(Exception):
    """Base exception for prompt registry operations."""


class PromptNotFoundError(PromptRegistryError):
    """Raised when a requested prompt template or version is not found."""


class DuplicatePromptError(PromptRegistryError):
    """Raised when attempting to register a duplicate prompt version without override."""


def compute_prompt_checksum(template: str) -> str:
    """Computes a deterministic SHA-256 checksum hex string for a prompt template."""
    return hashlib.sha256(template.strip().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """Immutable representation of a versioned prompt template."""

    name: str
    component: PromptComponent
    version: str
    template: str
    description: str = ""
    checksum: str = field(init=False)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "checksum", compute_prompt_checksum(self.template)
        )


@dataclass(frozen=True, slots=True)
class PromptVersion:
    """Version metadata for evaluation snapshots."""

    version_str: str
    checksum: str
    created_timestamp_ms: float
    description: str = ""


class PromptRegistry:
    """Registry for managing versioned prompt templates and eval snapshots."""

    def __init__(self) -> None:
        # Key: (name, version) -> PromptTemplate
        self._templates: dict[tuple[str, str], PromptTemplate] = {}

    def register(
        self, template: PromptTemplate, override: bool = False
    ) -> PromptTemplate:
        """Registers a prompt template.

        Raises DuplicatePromptError if name+version exists and override is False.
        """
        key = (template.name, template.version)
        if key in self._templates and not override:
            raise DuplicatePromptError(
                f"Prompt '{template.name}' version '{template.version}' is already registered."
            )
        self._templates[key] = template
        return template

    def get(self, name: str, version: str = "v1.0.0") -> PromptTemplate:
        """Gets a template by name and version.

        If version is 'latest', returns the highest lexicographical version.
        Raises PromptNotFoundError if not found.
        """
        if version == "latest":
            matching = [t for (n, v), t in self._templates.items() if n == name]
            if not matching:
                raise PromptNotFoundError(
                    f"No prompt template registered for name '{name}'"
                )
            return sorted(matching, key=lambda t: t.version)[-1]

        key = (name, version)
        if key not in self._templates:
            raise PromptNotFoundError(
                f"Prompt template '{name}' version '{version}' not found."
            )
        return self._templates[key]

    def list_versions(self, name: str) -> list[str]:
        """Lists all registered versions for a given prompt name."""
        return sorted(v for (n, v) in self._templates.keys() if n == name)

    def snapshot(self) -> dict[str, Any]:
        """Exports a full state snapshot for evaluation and regression testing."""
        return {
            f"{name}:{version}": {
                "name": t.name,
                "component": t.component.value,
                "version": t.version,
                "checksum": t.checksum,
                "description": t.description,
                "template": t.template,
                "metadata": t.metadata,
            }
            for (name, version), t in sorted(self._templates.items())
        }
