from __future__ import annotations

from time import monotonic

import pytest

from veetee_voice_server.config import Settings
from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
    TurnCancelledError,
)
from veetee_voice_server.conversation.evidence import (
    build_input_evidence,
    input_evidence_payload,
)
from veetee_voice_server.conversation.memory import (
    MemorySnapshot,
    StoredMemoryMessage,
)
from veetee_voice_server.conversation.types import (
    AdmissionDecision,
    AdmissionDisposition,
    ConversationMessage,
    DialogueAct,
    InputEvidence,
    InputSource,
    PlanAction,
    Transcript,
    WakeSource,
)
from veetee_voice_server.providers.semantic import (
    JsonPlannerProvider,
    LocalAdmissionProvider,
    StructuredConversationGate,
)
from veetee_voice_server.transport.opus import OpusDecoder, OpusEncoder

pytestmark = pytest.mark.asyncio


def context(
    *,
    turn_id: str = "turn",
    generation: int = 1,
    token: CancellationToken | None = None,
) -> OperationContext:
    return OperationContext(
        "session", turn_id, generation, token or CancellationToken(), monotonic() + 5
    )


def gate_payload(
    *,
    decision: str = "accepted",
    dialogue_act: str = "question",
    action: str = "respond",
    reason_code: str = "speech_relevant",
) -> dict[str, object]:
    return {
        "admission": {
            "decision": decision,
            "confidence": 0.91,
            "addressed_to_robot": 0.88,
            "reason_code": reason_code,
        },
        "dialogue_act": dialogue_act,
        "plan": {
            "action": action,
            "locale": "vi-VN",
            "intent": "dynamic.fixture",
            "response_required": True,
            "response_text": "Tôi nghe đây.",
            "tool_call": None,
        },
    }


async def test_local_admission_rejects_no_linguistic_signal_without_llm() -> None:
    provider = LocalAdmissionProvider()
    result = await provider.evaluate(Transcript("...", "vi-VN"), context())
    assert result.disposition is AdmissionDisposition.NON_ACTIONABLE


async def test_local_admission_keeps_short_contextual_reply_for_semantic_gate() -> None:
    provider = LocalAdmissionProvider()
    result = await provider.evaluate(
        Transcript(
            "Ừ",
            "vi-VN",
            context=(ConversationMessage("assistant", "Bạn thấy câu trả lời thế nào?"),),
        ),
        context(),
    )
    assert result.disposition is AdmissionDisposition.ACCEPTED


async def test_input_evidence_bounds_audio_and_keeps_unavailable_signals_explicit() -> None:
    pcm = (b"\0\0" * 80) + (b"\xff\x7f" * 20)
    evidence = build_input_evidence(
        pcm,
        sample_rate=16_000,
        source=InputSource.DEVICE_MIC,
        wake_source=WakeSource.BUTTON,
        vad_probabilities=[0.2, 0.8, 1.2, -0.2],
        noise_pcm_s16le=b"\0\0" * 20,
        server_buffer_truncated=True,
    )
    payload = input_evidence_payload(evidence)

    assert payload["source"] == "device_mic"
    assert payload["wake_source"] == "button"
    assert payload["signal"]["vad_mean_probability"] == 0.5  # type: ignore[index]
    assert payload["signal"]["clipping_ratio"] == 0.2  # type: ignore[index]
    assert payload["integrity"]["server_buffer_truncated"] is True  # type: ignore[index]
    assert payload["integrity"]["packet_loss_ratio"] is None  # type: ignore[index]
    assert payload["aec"]["self_echo_probability"] is None  # type: ignore[index]


