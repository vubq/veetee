from __future__ import annotations

import asyncio
import inspect
import json
import time
from typing import Callable, Dict, Optional, Tuple

from core.tools.registry import ToolRegistry, ToolValidationError, validate_arguments
from core.tools.results import ToolResult, ToolStatus


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        max_calls_per_turn: int = 3,
        max_parallel_read_only: int = 2,
        receipt_ttl_seconds: float = 900.0,
        receipt_cap: int = 512,
        retention_clock: Callable[[], float] = time.monotonic,
    ):
        self.registry = registry
        self.max_calls_per_turn = max(1, int(max_calls_per_turn))
        self._receipts: Dict[Tuple[str, str], ToolResult] = {}
        self._inflight: Dict[Tuple[str, str], asyncio.Task] = {}
        self._inflight_names: Dict[Tuple[str, str], str] = {}
        self._call_fingerprints: Dict[Tuple[str, str], str] = {}
        self._receipt_times: Dict[Tuple[str, str], float] = {}
        self._group_locks: Dict[str, asyncio.Lock] = {}
        self._read_slots = asyncio.Semaphore(max(1, int(max_parallel_read_only)))
        self._lock = asyncio.Lock()
        self._receipt_ttl_seconds = max(1.0, float(receipt_ttl_seconds))
        self._receipt_cap = max(1, int(receipt_cap))
        self._retention_clock = retention_clock

    def _evict_receipt_locked(self, scope_key: Tuple[str, str]) -> None:
        self._receipts.pop(scope_key, None)
        self._call_fingerprints.pop(scope_key, None)
        self._receipt_times.pop(scope_key, None)

    def _prune_retention_locked(self, now: Optional[float] = None) -> None:
        current = self._retention_clock() if now is None else float(now)
        protected = set(self._inflight)

        # TTL defines the dedupe horizon for every settled call. UNKNOWN is
        # protected from cap pressure inside that horizon so a recent uncertain
        # side effect cannot be silently dispatched again.
        for scope_key, stored_at in list(self._receipt_times.items()):
            if scope_key in protected:
                continue
            if current - stored_at >= self._receipt_ttl_seconds:
                self._evict_receipt_locked(scope_key)

        overflow = len(self._receipts) - self._receipt_cap
        if overflow <= 0:
            return
        candidates = sorted(
            (
                (stored_at, scope_key)
                for scope_key, stored_at in self._receipt_times.items()
                if scope_key not in protected
                and self._receipts.get(scope_key) is not None
                and self._receipts[scope_key].status != ToolStatus.UNKNOWN
            ),
            key=lambda item: item[0],
        )
        for _, scope_key in candidates[:overflow]:
            self._evict_receipt_locked(scope_key)

    async def _store_receipt(self, scope_key: Tuple[str, str], result: ToolResult) -> None:
        async with self._lock:
            self._receipts[scope_key] = result
            self._receipt_times[scope_key] = self._retention_clock()
            self._prune_retention_locked()

    async def execute(
        self,
        call_id: str,
        name: str,
        arguments: dict,
        *,
        turn_id: Optional[str] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        scope_key = (str(turn_id or "legacy"), str(call_id))
        fingerprint = json.dumps(
            {"name": name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        async with self._lock:
            self._prune_retention_locked()
            previous_fingerprint = self._call_fingerprints.get(scope_key)
            if previous_fingerprint is not None and previous_fingerprint != fingerprint:
                return ToolResult(
                    call_id,
                    name,
                    ToolStatus.FAILED,
                    error="tool call id was reused with different arguments",
                )
            self._call_fingerprints.setdefault(scope_key, fingerprint)
            cached = self._receipts.get(scope_key)
            if cached is not None:
                return cached
            task = self._inflight.get(scope_key)
            if task is None:
                task = asyncio.create_task(
                    self._execute_once(
                        scope_key,
                        call_id,
                        name,
                        arguments,
                        cancel_event=cancel_event,
                    )
                )
                self._inflight[scope_key] = task
                self._inflight_names[scope_key] = name
                task.add_done_callback(
                    lambda done, key=scope_key: asyncio.create_task(
                        self._cleanup_inflight(key, done)
                    )
                )

        try:
            # Keep a dispatched tool alive if the WebSocket turn is cancelled.
            # This preserves a truthful receipt for side effects that may have
            # completed even after the caller stopped waiting.
            return await asyncio.shield(task)
        finally:
            if task.done():
                await self._cleanup_inflight(scope_key, task)

    async def _cleanup_inflight(self, scope_key: Tuple[str, str], task: asyncio.Task) -> None:
        async with self._lock:
            if self._inflight.get(scope_key) is task:
                self._inflight.pop(scope_key, None)
                self._inflight_names.pop(scope_key, None)
            if scope_key not in self._receipts and scope_key not in self._inflight:
                self._call_fingerprints.pop(scope_key, None)
            self._prune_retention_locked()

    @staticmethod
    async def _acquire_cancellable(resource, cancel_event: Optional[asyncio.Event]) -> None:
        if cancel_event is None:
            await resource.acquire()
            return
        if cancel_event.is_set():
            raise asyncio.CancelledError

        acquire_task = asyncio.create_task(resource.acquire())
        cancel_task = asyncio.create_task(cancel_event.wait())
        try:
            done, _ = await asyncio.wait(
                (acquire_task, cancel_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done and cancel_event.is_set():
                if acquire_task.done() and not acquire_task.cancelled():
                    try:
                        if acquire_task.result():
                            resource.release()
                    except Exception:
                        pass
                else:
                    acquire_task.cancel()
                    try:
                        await acquire_task
                    except asyncio.CancelledError:
                        pass
                raise asyncio.CancelledError

            cancel_task.cancel()
            try:
                await cancel_task
            except asyncio.CancelledError:
                pass
            acquire_task.result()
            if cancel_event.is_set():
                resource.release()
                raise asyncio.CancelledError
        finally:
            for pending in (acquire_task, cancel_task):
                if not pending.done():
                    pending.cancel()

    async def _execute_once(
        self,
        scope_key: Tuple[str, str],
        call_id: str,
        name: str,
        arguments: dict,
        *,
        cancel_event: Optional[asyncio.Event],
    ) -> ToolResult:
        descriptor = self.registry.get(name)
        if descriptor is None:
            result = ToolResult(call_id, name, ToolStatus.FAILED, error="unknown tool")
            await self._store_receipt(scope_key, result)
            return result
        try:
            validate_arguments(descriptor.input_schema, arguments)
        except ToolValidationError as exc:
            result = ToolResult(call_id, name, ToolStatus.FAILED, error=str(exc))
            await self._store_receipt(scope_key, result)
            return result

        dispatched = False
        started = time.perf_counter()
        try:
            group_name = descriptor.concurrency_group or descriptor.name
            group_lock = self._group_locks.setdefault(group_name, asyncio.Lock())

            async def invoke_handler():
                nonlocal dispatched
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError
                dispatched = True
                value = descriptor.handler(arguments)
                if inspect.isawaitable(value):
                    value = await value
                return value

            async def scheduled_call():
                if descriptor.read_only:
                    read_acquired = False
                    group_acquired = False
                    try:
                        await self._acquire_cancellable(self._read_slots, cancel_event)
                        read_acquired = True
                        await self._acquire_cancellable(group_lock, cancel_event)
                        group_acquired = True
                        return await invoke_handler()
                    finally:
                        if group_acquired:
                            group_lock.release()
                        if read_acquired:
                            self._read_slots.release()
                group_acquired = False
                try:
                    await self._acquire_cancellable(group_lock, cancel_event)
                    group_acquired = True
                    return await invoke_handler()
                finally:
                    if group_acquired:
                        group_lock.release()

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
                if dispatched and not descriptor.read_only
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
        await self._store_receipt(scope_key, result)
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
            "receipt_cap": self._receipt_cap,
            "receipt_ttl_seconds": self._receipt_ttl_seconds,
            "retention_over_cap": max(0, len(self._receipts) - self._receipt_cap),
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
