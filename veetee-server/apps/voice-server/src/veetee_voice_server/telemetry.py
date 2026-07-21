from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

import orjson
import structlog

logger = structlog.get_logger(__name__)

_REDACTED_KEYS = {
    "api_key",
    "arguments",
    "audio",
    "authorization",
    "secret",
    "text",
    "token",
    "transcript",
}
_MAX_PAYLOAD_BYTES = 4_096


class ConversationEventPublisher(Protocol):
    async def publish_conversation_events(
        self, device_id: str, events: list[dict[str, Any]]
    ) -> int: ...


class ConversationTelemetry(Protocol):
    def record(
        self,
        session_id: str,
        event_type: str,
        *,
        generation: int,
        turn_id: str | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None: ...

    async def close(self) -> None: ...


class NullConversationTelemetry:
    def record(
        self,
        session_id: str,
        event_type: str,
        *,
        generation: int,
        turn_id: str | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        return

    async def close(self) -> None:
        return


class ConversationTelemetryBuffer:
    def __init__(
        self,
        publisher: ConversationEventPublisher,
        device_id: str,
        *,
        queue_capacity: int = 256,
        batch_size: int = 32,
        flush_seconds: float = 0.25,
        shutdown_seconds: float = 1.0,
    ) -> None:
        if queue_capacity < 1 or batch_size < 1 or batch_size > 64:
            raise ValueError("invalid conversation telemetry queue bounds")
        self._publisher = publisher
        self._device_id = device_id
        self._queue_capacity = queue_capacity
        # Reserve one slot for the shutdown sentinel so close never waits on a full hot-path queue.
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=queue_capacity + 1
        )
        self._batch_size = batch_size
        self._flush_seconds = flush_seconds
        self._shutdown_seconds = shutdown_seconds
        self._closed = False
        self._dropped = 0
        self._task = asyncio.create_task(
            self._run(), name=f"conversation-telemetry:{device_id}"
        )

    @property
    def dropped_events(self) -> int:
        return self._dropped

    def record(
        self,
        session_id: str,
        event_type: str,
        *,
        generation: int,
        turn_id: str | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        if self._closed:
            return
        if self._queue.qsize() >= self._queue_capacity:
            self._dropped += 1
            return
        event: dict[str, Any] = {
            "eventId": str(uuid4()),
            "sessionId": session_id,
            "generation": max(0, generation),
            "eventType": event_type,
            "payload": _safe_payload(payload or {}),
            "occurredAt": datetime.now(UTC).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
        }
        if turn_id:
            event["turnId"] = turn_id
        self._queue.put_nowait(event)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put_nowait(None)
        try:
            await asyncio.wait_for(self._task, timeout=self._shutdown_seconds)
        except TimeoutError:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        closing = False
        while not closing:
            first = await self._queue.get()
            if first is None:
                break
            batch = [first]
            deadline = asyncio.get_running_loop().time() + self._flush_seconds
            while len(batch) < self._batch_size:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                except TimeoutError:
                    break
                if item is None:
                    closing = True
                    break
                batch.append(item)
            await self._publish(batch, final=closing)

        while not self._queue.empty():
            drain_batch: list[dict[str, Any]] = []
            while len(drain_batch) < self._batch_size and not self._queue.empty():
                item = self._queue.get_nowait()
                if item is not None:
                    drain_batch.append(item)
            if drain_batch:
                await self._publish(drain_batch, final=True)

    async def _publish(self, batch: list[dict[str, Any]], *, final: bool) -> None:
        attempts = 1 if final else 2
        for attempt in range(attempts):
            try:
                await self._publisher.publish_conversation_events(self._device_id, batch)
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if attempt + 1 < attempts:
                    await asyncio.sleep(self._flush_seconds)
                    continue
                self._dropped += len(batch)
                logger.warning(
                    "conversation_telemetry_publish_failed",
                    device_id=self._device_id,
                    event_count=len(batch),
                    final=final,
                    error=type(error).__name__,
                )


def _safe_payload(payload: Mapping[str, object]) -> dict[str, object]:
    sanitized = {
        str(key)[:64]: _sanitize_value(str(key), value, depth=0)
        for key, value in payload.items()
    }
    try:
        encoded = orjson.dumps(sanitized)
    except (TypeError, ValueError):
        return {"redacted": True, "reason": "not_json_serializable"}
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        return {"redacted": True, "reason": "payload_too_large"}
    return sanitized


def _sanitize_value(key: str, value: object, *, depth: int) -> object:
    normalized_key = key.lower()
    if normalized_key in _REDACTED_KEYS or normalized_key.endswith(("_text", "_token")):
        return "[redacted]"
    if depth >= 4:
        return "[truncated]"
    if value is None or isinstance(value, str | int | float | bool):
        return value[:256] if isinstance(value, str) else value
    if isinstance(value, Mapping):
        return {
            str(child_key)[:64]: _sanitize_value(str(child_key), child_value, depth=depth + 1)
            for child_key, child_value in list(value.items())[:32]
        }
    if isinstance(value, list | tuple):
        return [_sanitize_value(key, item, depth=depth + 1) for item in value[:32]]
    return "[redacted]"
