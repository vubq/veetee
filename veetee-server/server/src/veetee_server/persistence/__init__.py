"""PostgreSQL persistence boundary for the Veetee control plane."""

from .database import DatabaseConfig, PostgresDatabase
from .repository import (
    ActivationRepository,
    AgentRepository,
    DeviceRepository,
    FirmwareReleaseRepository,
    StoredActivation,
    StoredAgent,
    StoredDevice,
    StoredFirmwareRelease,
    UserRepository,
    parse_semver,
    record_audit,
)

__all__ = [
    "ActivationRepository",
    "AgentRepository",
    "DatabaseConfig",
    "DeviceRepository",
    "FirmwareReleaseRepository",
    "PostgresDatabase",
    "StoredActivation",
    "StoredAgent",
    "StoredDevice",
    "StoredFirmwareRelease",
    "UserRepository",
    "parse_semver",
    "record_audit",
]
