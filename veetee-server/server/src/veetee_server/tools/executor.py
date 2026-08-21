"""Tool executor with policy, timeout, cancellation, and audit boundaries."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from veetee_server.tools.registry import ToolDefinition, ToolPolicy

logger = logging.getLogger("veetee.tools")


class ToolExecutionError(Exception):
    """Base exception for tool execution errors."""


class PolicyViolationError(ToolExecutionError):
    """Raised when tool call violates RBAC / device policy."""


class ConfirmationRequiredError(ToolExecutionError):
    """Raised when sensitive tool action requires user confirmation."""


class ToolTimeoutError(ToolExecutionError):
    """Raised when tool execution exceeds timeout limit."""


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Execution context binding user, device, session, generation, and confirmation state."""

    user_id: str = "anonymous"
    agent_id: str = "default_agent"
    device_id: str = "local_device"
    session_id: str = ""
    generation_id: str = ""
    confirmation_granted: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Audit record for tool calls."""

    tool_name: str
    user_id: str
    device_id: str
    session_id: str
    generation_id: str
    status: str  # "success", "error", "cancelled", "policy_denied", "confirmation_required"
    duration_ms: float
    error: str | None = None
    truncated: bool = False
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000)


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Standardized tool execution result."""

    tool_name: str
    status: str
    output: str
    raw_result: Any = None
    duration_ms: float = 0.0
    truncated: bool = False
    audit: AuditRecord | None = None


class ToolExecutor:
    """Executes tool handlers under strict policy, timeout, truncation, and audit bounds."""

    def __init__(
        self,
        default_timeout_seconds: float = 5.0,
        max_output_chars: int = 2048,
    ) -> None:
        self.default_timeout_seconds = default_timeout_seconds
        self.max_output_chars = max_output_chars
        self._audit_log: list[AuditRecord] = []

    def get_audit_log(self) -> list[AuditRecord]:
        """Returns recorded audit records."""
        return list(self._audit_log)

    async def execute(
        self,
        tool: ToolDefinition,
        arguments: dict[str, Any],
        context: ToolContext,
        policy: ToolPolicy | None = None,
        timeout_seconds: float | None = None,
    ) -> ToolResult:
        """Executes a tool with policy, timeout, cancellation, and audit checks."""
        start_time = time.perf_counter()
        timeout = timeout_seconds if timeout_seconds is not None else self.default_timeout_seconds

        # 1. Policy check
        if policy is not None and not policy.is_allowed(tool.name):
            duration_ms = (time.perf_counter() - start_time) * 1000
            audit = AuditRecord(
                tool_name=tool.name,
                user_id=context.user_id,
                device_id=context.device_id,
                session_id=context.session_id,
                generation_id=context.generation_id,
                status="policy_denied",
                duration_ms=duration_ms,
                error="Tool call denied by policy allowlist/denylist",
            )
            self._audit_log.append(audit)
            raise PolicyViolationError(
                f"Tool '{tool.name}' is prohibited by policy."
            )

        # 2. Confirmation check
        requires_conf = tool.requires_confirmation or (
            policy is not None and policy.requires_confirmation(tool.name, tool)
        )
        if requires_conf and not context.confirmation_granted:
            duration_ms = (time.perf_counter() - start_time) * 1000
            audit = AuditRecord(
                tool_name=tool.name,
                user_id=context.user_id,
                device_id=context.device_id,
                session_id=context.session_id,
                generation_id=context.generation_id,
                status="confirmation_required",
                duration_ms=duration_ms,
                error="User confirmation required before executing physical/sensitive action",
            )
            self._audit_log.append(audit)
            raise ConfirmationRequiredError(
                f"Tool '{tool.name}' requires user confirmation before execution."
            )

        if tool.handler is None:
            raise ToolExecutionError(f"Tool '{tool.name}' has no handler defined.")

        # 3. Execution under timeout & cancellation
        try:
            raw_res = await asyncio.wait_for(
                tool.handler(arguments, context), timeout=timeout
            )
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Convert result to string and enforce max output limit
            if isinstance(raw_res, str):
                output_str = raw_res
            else:
                output_str = json.dumps(raw_res, ensure_ascii=False)

            truncated = False
            if len(output_str) > self.max_output_chars:
                output_str = (
                    output_str[: self.max_output_chars]
                    + f"\n... [Output truncated to {self.max_output_chars} chars]"
                )
                truncated = True

            audit = AuditRecord(
                tool_name=tool.name,
                user_id=context.user_id,
                device_id=context.device_id,
                session_id=context.session_id,
                generation_id=context.generation_id,
                status="success",
                duration_ms=duration_ms,
                truncated=truncated,
            )
            self._audit_log.append(audit)

            return ToolResult(
                tool_name=tool.name,
                status="success",
                output=output_str,
                raw_result=raw_res,
                duration_ms=duration_ms,
                truncated=truncated,
                audit=audit,
            )

        except TimeoutError as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            audit = AuditRecord(
                tool_name=tool.name,
                user_id=context.user_id,
                device_id=context.device_id,
                session_id=context.session_id,
                generation_id=context.generation_id,
                status="timeout",
                duration_ms=duration_ms,
                error=f"Execution timed out after {timeout}s",
            )
            self._audit_log.append(audit)
            raise ToolTimeoutError(
                f"Tool '{tool.name}' timed out after {timeout} seconds."
            ) from exc

        except asyncio.CancelledError:
            duration_ms = (time.perf_counter() - start_time) * 1000
            audit = AuditRecord(
                tool_name=tool.name,
                user_id=context.user_id,
                device_id=context.device_id,
                session_id=context.session_id,
                generation_id=context.generation_id,
                status="cancelled",
                duration_ms=duration_ms,
                error="Execution cancelled by turn abort",
            )
            self._audit_log.append(audit)
            raise

        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            audit = AuditRecord(
                tool_name=tool.name,
                user_id=context.user_id,
                device_id=context.device_id,
                session_id=context.session_id,
                generation_id=context.generation_id,
                status="error",
                duration_ms=duration_ms,
                error=str(exc),
            )
            self._audit_log.append(audit)
            raise ToolExecutionError(
                f"Error executing tool '{tool.name}': {exc}"
            ) from exc
