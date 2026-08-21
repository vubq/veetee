"""In-memory session, turn and generation state machines."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid4

from veetee_server.audio.pacer import PacketPacer
from veetee_server.audio.queue import BoundedAudioQueue, OverflowPolicy

from .errors import CleanupTimeoutError, InvalidTransitionError, StaleGenerationError

if TYPE_CHECKING:
    from veetee_server.pipeline.downlink import DownlinkQueue


def _default_egress_queue() -> DownlinkQueue:
    # Imported lazily to avoid the import cycle domain.session -> pipeline
    # package -> pipeline.factory -> domain.session. The pipeline package owns
    # the downlink queue type but depends on this module, so the dependency
    # edge must point pipeline -> domain, never the reverse.
    from veetee_server.pipeline.downlink import DownlinkQueue

    return DownlinkQueue(overflow_policy=OverflowPolicy.FAIL_SESSION)


def _now() -> datetime:
    return datetime.now(UTC)


class SessionState(StrEnum):
    CONNECTING = "connecting"
    IDLE = "idle"
    LISTENING = "listening"
    SPEAKING = "speaking"
    CLOSING = "closing"
    CLOSED = "closed"


class TurnState(StrEnum):
    CREATED = "created"
    CAPTURING = "capturing"
    PROCESSING = "processing"
    STREAMING = "streaming"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class GenerationState(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    STALE = "stale"


@dataclass
class CancellationScope:
    """Own child tasks and wait for cancellation within an explicit deadline."""

    _tasks: set[asyncio.Task[Any]] = field(default_factory=set, init=False, repr=False)
    _cancel_requested: bool = field(default=False, init=False, repr=False)

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    @property
    def task_count(self) -> int:
        return len(self._tasks)

    def create_task(self, coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self.register(task)
        return task

    def register(self, task: asyncio.Task[Any]) -> None:
        if self._cancel_requested:
            task.cancel()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def request_cancel(self) -> tuple[asyncio.Task[Any], ...]:
        self._cancel_requested = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        return tasks

    async def cancel_and_wait(self, timeout_seconds: float) -> None:
        if timeout_seconds <= 0:
            raise ValueError("cleanup timeout must be positive")
        tasks = self.request_cancel()
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
        if pending:
            raise CleanupTimeoutError(timeout_seconds)


@dataclass
class Generation:
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=_now)
    _state: GenerationState = field(default=GenerationState.ACTIVE, init=False, repr=False)

    @property
    def state(self) -> GenerationState:
        return self._state

    def _complete(self) -> None:
        self._transition(GenerationState.COMPLETED)

    def _cancel(self) -> None:
        if self._state is GenerationState.ACTIVE:
            self._state = GenerationState.CANCELLED

    def _mark_stale(self) -> None:
        if self._state is GenerationState.ACTIVE:
            self._state = GenerationState.STALE

    def ensure_current(self) -> None:
        if self._state is not GenerationState.ACTIVE:
            raise StaleGenerationError(str(self.id))

    def _transition(self, target: GenerationState) -> None:
        if self._state is not GenerationState.ACTIVE:
            raise InvalidTransitionError("generation", self._state, target)
        self._state = target


@dataclass
class ConversationTurn:
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=_now)
    cancellation: CancellationScope = field(default_factory=CancellationScope)
    _state: TurnState = field(default=TurnState.CREATED, init=False, repr=False)
    _generation: Generation | None = field(default=None, init=False, repr=False)

    @property
    def state(self) -> TurnState:
        return self._state

    @property
    def generation(self) -> Generation | None:
        return self._generation

    def _start_capture(self) -> None:
        self._transition(TurnState.CAPTURING)

    def _start_processing(self) -> None:
        self._transition(TurnState.PROCESSING)

    def _start_streaming(self) -> Generation:
        self._transition(TurnState.STREAMING)
        self._generation = Generation()
        return self._generation

    def _complete(self) -> None:
        if self._generation is None or self._generation.state is not GenerationState.ACTIVE:
            target = self._generation.state if self._generation else GenerationState.CANCELLED
            raise InvalidTransitionError("generation", target, GenerationState.COMPLETED)
        self._transition(TurnState.COMPLETED)
        self._generation._complete()

    def _request_abort(self) -> None:
        if self._state not in {TurnState.COMPLETED, TurnState.FAILED, TurnState.ABORTED}:
            self._state = TurnState.ABORTED
        if self._generation:
            self._generation._mark_stale()
        self.cancellation.request_cancel()

    def _request_failure(self) -> None:
        if self._state in {TurnState.COMPLETED, TurnState.ABORTED, TurnState.FAILED}:
            raise InvalidTransitionError("turn", self._state, TurnState.FAILED)
        self._state = TurnState.FAILED
        if self._generation:
            self._generation._cancel()
        self.cancellation.request_cancel()

    def ensure_current_generation(self, generation: Generation) -> None:
        if self._generation is not generation:
            raise StaleGenerationError(str(generation.id))
        generation.ensure_current()

    def _transition(self, target: TurnState) -> None:
        allowed = {
            TurnState.CREATED: {TurnState.CAPTURING},
            TurnState.CAPTURING: {TurnState.PROCESSING},
            TurnState.PROCESSING: {TurnState.STREAMING},
            TurnState.STREAMING: {TurnState.COMPLETED},
        }
        if target not in allowed.get(self._state, set()):
            raise InvalidTransitionError("turn", self._state, target)
        self._state = target


@dataclass
class DeviceSession:
    device_id: str
    client_id: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=_now)
    cancellation: CancellationScope = field(default_factory=CancellationScope)
    cleanup_timeout_seconds: float = 2.0
    protocol_version: int = 1
    pacer: PacketPacer = field(default_factory=PacketPacer)
    ingress_queue: BoundedAudioQueue = field(
        default_factory=lambda: BoundedAudioQueue(overflow_policy=OverflowPolicy.DROP_OLDEST)
    )
    # Downlink queue carries ordered control + audio items toward the device.
    # It must stay a fail-session queue: a slow client must not buffer forever.
    egress_queue: DownlinkQueue = field(default_factory=_default_egress_queue)
    features: Mapping[str, bool] = field(default_factory=dict)
    listen_mode: Literal["auto", "manual", "realtime"] | None = None
    _state: SessionState = field(default=SessionState.CONNECTING, init=False, repr=False)
    _current_turn: ConversationTurn | None = field(default=None, init=False, repr=False)
    _generations: dict[UUID, Generation] = field(default_factory=dict, init=False, repr=False)
    _barge_in_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.device_id or not self.client_id:
            raise ValueError("device_id and client_id must be non-empty")
        if self.cleanup_timeout_seconds <= 0:
            raise ValueError("cleanup timeout must be positive")

    @property
    def aec_enabled(self) -> bool:
        return bool(self.features.get("aec", False))

    @property
    def is_barge_in_eligible(self) -> bool:
        return self.aec_enabled and self.listen_mode == "realtime"

    def negotiate_features(self, features: Mapping[str, bool] | None) -> None:
        self.features = MappingProxyType(dict(features or {}))

    async def begin_barge_in(self, expected_turn: ConversationTurn) -> ConversationTurn | None:
        """Atomically replaces a streaming turn without advancing its fresh I/O epoch twice."""
        async with self._barge_in_lock:
            if (
                self._state is not SessionState.SPEAKING
                or self._current_turn is not expected_turn
                or not self.is_barge_in_eligible
            ):
                return None
            await self.abort_turn()
            turn = ConversationTurn()
            turn._start_capture()
            self._current_turn = turn
            self._transition(SessionState.LISTENING)
            return turn

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def current_turn(self) -> ConversationTurn | None:
        return self._current_turn

    @property
    def generations(self) -> Mapping[UUID, Generation]:
        return MappingProxyType(self._generations)

    def accept(self) -> None:
        self._transition(SessionState.IDLE)

    async def start_turn(self) -> ConversationTurn:
        # A new listen/start barge-in is allowed while the session is speaking
        # (streaming) or while the previous turn is still being processed
        # (IDLE state with a processing turn); both abort the old turn first.
        replaced_turn = self._state is SessionState.SPEAKING or (
            self._state is SessionState.IDLE and self._current_turn is not None
        )
        if replaced_turn:
            await self.abort_turn()
        if self._state is not SessionState.IDLE or self._current_turn is not None:
            raise InvalidTransitionError("session", self._state, SessionState.LISTENING)
        if not replaced_turn:
            self._advance_queue_generation()
        turn = ConversationTurn()
        turn._start_capture()
        self._current_turn = turn
        self._transition(SessionState.LISTENING)
        return turn

    def begin_processing(self) -> None:
        turn = self._require_turn()
        turn._start_processing()
        self._transition(SessionState.IDLE)

    def begin_streaming(self) -> Generation:
        turn = self._require_turn()
        generation = turn._start_streaming()
        self._generations[generation.id] = generation
        self._transition(SessionState.SPEAKING)
        return generation

    def complete_turn(self) -> None:
        turn = self._require_turn()
        turn._complete()
        self._retire_turn(turn)

    async def abort_turn(self) -> None:
        turn = self._current_turn
        # Increment queue generation to drop any stale packets in flight.
        self._advance_queue_generation()
        # Reset the downlink pacing anchor so the next TTS stream starts fresh
        # instead of inheriting drift from the aborted stream.
        self.pacer.reset()
        if turn is None:
            if self._state not in {SessionState.CLOSING, SessionState.CLOSED}:
                self._transition_to_idle()
            return
        turn._request_abort()
        try:
            await turn.cancellation.cancel_and_wait(self.cleanup_timeout_seconds)
        finally:
            self._retire_turn(turn)

    async def fail_turn(self) -> None:
        turn = self._require_turn()
        turn._request_failure()
        try:
            await turn.cancellation.cancel_and_wait(self.cleanup_timeout_seconds)
        finally:
            self._retire_turn(turn)

    async def close(self) -> None:
        if self._state is SessionState.CLOSED:
            return
        self._transition(SessionState.CLOSING)
        cleanup_error: CleanupTimeoutError | None = None
        try:
            try:
                await self.abort_turn()
            except CleanupTimeoutError as error:
                cleanup_error = error
            try:
                await self.cancellation.cancel_and_wait(self.cleanup_timeout_seconds)
            except CleanupTimeoutError as error:
                cleanup_error = cleanup_error or error
        finally:
            self.ingress_queue.close()
            self.egress_queue.close()
            self._current_turn = None
            self._generations.clear()
            self._transition(SessionState.CLOSED)
        if cleanup_error:
            raise cleanup_error

    def ensure_current_generation(self, generation: Generation) -> None:
        turn = self._current_turn
        if turn is None or generation.id not in self._generations:
            raise StaleGenerationError(str(generation.id))
        turn.ensure_current_generation(generation)

    def _require_turn(self) -> ConversationTurn:
        if self._current_turn is None:
            raise InvalidTransitionError("session", self._state, SessionState.LISTENING)
        return self._current_turn

    def _advance_queue_generation(self) -> None:
        """Starts a fresh I/O epoch and purges output from earlier turns."""
        new_generation = max(self.ingress_queue.generation, self.egress_queue.generation) + 1
        self.ingress_queue.set_generation(new_generation)
        self.egress_queue.set_generation(new_generation)

    def _retire_turn(self, turn: ConversationTurn) -> None:
        if turn.generation:
            self._generations.pop(turn.generation.id, None)
        if self._current_turn is turn:
            self._current_turn = None
        if self._state not in {SessionState.CLOSING, SessionState.CLOSED}:
            self._transition_to_idle()

    def _transition_to_idle(self) -> None:
        if self._state is not SessionState.IDLE:
            self._transition(SessionState.IDLE)

    def _transition(self, target: SessionState) -> None:
        allowed = {
            SessionState.CONNECTING: {SessionState.IDLE, SessionState.CLOSING},
            SessionState.IDLE: {
                SessionState.LISTENING,
                SessionState.SPEAKING,
                SessionState.CLOSING,
            },
            SessionState.LISTENING: {SessionState.IDLE, SessionState.CLOSING},
            SessionState.SPEAKING: {SessionState.IDLE, SessionState.CLOSING},
            SessionState.CLOSING: {SessionState.CLOSED},
        }
        if target not in allowed.get(self._state, set()):
            raise InvalidTransitionError("session", self._state, target)
        self._state = target
