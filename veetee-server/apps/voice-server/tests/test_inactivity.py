from __future__ import annotations

import asyncio

import pytest

from veetee_voice_server.conversation.arbiter import ConversationState, TurnArbiter
from veetee_voice_server.conversation.inactivity import InactivityController
from veetee_voice_server.conversation.types import WakeSource

pytestmark = pytest.mark.asyncio


async def test_inactivity_goodbye_closes_gate_after_grace() -> None:
    arbiter = TurnArbiter("session-1")
    goodbye_reasons: list[str] = []

    async def goodbye(reason: str) -> None:
        goodbye_reasons.append(reason)

    controller = InactivityController(
        arbiter=arbiter,
        first_input_seconds=0.01,
        between_turns_seconds=0.01,
        closing_grace_seconds=0.01,
        max_session_seconds=1,
        goodbye=goodbye,
    )
    await controller.assistant_opened(WakeSource.BUTTON)
    await asyncio.sleep(0.04)

    assert goodbye_reasons == ["first_input_timeout"]
    assert arbiter.snapshot.state is ConversationState.STANDBY


async def test_wake_during_closing_cancels_goodbye_close() -> None:
    arbiter = TurnArbiter("session-1")
    goodbye_started = asyncio.Event()
    goodbye_release = asyncio.Event()

    async def goodbye(_: str) -> None:
        goodbye_started.set()
        await goodbye_release.wait()

    controller = InactivityController(
        arbiter=arbiter,
        first_input_seconds=0.01,
        between_turns_seconds=1,
        closing_grace_seconds=0.01,
        max_session_seconds=1,
        goodbye=goodbye,
    )
    await controller.assistant_opened(WakeSource.WAKE_WORD)
    await goodbye_started.wait()
    await controller.wake_during_closing(WakeSource.BUTTON)

    assert arbiter.snapshot.state is ConversationState.LISTENING
    goodbye_release.set()
    await controller.close()


async def test_rejected_candidate_resumes_original_timeout_deadline() -> None:
    arbiter = TurnArbiter("session-1")
    goodbye = asyncio.Event()

    async def on_goodbye(_: str) -> None:
        goodbye.set()

    controller = InactivityController(
        arbiter=arbiter,
        first_input_seconds=0.03,
        between_turns_seconds=1,
        closing_grace_seconds=0.01,
        max_session_seconds=1,
        goodbye=on_goodbye,
    )
    await controller.assistant_opened(WakeSource.BUTTON)
    await asyncio.sleep(0.015)
    await controller.candidate_started()
    await asyncio.sleep(0.025)
    assert not goodbye.is_set()

    await controller.candidate_rejected()
    await asyncio.wait_for(goodbye.wait(), timeout=0.02)
    await controller.close()


async def test_absolute_session_ceiling_cancels_an_active_turn() -> None:
    arbiter = TurnArbiter("session-1")
    goodbye_reasons: list[str] = []

    async def goodbye(reason: str) -> None:
        goodbye_reasons.append(reason)

    controller = InactivityController(
        arbiter=arbiter,
        first_input_seconds=1,
        between_turns_seconds=1,
        closing_grace_seconds=0.01,
        max_session_seconds=0.01,
        goodbye=goodbye,
    )
    await controller.assistant_opened(WakeSource.BUTTON)
    turn = await arbiter.begin_turn(1)
    await asyncio.sleep(0.04)

    assert turn.token.cancelled is True
    assert goodbye_reasons == ["max_session_duration"]
    assert arbiter.snapshot.state is ConversationState.STANDBY

async def test_zero_session_ceiling_does_not_close_an_active_session() -> None:
    arbiter = TurnArbiter("session-unlimited")
    goodbye_reasons: list[str] = []

    async def goodbye(reason: str) -> None:
        goodbye_reasons.append(reason)

    controller = InactivityController(
        arbiter=arbiter,
        first_input_seconds=1,
        between_turns_seconds=1,
        closing_grace_seconds=0.01,
        max_session_seconds=0,
        goodbye=goodbye,
    )
    await controller.assistant_opened(WakeSource.BUTTON)
    await asyncio.sleep(0.03)

    assert goodbye_reasons == []
    assert arbiter.snapshot.state is ConversationState.LISTENING
    await controller.close()

async def test_zero_turn_ceiling_keeps_operation_deadline_open() -> None:
    arbiter = TurnArbiter("session-unlimited-turn")
    await arbiter.open_assistant(WakeSource.BUTTON)
    context = await arbiter.begin_turn(0)

    assert context.deadline_at == float("inf")
    assert context.remaining_seconds == float("inf")

    await arbiter.abort("test_cleanup")

async def test_negative_turn_ceiling_is_rejected() -> None:
    arbiter = TurnArbiter("session-invalid-turn")
    await arbiter.open_assistant(WakeSource.BUTTON)

    with pytest.raises(ValueError, match="non-negative"):
        await arbiter.begin_turn(-1)


async def test_interrupt_closing_cancels_goodbye_without_closing_gate() -> None:
    arbiter = TurnArbiter("session-1")
    goodbye_started = asyncio.Event()
    goodbye_cancelled = asyncio.Event()

    async def goodbye(_: str) -> None:
        goodbye_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            goodbye_cancelled.set()

    controller = InactivityController(
        arbiter=arbiter,
        first_input_seconds=0.01,
        between_turns_seconds=1,
        closing_grace_seconds=1,
        max_session_seconds=1,
        goodbye=goodbye,
    )
    await controller.assistant_opened(WakeSource.BUTTON)
    await goodbye_started.wait()
    receipt = await arbiter.abort("button_interrupt")
    await controller.interrupt_closing()
    await arbiter.finish_cancellation(receipt)

    await asyncio.wait_for(goodbye_cancelled.wait(), timeout=0.1)
    assert arbiter.snapshot.state is ConversationState.LISTENING
    await controller.close()
