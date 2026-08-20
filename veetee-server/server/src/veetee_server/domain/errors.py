"""Typed errors raised by domain state transitions."""

from dataclasses import dataclass
from enum import StrEnum


class DomainErrorCode(StrEnum):
    INVALID_TRANSITION = "veetee_invalid_transition"
    STALE_GENERATION = "veetee_stale_generation"
    CLEANUP_TIMEOUT = "veetee_cleanup_timeout"


@dataclass(eq=False)
class DomainError(Exception):
    code: DomainErrorCode
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class InvalidTransitionError(DomainError):
    def __init__(self, entity: str, current: StrEnum, target: StrEnum) -> None:
        super().__init__(
            DomainErrorCode.INVALID_TRANSITION,
            f"{entity} cannot transition from {current} to {target}",
        )


class StaleGenerationError(DomainError):
    def __init__(self, generation_id: str) -> None:
        super().__init__(
            DomainErrorCode.STALE_GENERATION,
            f"generation {generation_id} is stale",
        )


class CleanupTimeoutError(DomainError):
    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(
            DomainErrorCode.CLEANUP_TIMEOUT,
            f"cleanup did not finish within {timeout_seconds} seconds",
        )
