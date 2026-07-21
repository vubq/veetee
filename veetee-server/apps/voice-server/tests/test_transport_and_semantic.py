from __future__ import annotations

from time import monotonic

import pytest

from veetee_voice_server.config import Settings
from veetee_voice_server.conversation.cancellation import CancellationToken, OperationContext
from veetee_voice_server.conversation.types import (
    AdmissionDecision,
    AdmissionDisposition,
    DialogueAct,
    PlanAction,
    Transcript,
)
from veetee_voice_server.providers.semantic import JsonPlannerProvider, LocalAdmissionProvider
from veetee_voice_server.transport.opus import OpusDecoder, OpusEncoder

pytestmark = pytest.mark.asyncio


def context() -> OperationContext:
    return OperationContext(
        "session", "turn", 1, CancellationToken(), monotonic() + 5
    )


async def test_local_admission_rejects_no_linguistic_signal_without_llm() -> None:
    provider = LocalAdmissionProvider()
    result = await provider.evaluate(Transcript("...", "vi-VN"), context())
    assert result.disposition is AdmissionDisposition.NON_ACTIONABLE


async def test_planner_tolerates_unknown_model_dialogue_label() -> None:
    async def complete_json(_: object, __: object) -> dict[str, object]:
        return {
            "action": "respond",
            "dialogue_act": "inform",
            "locale": "vi-VN",
            "intent": "dynamic.intent",
            "response_required": True,
        }

    planner = JsonPlannerProvider(complete_json)
    plan = await planner.plan(
        Transcript("Xin chào", "vi-VN"),
        AdmissionDecision(AdmissionDisposition.ACCEPTED, 1.0, "test"),
        context(),
    )
    assert plan.action is PlanAction.RESPOND
    assert plan.dialogue_act is DialogueAct.ANSWER


async def test_opus_round_trip_20_ms_mono_frame() -> None:
    encoder = OpusEncoder(16_000)
    decoder = OpusDecoder(16_000)
    try:
        packet = encoder.encode(b"\0\0" * 320, frame_samples=320)
        decoded = decoder.decode(packet)
    finally:
        encoder.close()
        decoder.close()
    assert len(decoded) == 640


async def test_opus_round_trip_60_ms_24khz_downlink_frame() -> None:
    encoder = OpusEncoder(24_000)
    decoder = OpusDecoder(24_000)
    try:
        packet = encoder.encode(b"\0\0" * 1_440, frame_samples=1_440)
        decoded = decoder.decode(packet)
    finally:
        encoder.close()
        decoder.close()
    assert len(decoded) == 2_880


async def test_9router_environment_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VEETEE_9ROUTER_API_KEY", "sentinel")
    monkeypatch.setenv("VEETEE_9ROUTER_MODEL", "cx/test")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.nine_router_api_key == "sentinel"
    assert settings.nine_router_model == "cx/test"