@pytest.mark.parametrize(
    ("characters", "duration_ms", "signal_rms", "snr", "vad_mean", "vad_ratio"),
    [
        (3, 2_240, -37.90, 1.85, 0.6929, 0.7541),
        (20, 740, -28.95, 0.55, 0.3141, 0.0667),
        (61, 7_040, -33.34, 5.24, 0.7735, 0.8208),
        (16, 1_400, -29.39, 6.14, 0.3973, 0.2857),
        (12, 2_060, -28.50, -0.28, 0.4431, 0.4821),
        (17, 2_180, -38.63, 2.49, 0.6483, 0.7167),
        (10, 2_300, -35.72, 4.58, 0.5906, 0.6406),
        (29, 2_600, -34.44, -0.41, 0.7884, 0.7917),
        (3, 980, -29.97, 11.48, 0.2910, 0.2857),
        (3, 860, -29.48, 7.08, 0.2364, 0.2632),
        (5, 740, -29.01, 6.28, 0.1951, 0.0667),
        (13, 860, -28.01, -2.24, 0.3781, 0.1667),
        # A short sharp sound can have level + SNR but still lack dense speech.
        (3, 980, -26.39, 9.09, 0.3550, 0.3810),
    ],
)
async def test_signal_gate_rejects_redacted_ambient_false_turn_corpus(
    characters: int,
    duration_ms: int,
    signal_rms: float,
    snr: float,
    vad_mean: float,
    vad_ratio: float,
) -> None:
    calls = 0

    async def complete_json(_: object, __: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return gate_payload()

    gate = StructuredConversationGate(complete_json)
    decision = await gate.evaluate(
        Transcript(
            "x" * characters,
            "vi-VN",
            context=(ConversationMessage("assistant", "Tôi vẫn đang nghe."),),
            input_evidence=InputEvidence(
                source=InputSource.DEVICE_MIC,
                wake_source=WakeSource.WAKE_WORD,
                utterance_duration_ms=duration_ms,
                signal_rms_dbfs=signal_rms,
                estimated_snr_db=snr,
                vad_mean_probability=vad_mean,
                vad_speech_ratio=vad_ratio,
            ),
        ),
        context(),
    )

    assert decision.disposition is AdmissionDisposition.NON_ACTIONABLE
    assert decision.reason_code == "low_quality"
    assert calls == 0


@pytest.mark.parametrize(
    ("signal_rms", "snr", "vad_mean", "vad_ratio"),
    [
        (-24.75, 15.26, 0.7505, 0.8125),
        # Loud near-field speech remains usable even when room noise lowers SNR.
        (-18.06, 2.42, 0.6422, 0.6538),
        # A short turn can pass through level + SNR even with sparse VAD frames.
        (-20.00, 12.00, 0.4500, 0.4000),
        # Quiet speech can pass through clean SNR + dense VAD support.
        (-34.00, 12.00, 0.7000, 0.7500),
    ],
)
async def test_signal_gate_keeps_two_independent_speech_supports(
    signal_rms: float,
    snr: float,
    vad_mean: float,
    vad_ratio: float,
) -> None:
    calls = 0

    async def complete_json(_: object, __: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return gate_payload()

    gate = StructuredConversationGate(complete_json)
    decision = await gate.evaluate(
        Transcript(
            "Ừ, kể tiếp đi",
            "vi-VN",
            context=(ConversationMessage("assistant", "Tôi đang kể câu chuyện."),),
            input_evidence=InputEvidence(
                source=InputSource.DEVICE_MIC,
                wake_source=WakeSource.WAKE_WORD,
                utterance_duration_ms=1_200,
                signal_rms_dbfs=signal_rms,
                estimated_snr_db=snr,
                vad_mean_probability=vad_mean,
                vad_speech_ratio=vad_ratio,
            ),
        ),
        context(),
    )

    assert decision.disposition is AdmissionDisposition.ACCEPTED
    assert calls == 1


async def test_signal_gate_keeps_clear_one_word_contextual_reply() -> None:
    calls = 0

    async def complete_json(_: object, __: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return gate_payload()

    gate = StructuredConversationGate(complete_json)
    decision = await gate.evaluate(
        Transcript(
            "Ừ",
            "vi-VN",
            context=(ConversationMessage("assistant", "Bạn muốn tôi kể tiếp chứ?"),),
            input_evidence=InputEvidence(
                source=InputSource.DEVICE_MIC,
                wake_source=WakeSource.WAKE_WORD,
                utterance_duration_ms=700,
                signal_rms_dbfs=-20.0,
                estimated_snr_db=12.0,
                vad_mean_probability=0.7,
                vad_speech_ratio=0.7,
            ),
        ),
        context(),
    )

    assert decision.disposition is AdmissionDisposition.ACCEPTED
    assert calls == 1


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


async def test_planner_normalizes_response_required_for_executable_actions() -> None:
    async def complete_json(_: object, __: object) -> dict[str, object]:
        return {
            "action": "call_tool_then_respond",
            "dialogue_act": "command",
            "locale": "vi-VN",
            "intent": "dynamic.tool",
            "response_required": False,
            "tool_call": {
                "name": "self.audio_speaker.set_volume",
                "arguments": {"volume": 55},
            },
        }

    planner = JsonPlannerProvider(complete_json)
    plan = await planner.plan(
        Transcript("Hãy chỉnh âm lượng", "vi-VN"),
        AdmissionDecision(AdmissionDisposition.ACCEPTED, 1.0, "test"),
        context(),
    )
    assert plan.action is PlanAction.CALL_TOOL_THEN_RESPOND
    assert plan.response_required is True


async def test_structured_gate_receives_untrusted_memory_and_parses_fact_candidates() -> None:
    captured: dict[str, object] = {}

    async def complete_json(payload: dict[str, object], _: object) -> dict[str, object]:
        captured.update(payload)
        value = gate_payload()
        plan = value["plan"]
        assert isinstance(plan, dict)
        plan["memory_facts"] = [
            {
                "category": "preference",
                "key": "drink",
                "value": "cà phê",
                "confidence": 0.9,
                "expires_in_days": 30,
            }
        ]
        return value

    gate = StructuredConversationGate(complete_json)
    operation = context()
    transcript = Transcript(
        "Tôi thích cà phê",
        "vi-VN",
        cross_session_memory=MemorySnapshot(
            messages=(
                StoredMemoryMessage(
                    "assistant", "Dữ liệu cũ", "2026-07-01T00:00:00Z"
                ),
            )
        ),
    )

    decision = await gate.evaluate(transcript, operation)
    plan = await gate.plan(transcript, decision, operation)

    memory = captured["untrusted_cross_session_memory"]
    assert isinstance(memory, dict)
    assert memory["boundary"] == "untrusted_cross_session_memory"
    assert plan.memory_facts[0].key == "drink"
    assert plan.memory_facts[0].value == "cà phê"


async def test_structured_gate_rejects_invalid_signal_without_model_call() -> None:
    calls = 0

    async def complete_json(_: object, __: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return gate_payload()

    gate = StructuredConversationGate(complete_json)
    decision = await gate.evaluate(Transcript("...", "vi-VN"), context())

    assert decision.disposition is AdmissionDisposition.NON_ACTIONABLE
    assert calls == 0


async def test_structured_gate_rejection_does_not_expose_a_plan() -> None:
    async def complete_json(_: object, __: object) -> dict[str, object]:
        return gate_payload(decision="not_addressed", reason_code="not_addressed")

    gate = StructuredConversationGate(complete_json)
    operation = context()
    transcript = Transcript("Âm thanh từ phòng bên", "vi-VN")
    decision = await gate.evaluate(transcript, operation)

    assert decision.disposition is AdmissionDisposition.NOT_ADDRESSED
    with pytest.raises(ValueError, match="unavailable"):
        await gate.plan(transcript, decision, operation)


async def test_structured_gate_caches_plan_for_exact_turn_and_generation() -> None:
    async def complete_json(_: object, __: object) -> dict[str, object]:
        return gate_payload()

    gate = StructuredConversationGate(complete_json)
    operation = context()
    transcript = Transcript("Veetee giúp tôi", "vi-VN")
    decision = await gate.evaluate(transcript, operation)
    plan = await gate.plan(transcript, decision, operation)

    assert decision.disposition is AdmissionDisposition.ACCEPTED
    assert plan.action is PlanAction.RESPOND
    assert plan.response_text == "Tôi nghe đây."
    with pytest.raises(ValueError, match="unavailable"):
        await gate.plan(transcript, decision, operation)


async def test_structured_gate_passes_recent_context_to_semantic_model() -> None:
    payloads: list[object] = []

    async def complete_json(payload: object, __: object) -> dict[str, object]:
        payloads.append(payload)
        return gate_payload()

    gate = StructuredConversationGate(complete_json)
    transcript = Transcript(
        "Gke vậy sao?",
        "vi-VN",
        confidence=1.0,
        stability=1.0,
        context=(
            ConversationMessage("user", "Bạn vừa nói gì?"),
            ConversationMessage("assistant", "Tôi vừa nói một câu đùa."),
        ),
    )
    await gate.evaluate(transcript, context())

    assert payloads[0]["conversation_context"] == [  # type: ignore[index]
        {"role": "user", "text": "Bạn vừa nói gì?"},
        {"role": "assistant", "text": "Tôi vừa nói một câu đùa."},
    ]
    assert payloads[0]["input_evidence"]["wake_source"] is None  # type: ignore[index]


@pytest.mark.parametrize(
    ("turn_id", "generation"),
    [("another-turn", 1), ("turn", 2)],
)
async def test_structured_gate_never_reuses_plan_across_contexts(
    turn_id: str, generation: int
) -> None:
    async def complete_json(_: object, __: object) -> dict[str, object]:
        return gate_payload()

    gate = StructuredConversationGate(complete_json)
    transcript = Transcript("Veetee giúp tôi", "vi-VN")
    decision = await gate.evaluate(transcript, context())

    with pytest.raises(ValueError, match="unavailable"):
        await gate.plan(
            transcript,
            decision,
            context(turn_id=turn_id, generation=generation),
        )


async def test_structured_gate_normalizes_end_and_interrupt_actions() -> None:
    payloads = [
        gate_payload(dialogue_act="end"),
        gate_payload(dialogue_act="interrupt"),
    ]

    async def complete_json(_: object, __: object) -> dict[str, object]:
        return payloads.pop(0)

    gate = StructuredConversationGate(complete_json)
    transcript = Transcript("fixture", "vi-VN")
    end_context = context(turn_id="end")
    end_decision = await gate.evaluate(transcript, end_context)
    end_plan = await gate.plan(transcript, end_decision, end_context)
    interrupt_decision = await gate.evaluate(transcript, context(turn_id="interrupt", generation=2))

    assert end_decision.disposition is AdmissionDisposition.END
    assert end_plan.action is PlanAction.END_SESSION
    assert end_plan.dialogue_act is DialogueAct.END
    assert interrupt_decision.disposition is AdmissionDisposition.INTERRUPT


async def test_structured_gate_bounds_unknown_reason_code() -> None:
    async def complete_json(_: object, __: object) -> dict[str, object]:
        return gate_payload(reason_code="invented_reason")

    gate = StructuredConversationGate(complete_json)
    decision = await gate.evaluate(Transcript("fixture", "vi-VN"), context())

    assert decision.reason_code == "invalid_model_output"


async def test_structured_gate_normalizes_accepted_low_quality_reason() -> None:
    async def complete_json(_: object, __: object) -> dict[str, object]:
        return gate_payload(
            decision="accepted",
            action="ask_clarification",
            reason_code="low_quality",
        )

    gate = StructuredConversationGate(complete_json)
    operation = context()
    transcript = Transcript(
        "fixture",
        "vi-VN",
        context=(ConversationMessage("assistant", "Tôi vẫn đang nghe."),),
        input_evidence=InputEvidence(
            source=InputSource.DEVICE_MIC,
            wake_source=WakeSource.WAKE_WORD,
        ),
    )
    decision = await gate.evaluate(transcript, operation)
    plan = await gate.plan(transcript, decision, operation)

    assert decision.disposition is AdmissionDisposition.ACCEPTED
    assert decision.reason_code == "speech_relevant"
    assert plan.action is PlanAction.ASK_CLARIFICATION


async def test_accepted_noop_is_streamed_as_a_natural_turn() -> None:
    async def complete_json(_: object, __: object) -> dict[str, object]:
        return gate_payload(
            action="noop",
            dialogue_act="social",
        )

    gate = StructuredConversationGate(complete_json)
    operation = context()
    transcript = Transcript("Đù", "vi-VN")
    decision = await gate.evaluate(transcript, operation)
    plan = await gate.plan(transcript, decision, operation)

    assert decision.disposition is AdmissionDisposition.ACCEPTED
    assert plan.action is PlanAction.RESPOND
    assert plan.response_required is True
    assert plan.response_text is None


@pytest.mark.parametrize(
    "text",
    [
        "Đấm nhau không?",
        "Đù",
        "Ghê vậy sao?",
        "Ủa?",
        "Kể tiếp đi",
        "Thế à?",
    ],
)
async def test_explicit_conversational_text_survives_semantic_false_rejection(
    text: str,
) -> None:
    async def complete_json(_: object, __: object) -> dict[str, object]:
        return gate_payload(
            decision="not_addressed",
            action="noop",
            dialogue_act="social",
            reason_code="not_addressed",
        )

    gate = StructuredConversationGate(complete_json)
    operation = context()
    transcript = Transcript(
        text,
        "vi-VN",
        confidence=1.0,
        stability=1.0,
        input_evidence=InputEvidence(
            source=InputSource.TYPED_TEXT,
            wake_source=WakeSource.BUTTON,
        ),
    )
    decision = await gate.evaluate(transcript, operation)
    plan = await gate.plan(transcript, decision, operation)

    assert decision.disposition is AdmissionDisposition.ACCEPTED
    assert decision.reason_code == "speech_relevant"
    assert plan.action is PlanAction.RESPOND
    assert plan.response_required is True
    assert plan.response_text is None


async def test_active_voice_context_recovers_uncertain_social_follow_up() -> None:
    async def complete_json(_: object, __: object) -> dict[str, object]:
        return gate_payload(
            decision="unclear",
            action="noop",
            dialogue_act="social",
            reason_code="unclear",
        )

    gate = StructuredConversationGate(complete_json)
    operation = context()
    transcript = Transcript(
        "Ghê vậy sao?",
        "vi-VN",
        context=(ConversationMessage("assistant", "Tôi vừa kể một chuyện bất ngờ."),),
        input_evidence=InputEvidence(
            source=InputSource.DEVICE_MIC,
            wake_source=WakeSource.WAKE_WORD,
        ),
    )
    decision = await gate.evaluate(transcript, operation)
    plan = await gate.plan(transcript, decision, operation)

    assert decision.disposition is AdmissionDisposition.ACCEPTED
    assert plan.action is PlanAction.RESPOND


async def test_active_voice_context_keeps_concrete_signal_rejection() -> None:
    async def complete_json(_: object, __: object) -> dict[str, object]:
        return gate_payload(
            decision="non_actionable",
            action="noop",
            reason_code="self_echo",
        )

    gate = StructuredConversationGate(complete_json)
    operation = context()
    transcript = Transcript(
        "fixture",
        "vi-VN",
        context=(ConversationMessage("assistant", "Tôi đang nói."),),
        input_evidence=InputEvidence(
            source=InputSource.DEVICE_MIC,
            wake_source=WakeSource.BUTTON,
            aec_enabled=True,
            self_echo_probability=0.96,
        ),
    )
    decision = await gate.evaluate(transcript, operation)

    assert decision.disposition is AdmissionDisposition.NON_ACTIONABLE
    with pytest.raises(ValueError, match="unavailable"):
        await gate.plan(transcript, decision, operation)


async def test_active_voice_context_keeps_explicit_speaker_mismatch() -> None:
    async def complete_json(_: object, __: object) -> dict[str, object]:
        return gate_payload(
            decision="not_addressed",
            action="noop",
            reason_code="not_addressed",
        )

    gate = StructuredConversationGate(complete_json)
    transcript = Transcript(
        "fixture",
        "vi-VN",
        context=(ConversationMessage("assistant", "Tôi đang nghe."),),
        input_evidence=InputEvidence(
            source=InputSource.DEVICE_MIC,
            wake_source=WakeSource.BUTTON,
            target_speaker_probability=0.05,
        ),
    )
    decision = await gate.evaluate(transcript, context())

    assert decision.disposition is AdmissionDisposition.NOT_ADDRESSED


async def test_cancelled_structured_gate_does_not_leave_cached_plan() -> None:
    token = CancellationToken()

    async def complete_json(_: object, __: object) -> dict[str, object]:
        token.cancel("fixture_cancel")
        return gate_payload()

    gate = StructuredConversationGate(complete_json)
    operation = context(token=token)
    transcript = Transcript("fixture", "vi-VN")
    with pytest.raises(TurnCancelledError):
        await gate.evaluate(transcript, operation)
    with pytest.raises(TurnCancelledError):
        await gate.plan(
            transcript,
            AdmissionDecision(AdmissionDisposition.ACCEPTED, 1.0, "fixture"),
            operation,
        )


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
