import asyncio
from typing import Literal, cast
from uuid import UUID

import pytest

from veetee_server.domain.errors import (
    CleanupTimeoutError,
    DomainErrorCode,
    InvalidTransitionError,
    StaleGenerationError,
)
from veetee_server.domain.session import (
    ConversationTurn,
    DeviceSession,
    Generation,
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


async def stream_turn(session: DeviceSession) -> tuple[ConversationTurn, Generation]:
    turn = await session.start_turn()
    session.begin_processing()
    generation = session.begin_streaming()
    return turn, generation


@pytest.mark.asyncio
async def test_session_turn_generation_happy_path() -> None:
    session = make_session()
    turn, generation = await stream_turn(session)

    assert session.state == SessionState.SPEAKING
    assert turn.state is TurnState.STREAMING
    assert UUID(str(generation.id))
    session.ensure_current_generation(generation)

    session.complete_turn()

    assert cast(SessionState, session.state) == SessionState.IDLE
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
    old_epoch = session.egress_queue.generation

    await session.start_turn()
    assert session.egress_queue.generation == old_epoch + 1
    session.begin_processing()
    new_generation = session.begin_streaming()

    with pytest.raises(StaleGenerationError) as error:
        session.ensure_current_generation(old_generation)
    assert error.value.code is DomainErrorCode.STALE_GENERATION
    session.ensure_current_generation(new_generation)


@pytest.mark.asyncio
async def test_new_turn_purges_completed_turn_downlink() -> None:
    session = make_session()
    await stream_turn(session)
    old_epoch = session.egress_queue.generation
    session.complete_turn()

    from veetee_server.pipeline.downlink import DownlinkItem, DownlinkKind

    await session.egress_queue.put(
        DownlinkItem(kind=DownlinkKind.CONTROL, payload=b"old", generation=old_epoch)
    )
    await session.start_turn()

    assert session.egress_queue.generation == old_epoch + 1
    assert session.egress_queue.item_count == 0


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
        session.generations[UUID(int=0)] = None  # type: ignore[index]


@pytest.mark.parametrize(
    "aec_enabled, listen_mode, expected_eligible",
    [
        (True, "realtime", True),
        (True, "auto", False),
        (True, "manual", False),
        (True, None, False),
        (False, "realtime", False),
        (False, "auto", False),
        (False, "manual", False),
        (False, None, False),
    ],
)
def test_barge_in_eligibility_matrix(
    aec_enabled: bool,
    listen_mode: Literal["auto", "manual", "realtime"] | None,
    expected_eligible: bool,
) -> None:
    session = make_session()
    session.negotiate_features({"aec": aec_enabled})
    session.listen_mode = listen_mode

    assert session.aec_enabled is aec_enabled
    assert session.is_barge_in_eligible is expected_eligible


@pytest.mark.asyncio
async def test_begin_barge_in_expected_turn_guard() -> None:
    session = make_session()
    session.negotiate_features({"aec": True})
    session.listen_mode = "realtime"

    turn, _ = await stream_turn(session)
    wrong_turn = ConversationTurn()

    # Wrong expected turn -> guard rejects barge in
    res = await session.begin_barge_in(wrong_turn)
    assert res is None
    assert session.state is SessionState.SPEAKING
    assert session.current_turn is turn


@pytest.mark.asyncio
async def test_begin_barge_in_state_and_eligibility_guards() -> None:
    session = make_session()

    # Case 1: Session not in SPEAKING (e.g. IDLE or LISTENING)
    dummy_turn = ConversationTurn()
    assert await session.begin_barge_in(dummy_turn) is None

    # Case 2: Session in SPEAKING but not eligible (e.g. aec=False)
    session.negotiate_features({"aec": False})
    session.listen_mode = "realtime"
    turn, _ = await stream_turn(session)
    assert await session.begin_barge_in(turn) is None
    assert session.state is SessionState.SPEAKING
    assert session.current_turn is turn


@pytest.mark.asyncio
async def test_begin_barge_in_single_epoch_advance_and_turn_capture() -> None:
    session = make_session()
    session.negotiate_features({"aec": True})
    session.listen_mode = "realtime"

    turn1, gen1 = await stream_turn(session)
    old_ingress_gen = session.ingress_queue.generation
    old_egress_gen = session.egress_queue.generation
    assert old_ingress_gen == old_egress_gen

    new_turn = await session.begin_barge_in(turn1)
    assert new_turn is not None
    assert new_turn is not turn1

    # Exactly one epoch advance
    assert session.ingress_queue.generation == old_ingress_gen + 1
    assert session.egress_queue.generation == old_egress_gen + 1

    # Stale generation check
    assert gen1.state is GenerationState.STALE
    with pytest.raises(StaleGenerationError):
        session.ensure_current_generation(gen1)

    # Capture turn state & session state
    assert new_turn.state is TurnState.CAPTURING
    assert session.state is SessionState.LISTENING
    assert session.current_turn is new_turn


@pytest.mark.asyncio
async def test_begin_barge_in_noop_on_completed_or_aborted_turn_race() -> None:
    # Race condition 1: old turn completed before begin_barge_in
    session1 = make_session()
    session1.negotiate_features({"aec": True})
    session1.listen_mode = "realtime"
    turn1, _ = await stream_turn(session1)
    session1.complete_turn()
    assert await session1.begin_barge_in(turn1) is None
    assert session1.state is SessionState.IDLE

    # Race condition 2: old turn aborted before begin_barge_in
    session2 = make_session()
    session2.negotiate_features({"aec": True})
    session2.listen_mode = "realtime"
    turn2, _ = await stream_turn(session2)
    await session2.abort_turn()
    assert await session2.begin_barge_in(turn2) is None
    assert session2.state is SessionState.IDLE


@pytest.mark.asyncio
async def test_concurrent_barge_in_advances_epoch_once() -> None:
    session = make_session()
    session.negotiate_features({"aec": True})
    session.listen_mode = "realtime"
    old_turn, _ = await stream_turn(session)
    old_epoch = session.egress_queue.generation
    cleanup_started = asyncio.Event()

    async def child() -> None:
        try:
            await asyncio.Future()
        finally:
            cleanup_started.set()
            await asyncio.sleep(0)

    old_turn.cancellation.create_task(child())
    await asyncio.sleep(0)

    first, second = await asyncio.gather(
        session.begin_barge_in(old_turn),
        session.begin_barge_in(old_turn),
    )

    assert cleanup_started.is_set()
    assert (first is None) != (second is None)
    assert session.egress_queue.generation == old_epoch + 1
    assert session.state is SessionState.LISTENING
