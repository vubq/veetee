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
