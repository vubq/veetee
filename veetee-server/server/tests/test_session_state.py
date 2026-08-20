import asyncio
from uuid import UUID

import pytest

from veetee_server.domain.errors import (
    CleanupTimeoutError,
    DomainErrorCode,
    InvalidTransitionError,
    StaleGenerationError,
)
from veetee_server.domain.session import (
    DeviceSession,
    GenerationState,
    SessionState,
    TurnState,
)


def make_session(*, cleanup_timeout_seconds: float = 2.0) -> DeviceSession:
    session = DeviceSession(
        device_id="device-opaque",
        client_id="client-opaque",
        cleanup_timeout_seconds=cleanup_timeout_seconds,
    )
    session.accept()
    return session


async def stream_turn(session: DeviceSession):
    turn = await session.start_turn()
    session.begin_processing()
    generation = session.begin_streaming()
    return turn, generation


@pytest.mark.asyncio
async def test_session_turn_generation_happy_path() -> None:
    session = make_session()
    turn, generation = await stream_turn(session)

    assert session.state is SessionState.SPEAKING
    assert turn.state is TurnState.STREAMING
    assert UUID(str(generation.id))
    session.ensure_current_generation(generation)

    session.complete_turn()

    assert session.state is SessionState.IDLE
    assert turn.state is TurnState.COMPLETED
    assert generation.state is GenerationState.COMPLETED
    assert session.current_turn is None
    assert session.generations == {}


@pytest.mark.asyncio
async def test_out_of_order_turn_transition_is_typed_error() -> None:
    session = make_session()
    await session.start_turn()

    with pytest.raises(InvalidTransitionError) as error:
        session.begin_streaming()

    assert error.value.code is DomainErrorCode.INVALID_TRANSITION


@pytest.mark.asyncio
async def test_duplicate_start_is_rejected_without_replacing_turn() -> None:
    session = make_session()
    turn = await session.start_turn()

    with pytest.raises(InvalidTransitionError):
        await session.start_turn()

    assert session.current_turn is turn
    assert turn.state is TurnState.CAPTURING


@pytest.mark.asyncio
async def test_abort_is_idempotent_and_stales_generation() -> None:
    session = make_session()
    _, generation = await stream_turn(session)

    await session.abort_turn()
    await session.abort_turn()

    assert session.state is SessionState.IDLE
    assert generation.state is GenerationState.STALE
    assert session.current_turn is None
    assert session.generations == {}


@pytest.mark.asyncio
async def test_stale_generation_cannot_emit_output_after_barge_in() -> None:
    session = make_session()
    _, old_generation = await stream_turn(session)

    await session.start_turn()
    session.begin_processing()
    new_generation = session.begin_streaming()

    with pytest.raises(StaleGenerationError) as error:
        session.ensure_current_generation(old_generation)
    assert error.value.code is DomainErrorCode.STALE_GENERATION
    session.ensure_current_generation(new_generation)


@pytest.mark.asyncio
async def test_fail_turn_cleans_aggregate_and_cancels_generation() -> None:
    session = make_session()
    turn, generation = await stream_turn(session)

    await session.fail_turn()

    assert session.state is SessionState.IDLE
    assert turn.state is TurnState.FAILED
    assert generation.state is GenerationState.CANCELLED
    assert session.current_turn is None
    assert session.generations == {}


@pytest.mark.asyncio
async def test_complete_rejects_cancelled_generation_without_mutating_turn() -> None:
    session = make_session()
    turn, generation = await stream_turn(session)
    generation._cancel()

    with pytest.raises(InvalidTransitionError):
        session.complete_turn()

    assert session.state is SessionState.SPEAKING
    assert turn.state is TurnState.STREAMING
    assert session.current_turn is turn


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["complete", "abort", "fail"])
async def test_late_output_is_always_stale(terminal: str) -> None:
    session = make_session()
    _, generation = await stream_turn(session)

    if terminal == "complete":
        session.complete_turn()
    elif terminal == "abort":
        await session.abort_turn()
    else:
        await session.fail_turn()

    with pytest.raises(StaleGenerationError):
        session.ensure_current_generation(generation)


@pytest.mark.asyncio
async def test_turn_cancellation_waits_for_child_task_cleanup() -> None:
    session = make_session()
    turn, _ = await stream_turn(session)
    cleaned = asyncio.Event()

    async def child() -> None:
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    task = turn.cancellation.create_task(child())
    await asyncio.sleep(0)

    await session.abort_turn()

    assert task.cancelled()
    assert cleaned.is_set()
    assert turn.cancellation.task_count == 0


@pytest.mark.asyncio
async def test_cleanup_timeout_is_typed_and_aggregate_still_retires() -> None:
    session = make_session(cleanup_timeout_seconds=0.01)
    turn, _ = await stream_turn(session)
    release = asyncio.Event()

    async def cancellation_resistant_child() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await release.wait()

    task = turn.cancellation.create_task(cancellation_resistant_child())
    await asyncio.sleep(0)

    with pytest.raises(CleanupTimeoutError) as error:
        await session.abort_turn()

    assert error.value.code is DomainErrorCode.CLEANUP_TIMEOUT
    assert session.state is SessionState.IDLE
    assert session.current_turn is None
    assert session.generations == {}
    release.set()
    await task


@pytest.mark.asyncio
async def test_generation_registry_remains_bounded_across_turns() -> None:
    session = make_session()

    for _ in range(100):
        await stream_turn(session)
        session.complete_turn()

    assert session.state is SessionState.IDLE
    assert session.generations == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["connecting", "idle", "listening", "speaking"])
async def test_close_cleans_every_session_state(initial: str) -> None:
    session = DeviceSession(device_id="device", client_id="client")
    if initial != "connecting":
        session.accept()
    if initial in {"listening", "speaking"}:
        await session.start_turn()
    if initial == "speaking":
        session.begin_processing()
        session.begin_streaming()

    await session.close()
    await session.close()

    assert session.state is SessionState.CLOSED
    assert session.current_turn is None
    assert session.generations == {}


@pytest.mark.asyncio
async def test_closed_session_rejects_new_turn() -> None:
    session = make_session()
    await session.close()

    with pytest.raises(InvalidTransitionError):
        await session.start_turn()


def test_identity_and_cleanup_timeout_validation() -> None:
    with pytest.raises(ValueError):
        DeviceSession(device_id="", client_id="client")
    with pytest.raises(ValueError):
        DeviceSession(device_id="device", client_id="", cleanup_timeout_seconds=0)


def test_state_and_generation_mapping_are_read_only() -> None:
    session = make_session()

    with pytest.raises(AttributeError):
        session.state = SessionState.CLOSED  # type: ignore[misc]
    with pytest.raises(TypeError):
        session.generations[UUID(int=0)] = None  # type: ignore[index,assignment]
