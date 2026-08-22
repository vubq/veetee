"""PostgreSQL persistence boundary for the Veetee control plane."""

from .conversation import (
    ConversationRepository,
    StoredConversation,
    StoredTurn,
    TurnInput,
    purge_expired_conversations,
)
from .database import DatabaseConfig, PostgresDatabase
from .lifecycle import (
    AgentLifecycleRepository,
    StoredAgentSnapshot,
    StoredAgentTemplate,
    StoredTag,
)
from .repository import (
    ActivationRepository,
    Actor,
    AgentRepository,
    DeviceRepository,
    FirmwareReleaseRepository,
    ProviderRepository,
    StoredActivation,
    StoredAgent,
    StoredDevice,
    StoredFirmwareRelease,
    UserRepository,
    hash_login_identifier,
    parse_semver,
    record_audit,
)

__all__ = [
    "ActivationRepository",
    "Actor",
    "AgentLifecycleRepository",
    "AgentRepository",
    "ConversationRepository",
    "DatabaseConfig",
    "DeviceRepository",
    "FirmwareReleaseRepository",
    "PostgresDatabase",
    "ProviderRepository",
    "StoredActivation",
    "StoredAgent",
    "StoredAgentSnapshot",
    "StoredAgentTemplate",
    "StoredConversation",
    "StoredDevice",
    "StoredFirmwareRelease",
    "StoredProvider",
    "StoredTag",
    "StoredTurn",
    "TurnInput",
    "UserRepository",
    "hash_login_identifier",
    "parse_semver",
    "purge_expired_conversations",
    "record_audit",
]
