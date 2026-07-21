from __future__ import annotations

import asyncio
from time import monotonic

import pytest

from veetee_voice_server.conversation.arbiter import ConversationState, TurnArbiter
from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
    await_operation,
)
from veetee_voice_server.conversation.types import WakeSource

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("source", [WakeSource.BUTTON, WakeSource.WAKE_WORD])
async def test_button_and_wake_word_open_the_same_auto_gate(source: WakeSource) -> None:
    arbiter = TurnArbiter("session-1")
    snapshot = await arbiter.open_assistant(source)

    assert snapshot.assistant_gate_open is True
    assert snapshot.state is ConversationState.LISTENING


async def test_abort_invalidates_generation_before_returning_to_listening() -> None:
    arbiter = TurnArbiter("session-1")
    await arbiter.open_assistant(WakeSource.BUTTON)
    turn = await arbiter.begin_turn(30)

    receipt = await arbiter.abort("button_interrupt")

    assert turn.token.cancelled is True
    assert receipt.cancelled_turn_id == turn.turn_id
    assert arbiter.is_current(turn) is False
    assert arbiter.snapshot.state is ConversationState.CANCELLING

    snapshot = await arbiter.finish_cancellation(receipt)
    assert snapshot.state is ConversationState.LISTENING


async def test_close_gate_cancels_current_turn_and_enters_standby() -> None:
    arbiter = TurnArbiter("session-1")
    await arbiter.open_assistant(WakeSource.WAKE_WORD)
    turn = await arbiter.begin_turn(30)

    snapshot = await arbiter.close_assistant("user_requested")

    assert turn.token.cancelled is True
    assert snapshot.assistant_gate_open is False
    assert snapshot.state is ConversationState.STANDBY


async def test_external_task_cancellation_cleans_up_child_operation() -> None:
    child_stopped = asyncio.Event()

    async def child() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            child_stopped.set()

    context = OperationContext(
        "session-1", "session-1:1", 1, CancellationToken(), monotonic() + 5
    )
    task = asyncio.create_task(await_operation(child(), context))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(child_stopped.wait(), timeout=0.1)
