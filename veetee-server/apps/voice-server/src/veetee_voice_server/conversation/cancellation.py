from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass
from time import monotonic


class TurnCancelledError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(f"Turn cancelled: {reason}")
        self.reason = reason


class OperationDeadlineExceededError(TimeoutError):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason = "cancelled"

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def cancel(self, reason: str) -> None:
        if self._event.is_set():
            return
        self._reason = reason
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TurnCancelledError(self.reason)

    async def wait(self) -> None:
        await self._event.wait()


@dataclass(frozen=True, slots=True)
class OperationContext:
    session_id: str
    turn_id: str
    generation: int
    token: CancellationToken
    deadline_at: float

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_at - monotonic())

    def child(self, timeout_seconds: float) -> OperationContext:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        return OperationContext(
            session_id=self.session_id,
            turn_id=self.turn_id,
            generation=self.generation,
            token=self.token,
            deadline_at=min(self.deadline_at, monotonic() + timeout_seconds),
        )

    def checkpoint(self) -> None:
        self.token.raise_if_cancelled()
        if self.remaining_seconds <= 0:
            raise OperationDeadlineExceededError(f"Deadline exceeded for {self.turn_id}")


def _consume_task_result(task: asyncio.Task[object]) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except asyncio.CancelledError:
        return


async def await_operation[T](awaitable: Awaitable[T], context: OperationContext) -> T:
    context.checkpoint()
    operation = asyncio.ensure_future(awaitable)
    cancellation = asyncio.create_task(context.token.wait())
    try:
        done, _ = await asyncio.wait(
            {operation, cancellation},
            timeout=context.remaining_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancellation in done:
            operation.cancel()
            operation.add_done_callback(_consume_task_result)
            raise TurnCancelledError(context.token.reason)
        if operation in done:
            return await operation

        operation.cancel()
        operation.add_done_callback(_consume_task_result)
        raise OperationDeadlineExceededError(f"Deadline exceeded for {context.turn_id}")
    finally:
        cancellation.cancel()


async def iterate_operation[T](
    stream: AsyncIterator[T], context: OperationContext
) -> AsyncIterator[T]:
    while True:
        try:
            item = await await_operation(anext(stream), context)
        except StopAsyncIteration:
            return
        yield item
