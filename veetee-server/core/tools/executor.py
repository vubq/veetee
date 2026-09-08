from __future__ import annotations

import asyncio
import inspect
import json
import time
from typing import Dict

from core.tools.registry import ToolRegistry, ToolValidationError, validate_arguments
from core.tools.results import ToolResult, ToolStatus


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        max_calls_per_turn: int = 3,
        max_parallel_read_only: int = 2,
    ):
        self.registry = registry
        self.max_calls_per_turn = max(1, int(max_calls_per_turn))
        self._receipts: Dict[str, ToolResult] = {}
        self._inflight: Dict[str, asyncio.Task] = {}
        self._inflight_names: Dict[str, str] = {}
        self._call_fingerprints: Dict[str, str] = {}
        self._group_locks: Dict[str, asyncio.Lock] = {}
        self._read_slots = asyncio.Semaphore(max(1, int(max_parallel_read_only)))
        self._lock = asyncio.Lock()

    async def execute(self, call_id: str, name: str, arguments: dict) -> ToolResult:
        fingerprint = json.dumps(
            {"name": name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        async with self._lock:
            previous_fingerprint = self._call_fingerprints.get(call_id)
            if previous_fingerprint is not None and previous_fingerprint != fingerprint:
                return ToolResult(
                    call_id,
                    name,
                    ToolStatus.FAILED,
                    error="tool call id was reused with different arguments",
                )
            self._call_fingerprints.setdefault(call_id, fingerprint)
            cached = self._receipts.get(call_id)
            if cached is not None:
                return cached
            task = self._inflight.get(call_id)
            if task is None:
                task = asyncio.create_task(self._execute_once(call_id, name, arguments))
                self._inflight[call_id] = task
                self._inflight_names[call_id] = name

        try:
            # Keep a dispatched tool alive if the WebSocket turn is cancelled.
            # This preserves a truthful receipt for side effects that may have
            # completed even after the caller stopped waiting.
            return await asyncio.shield(task)
        finally:
            if task.done():
                async with self._lock:
                    if self._inflight.get(call_id) is task:
                        self._inflight.pop(call_id, None)
                        self._inflight_names.pop(call_id, None)

    async def _execute_once(self, call_id: str, name: str, arguments: dict) -> ToolResult:
        descriptor = self.registry.get(name)
        if descriptor is None:
            result = ToolResult(call_id, name, ToolStatus.FAILED, error="unknown tool")
            self._receipts[call_id] = result
            return result
        try:
            validate_arguments(descriptor.input_schema, arguments)
        except ToolValidationError as exc:
            result = ToolResult(call_id, name, ToolStatus.FAILED, error=str(exc))
            self._receipts[call_id] = result
            return result

        dispatched = False
        started = time.perf_counter()
        try:
            group_name = descriptor.concurrency_group or descriptor.name
            group_lock = self._group_locks.setdefault(group_name, asyncio.Lock())

            async def invoke_handler():
                nonlocal dispatched
                dispatched = True
                value = descriptor.handler(arguments)
                if inspect.isawaitable(value):
                    value = await value
                return value

            async def scheduled_call():
                if descriptor.read_only:
                    async with self._read_slots:
                        async with group_lock:
                            return await invoke_handler()
                async with group_lock:
                    return await invoke_handler()

            value = await asyncio.wait_for(
                scheduled_call(),
                timeout=max(0.05, descriptor.timeout_ms / 1000.0),
            )
            if isinstance(value, ToolResult):
                result = ToolResult(
                    call_id,
                    name,
                    value.status,
                    data=value.data,
                    error=value.error,
                    metadata=value.metadata,
                )
            else:
                result = ToolResult(call_id, name, ToolStatus.SUCCEEDED, data=value)
        except asyncio.TimeoutError:
            status = (
                ToolStatus.UNKNOWN
                if dispatched and not descriptor.read_only and not descriptor.idempotent
                else ToolStatus.TIMED_OUT
            )
            result = ToolResult(
                call_id,
                name,
                status,
                error="tool timed out",
                metadata={"dispatched": dispatched},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = ToolResult(call_id, name, ToolStatus.FAILED, error=str(exc))
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if "elapsed_ms" not in result.metadata:
            result = ToolResult(
                result.call_id,
                result.name,
                result.status,
                data=result.data,
                error=result.error,
                metadata={**result.metadata, "elapsed_ms": round(elapsed_ms, 3)},
            )
        self._receipts[call_id] = result
        return result

    def snapshot(self) -> dict:
        status_counts: Dict[str, int] = {}
        for result in self._receipts.values():
            status_counts[result.status.value] = status_counts.get(result.status.value, 0) + 1
        active_tools: Dict[str, int] = {}
        for name in self._inflight_names.values():
            active_tools[name] = active_tools.get(name, 0) + 1
        return {
            "active_count": len(self._inflight),
            "active_tools": active_tools,
            "receipt_count": len(self._receipts),
            "receipt_status_counts": status_counts,
        }

    @staticmethod
    def render(descriptor, result: ToolResult) -> str:
        if descriptor and descriptor.renderer:
            return descriptor.renderer(result)
        if result.ok:
            return str(result.data)
        if result.status == ToolStatus.UNKNOWN:
            return "Mình đã gửi yêu cầu nhưng chưa xác minh được trạng thái cuối cùng."
        return f"Mình chưa thực hiện được {result.name}: {result.error or result.status.value}."
