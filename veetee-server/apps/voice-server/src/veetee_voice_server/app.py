from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any, cast
from uuid import uuid4

import httpx
import structlog
from fastapi import FastAPI, Header, HTTPException, Request, Response, WebSocket, status
from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field

from veetee_voice_server.config import Settings, get_settings
from veetee_voice_server.conversation.arbiter import TurnArbiter
from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
    OperationDeadlineExceededError,
)
from veetee_voice_server.conversation.engine import ConversationEngine
from veetee_voice_server.logging import configure_logging
from veetee_voice_server.manager import (
    DeviceContext,
    ManagerAuthenticationError,
    ManagerClient,
    SessionProfile,
)
from veetee_voice_server.providers.contracts import ToolBroker, TtsProvider
from veetee_voice_server.providers.failover import (
    FailoverLlmProvider,
    LlmProviderCandidate,
    ProviderChainUnavailableError,
)
from veetee_voice_server.providers.llm_factory import create_llm_provider
from veetee_voice_server.providers.local_asr import SherpaZipformerAsrProvider
from veetee_voice_server.providers.local_tts import VieNeuTtsProvider
from veetee_voice_server.providers.nine_router import NineRouterProviderError
from veetee_voice_server.providers.semantic import StructuredConversationGate
from veetee_voice_server.providers.silero_vad import SileroVadModel
from veetee_voice_server.readiness import ComponentHealth, ReadinessRegistry
from veetee_voice_server.telemetry import ConversationTelemetryBuffer
from veetee_voice_server.tools.context import with_session_context_tools
from veetee_voice_server.transport.lab import (
    EmptyLabToolBroker,
    LabSession,
    SelectedDeviceLabToolBroker,
    SimulatedLabToolBroker,
)
from veetee_voice_server.transport.mcp import DeviceMcpError
from veetee_voice_server.transport.session import VoiceSession
from veetee_voice_server.transport.session_registry import (
    DeviceSessionRegistry,
    DeviceSessionUnavailableError,
)
from veetee_voice_server.transport.sink import ConversationSink

logger = structlog.get_logger(__name__)


def _published_agent_context(profile: SessionProfile) -> dict[str, object]:
    """Expose only bounded, non-secret agent/device config to the model."""

    return {
        "agent_id": profile.agent_id,
        "config_version": profile.config_version,
        "agent_name": profile.agent_name,
        "locale": profile.locale,
        "interaction_mode": profile.interaction_mode,
        "device_locale": profile.device_locale,
        "device_time_zone": profile.device_time_zone,
        "device_time_zone_offset_minutes": profile.device_time_zone_offset_minutes,
        "conversation_policy": {
            "first_input_seconds": profile.policy.first_input_seconds,
            "between_turns_seconds": profile.policy.between_turns_seconds,
            "closing_grace_seconds": profile.policy.closing_grace_seconds,
            "max_session_seconds": profile.policy.max_session_seconds,
            "total_turn_seconds": profile.policy.total_turn_seconds,
            "context_message_limit": profile.policy.context_message_limit,
        },
    }


class HealthResponse(BaseModel):
    status: str
    service: str


class ReadyResponse(BaseModel):
    status: str
    components: list[dict[str, object]]


class ManagerMcpCallRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False
    timeout_seconds: float = Field(default=10.0, ge=0.5, le=30.0)


def _planner_system_prompt(
    profile: SessionProfile,
    tools: ToolBroker,
    *,
    tool_catalog: list[dict[str, Any]] | None = None,
) -> str:
    tool_catalog = tools.list_tools() if tool_catalog is None else tool_catalog
    catalog = json.dumps(tool_catalog, ensure_ascii=False, separators=(",", ":"))
    streams_prose = bool(
        profile.llm_chain
        and profile.llm_chain[0].config.get("streamProseResponse") is True
    )
    response_text_instruction = (
        "For accepted respond or ask_clarification plans, response_text must be null; "
        "the runtime starts a separate prose stream after admission so it can feed TTS "
        "incrementally. Tool actions also require response_text null. "
        if streams_prose
        else (
            "For a complete short answer, put directly speakable text in response_text. "
            "For an answer needing more detail, set response_text null so the runtime can "
            "stream the full natural response. Tool actions require response_text null. "
        )
    )
    prompt_tool_names = [
        {"name": item["name"], "description": ""}
        for item in tool_catalog
        if isinstance(item.get("name"), str)
    ]
    agent_context = json.dumps(
        _published_agent_context(profile), ensure_ascii=False, separators=(",", ":")
    )
    published_prompt = (
        ""
        if streams_prose
        else f"\n\nPublished agent prompt:\n{profile.render_system_prompt(prompt_tool_names)}"
    )
    return (
        "Return exactly one JSON object with admission, dialogue_act and plan. "
        "admission.decision: accepted|non_actionable|not_addressed|unclear|interrupt|end. "
        "admission must also include numeric confidence and addressed_to_robot in [0,1], "
        "plus reason_code from speech_relevant|non_speech|low_quality|not_addressed|"
        "self_echo|duplicate|low_confidence|semantic_interrupt|conversation_end|unclear|"
        "invalid_model_output. plan.action: respond|call_tool_then_respond|"
        "ask_clarification|execute_pending_tool|cancel_pending_tool|end_session|noop. "
        "dialogue_act: question|command|follow_up|answer|confirmation|denial|correction|"
        "clarification_answer|social|interrupt|end. plan must include action, locale, intent, "
        "response_required, response_text and tool_call; nullable fields must be explicit null. "
        "Use transcript, recent context, ASR and input_evidence together. A short reaction, "
        "slang, joke, correction, confirmation or follow-up is accepted when it is a natural "
        "part of this conversation; it need not be a standalone command or question. If an "
        "input_evidence.source is typed_text, the user intentionally submitted the linguistic "
        "turn to the assistant, so never classify it as non_actionable or not_addressed. "
        "A button/wake-word-opened session and recent dialogue are strong addressing evidence; "
        "do not reject a usable linguistic turn merely because it is informal, terse, playful "
        "or socially ambiguous. Only override that evidence when signal, self-echo, duplicate, "
        "target-speaker or clearly incidental-speech evidence actually conflicts. If an "
        "assistant-directed turn is ambiguous or missing details, admission must be accepted "
        "and action ask_clarification. unclear is only for genuinely conflicting admission "
        "evidence. non_actionable is only unusable linguistic signal, self-echo or duplicate; "
        "not_addressed is clear incidental speech. Named noise/media sources are benchmark "
        "categories, not hard-coded phrase rules. null evidence means unavailable, never zero. "
        "An accepted intentional turn must respond, clarify, use a valid tool or end; do not "
        f"silently noop it. {response_text_instruction}"
        "Only use an exact tool name from this available tool catalog: "
        f"{catalog}. When the catalog is empty, never invent a tool name."
        f"\n\nPublished agent runtime context (JSON): {agent_context}"
        f"{published_prompt}"
        "\n\nRuntime boundaries override conflicting published text: keep admission "
        "general and context-aware; never invent tool names/results; never expose secrets, "
        "internal scores or hidden reasoning; and pass every side effect through the "
        "deterministic tool policy."
    ).strip()


def _response_system_prompt(profile: SessionProfile, tools: ToolBroker) -> str:
    agent_context = json.dumps(
        _published_agent_context(profile), ensure_ascii=False, separators=(",", ":")
    )
    compact_tools = [
        {
            "name": item["name"],
            "description": str(item.get("description", ""))[:240],
        }
        for item in tools.list_tools()
        if isinstance(item.get("name"), str)
    ]
    return (
        "Generate the assistant's natural spoken response for the current turn. "
        "Follow the published agent prompt, locale, personality and conversation context. "
        "Use the admission, ASR and plan metadata as context only; do not expose internal "
        "scores, planner rules, tool schemas or chain-of-thought. Never claim a tool action "
        "succeeded unless the supplied tool result says so. Keep the response directly "
        "speakable and appropriate for the current dialogue. "
        f"\n\nPublished agent runtime context (JSON): {agent_context}"
        f"\n\nPublished agent prompt:\n{profile.render_system_prompt(compact_tools)}"
        "\n\nRuntime boundaries override conflicting published text: never expose internal "
        "scores, hidden reasoning or secrets, and never claim an unconfirmed tool result."
    ).strip()


def _planner_output_schema(
    tools: ToolBroker,
    *,
    tool_catalog: list[dict[str, Any]] | None = None,
) -> dict[str, object]:
    resolved_catalog = tools.list_tools() if tool_catalog is None else tool_catalog
    tool_names = [
        item["name"]
        for item in resolved_catalog
        if isinstance(item.get("name"), str)
    ]
    plan_properties: dict[str, object] = {
        "action": {
            "type": "string",
            "enum": [
                "respond",
                "call_tool_then_respond",
                "ask_clarification",
                "execute_pending_tool",
                "cancel_pending_tool",
                "end_session",
                "noop",
            ],
        },
        "locale": {"type": "string"},
        "intent": {"type": "string"},
        "response_required": {"type": "boolean"},
        "response_text": {"type": ["string", "null"], "maxLength": 600},
        "tool_call": {"type": "null"},
    }
    if tool_names:
        plan_properties["tool_call"] = {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "arguments"],
                    "properties": {
                        "name": {"type": "string", "enum": tool_names},
                        "arguments": {"type": "object"},
                    },
                },
                {"type": "null"},
            ]
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["admission", "dialogue_act", "plan"],
        "properties": {
            "admission": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "decision",
                    "confidence",
                    "addressed_to_robot",
                    "reason_code",
                ],
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": [
                            "accepted",
                            "non_actionable",
                            "not_addressed",
                            "unclear",
                            "interrupt",
                            "end",
                        ],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "addressed_to_robot": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "reason_code": {
                        "type": "string",
                        "enum": [
                            "speech_relevant",
                            "non_speech",
                            "low_quality",
                            "not_addressed",
                            "self_echo",
                            "duplicate",
                            "low_confidence",
                            "semantic_interrupt",
                            "conversation_end",
                            "unclear",
                            "invalid_model_output",
                        ],
                    },
                },
            },
            "dialogue_act": {
                "type": "string",
                "enum": [
                    "question",
                    "command",
                    "follow_up",
                    "answer",
                    "confirmation",
                    "denial",
                    "correction",
                    "clarification_answer",
                    "social",
                    "interrupt",
                    "end",
                ],
            },
            "plan": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "action",
                    "locale",
                    "intent",
                    "response_required",
                    "response_text",
                    "tool_call",
                ],
                "properties": plan_properties,
            },
        },
    }


def _validated_planner_output(
    value: dict[str, Any],
    schema: dict[str, object],
    locale: str,
    *,
    stream_response: bool = False,
) -> dict[str, Any]:
    admission = value.get("admission")
    if not isinstance(admission, dict):
        return _degraded_conversation_gate(locale)
    decision = admission.get("decision")
    valid_decisions = {
        "accepted",
        "non_actionable",
        "not_addressed",
        "unclear",
        "interrupt",
        "end",
    }
    if decision not in valid_decisions:
        return _degraded_conversation_gate(locale)
    valid_reason_codes = {
        "speech_relevant",
        "non_speech",
        "low_quality",
        "not_addressed",
        "self_echo",
        "duplicate",
        "low_confidence",
        "semantic_interrupt",
        "conversation_end",
        "unclear",
        "invalid_model_output",
    }
    reason_code = admission.get("reason_code")
    normalized_admission = {
        "decision": decision,
        "confidence": _bounded_model_score(
            admission.get("confidence"),
            fallback=0.5 if decision == "accepted" else 0.0,
        ),
        "addressed_to_robot": _bounded_model_score(
            admission.get("addressed_to_robot"),
            fallback=0.5 if decision == "accepted" else 0.0,
        ),
        "reason_code": (
            reason_code if reason_code in valid_reason_codes else "invalid_model_output"
        ),
    }
    plan = value.get("plan")
    plan = plan if isinstance(plan, dict) else {}
    valid_dialogue_acts = {
        "question",
        "command",
        "follow_up",
        "answer",
        "confirmation",
        "denial",
        "correction",
        "clarification_answer",
        "social",
        "interrupt",
        "end",
    }
    dialogue_act = value.get("dialogue_act")
    if dialogue_act not in valid_dialogue_acts:
        dialogue_act = (
            "end" if decision == "end" else "interrupt" if decision == "interrupt" else "answer"
        )
    valid_actions = {
        "respond",
        "call_tool_then_respond",
        "ask_clarification",
        "execute_pending_tool",
        "cancel_pending_tool",
        "end_session",
        "noop",
    }
    action = plan.get("action")
    tool_call = plan.get("tool_call")
    if action not in valid_actions:
        if decision == "end" or dialogue_act == "end":
            action = "end_session"
        elif decision in {
            "non_actionable",
            "not_addressed",
            "unclear",
            "interrupt",
        }:
            action = "noop"
        elif action is None and isinstance(tool_call, dict):
            action = "call_tool_then_respond"
        else:
            action = "respond"
            tool_call = None
    if action in {"call_tool_then_respond", "execute_pending_tool"} and not isinstance(
        tool_call, dict
    ):
        action = "respond"
        tool_call = None
    if action in {"call_tool_then_respond", "execute_pending_tool"}:
        root_properties = schema.get("properties")
        plan_schema = (
            root_properties.get("plan") if isinstance(root_properties, dict) else None
        )
        plan_properties = (
            plan_schema.get("properties") if isinstance(plan_schema, dict) else None
        )
        tool_call_schema = (
            plan_properties.get("tool_call") if isinstance(plan_properties, dict) else None
        )
        tool_call_error = (
            next(Draft202012Validator(tool_call_schema).iter_errors(tool_call), None)
            if isinstance(tool_call_schema, dict)
            else ValueError("missing tool_call schema")
        )
        if tool_call_error is not None:
            action = "respond"
            tool_call = None
    else:
        # JSON Object Mode models sometimes emit an empty placeholder object
        # even for a regular response. It must not invalidate an otherwise
        # accepted linguistic turn or leak into the tool policy.
        tool_call = None
    if decision not in {"accepted", "end"}:
        action = "noop"
        tool_call = None
    elif decision == "end":
        action = "end_session"
        tool_call = None
        dialogue_act = "end"
    response_text = plan.get("response_text")
    if not isinstance(response_text, str) or len(response_text) > 600:
        response_text = None
    if stream_response and decision == "accepted" and action in {
        "respond",
        "ask_clarification",
    }:
        response_text = None
    response_required = plan.get("response_required")
    if not isinstance(response_required, bool):
        response_required = action not in {"noop", "cancel_pending_tool"}
    if action in {
        "respond",
        "call_tool_then_respond",
        "execute_pending_tool",
        "ask_clarification",
    }:
        response_required = True
    elif action in {"noop", "cancel_pending_tool"}:
        response_required = False
    normalized = {
        "admission": normalized_admission,
        "dialogue_act": dialogue_act,
        "plan": {
            "action": action,
            "locale": (
                plan.get("locale")
                if isinstance(plan.get("locale"), str) and plan.get("locale")
                else locale
            ),
            "intent": plan.get("intent") if isinstance(plan.get("intent"), str) else "",
            "response_required": response_required,
            "response_text": response_text,
            "tool_call": tool_call if isinstance(tool_call, dict) else None,
        },
    }
    validation_error = next(Draft202012Validator(schema).iter_errors(normalized), None)
    if validation_error is None:
        return normalized
    logger.warning(
        "conversation_gate_schema_rejected",
        validator=validation_error.validator,
        path=".".join(str(part) for part in validation_error.path),
        fallback="respond_without_tools",
    )
    return _degraded_conversation_gate(locale)


def _bounded_model_score(value: object, *, fallback: float) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return min(max(float(value), 0.0), 1.0)
    if isinstance(value, str):
        try:
            return min(max(float(value), 0.0), 1.0)
        except ValueError:
            pass
    return fallback


def _degraded_conversation_gate(locale: str) -> dict[str, Any]:
    """Keep a linguistic turn conversational while disabling all tool execution."""

    return {
        "admission": {
            "decision": "accepted",
            "confidence": 0.5,
            "addressed_to_robot": 0.5,
            "reason_code": "invalid_model_output",
        },
        "dialogue_act": "answer",
        "plan": {
            "action": "respond",
            "locale": locale,
            "intent": "",
            "response_required": True,
            "response_text": None,
            "tool_call": None,
        },
    }


class _ConversationGateArtifacts:
    """Cache prompt/schema while refreshing after asynchronous MCP discovery."""

    def __init__(self, profile: SessionProfile, tools: ToolBroker) -> None:
        self._profile = profile
        self._tools = tools
        self._fingerprint: str | None = None
        self._schema: dict[str, object] | None = None
        self._system_prompt: str | None = None
        self._schema_chars = 0
        self._tool_count = 0

    def resolve(self) -> tuple[dict[str, object], str, int, int]:
        catalog = self._tools.list_tools()
        fingerprint = json.dumps(
            catalog,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if fingerprint != self._fingerprint:
            self._schema = _planner_output_schema(
                self._tools,
                tool_catalog=catalog,
            )
            self._system_prompt = _planner_system_prompt(
                self._profile,
                self._tools,
                tool_catalog=catalog,
            )
            self._schema_chars = len(
                json.dumps(self._schema, separators=(",", ":"))
            )
            self._tool_count = len(catalog)
            self._fingerprint = fingerprint
        assert self._schema is not None
        assert self._system_prompt is not None
        return (
            self._schema,
            self._system_prompt,
            self._schema_chars,
            self._tool_count,
        )


async def _complete_conversation_gate_json(
    llm: FailoverLlmProvider,
    profile: SessionProfile,
    tools: ToolBroker,
    payload: dict[str, object],
    context: OperationContext,
    *,
    schema: dict[str, object] | None = None,
    system_prompt: str | None = None,
    schema_chars: int | None = None,
    tool_count: int | None = None,
) -> dict[str, Any]:
    resolved_schema = schema if schema is not None else _planner_output_schema(tools)
    resolved_system_prompt = (
        system_prompt
        if system_prompt is not None
        else _planner_system_prompt(profile, tools)
    )
    resolved_schema_chars = (
        schema_chars
        if schema_chars is not None
        else len(json.dumps(resolved_schema, separators=(",", ":")))
    )
    resolved_tool_count = tool_count if tool_count is not None else len(tools.list_tools())
    user_prompt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    conversation_context = payload.get("conversation_context")
    context_message_count = (
        len(conversation_context) if isinstance(conversation_context, list) else 0
    )
    started_at = monotonic()
    logger.info(
        "conversation_gate_request",
        turn_id=context.turn_id,
        context_messages=context_message_count,
        system_prompt_chars=len(resolved_system_prompt),
        user_prompt_chars=len(user_prompt),
        schema_chars=resolved_schema_chars,
        tool_count=resolved_tool_count,
    )
    try:
        value = await llm.complete_json(
            system_prompt=resolved_system_prompt,
            user_prompt=user_prompt,
            context=context,
            schema=resolved_schema,
            schema_name="veetee_conversation_gate",
            schema_transport="json_schema",
            max_output_tokens=512,
            validate_schema=False,
        )
    except (NineRouterProviderError, ProviderChainUnavailableError, httpx.HTTPError) as error:
        logger.warning(
            "conversation_gate_provider_failed",
            turn_id=context.turn_id,
            error=type(error).__name__,
            provider_code=getattr(error, "code", None),
            status_code=getattr(error, "status_code", None),
            retryable=getattr(error, "retryable", None),
            finish_reason=getattr(error, "finish_reason", None),
            output_characters=getattr(error, "output_characters", None),
            schema_validator=getattr(error, "schema_validator", None),
            schema_path=getattr(error, "schema_path", None),
            fallback="respond_without_tools",
        )
        degraded = _degraded_conversation_gate(profile.locale)
        degraded["_runtime_error_code"] = "semantic_provider_unavailable"
        return degraded
    logger.info(
        "conversation_gate_response",
        turn_id=context.turn_id,
        duration_ms=round((monotonic() - started_at) * 1_000, 1),
    )
    stream_response = bool(
        profile.llm_chain
        and profile.llm_chain[0].config.get("streamProseResponse") is True
    )
    return _validated_planner_output(
        value,
        resolved_schema,
        profile.locale,
        stream_response=stream_response,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    readiness = ReadinessRegistry()
    runtime: dict[str, object] = {}
    llm_registry: dict[tuple[str, str, str, str, str, str], LlmProviderCandidate] = {}
    llm_chain_registry: dict[
        tuple[tuple[str, str, str, str, str, str], ...], FailoverLlmProvider
    ] = {}
    tts_registry: dict[
        tuple[str, str, str, str, str, float, float, float], TtsProvider
    ] = {}
    device_sessions = DeviceSessionRegistry()
    lab_capacity_lock = asyncio.Lock()
    active_lab_sessions = 0

    def llm_for_profile(profile: SessionProfile) -> FailoverLlmProvider:
        keys: list[tuple[str, str, str, str, str, str]] = []
        candidates: list[LlmProviderCandidate] = []
        for endpoint in profile.llm_chain:
            secret_fingerprint = hashlib.sha256(endpoint.api_key.encode()).hexdigest()[:16]
            key = (
                endpoint.provider_id,
                endpoint.base_url,
                endpoint.model,
                endpoint.reasoning_effort,
                secret_fingerprint,
                json.dumps(endpoint.config, sort_keys=True, ensure_ascii=False),
            )
            candidate = llm_registry.get(key)
            if candidate is None:
                provider = create_llm_provider(
                    adapter=endpoint.adapter,
                    base_url=endpoint.base_url,
                    model=endpoint.model,
                    api_key=endpoint.api_key,
                    reasoning_effort=endpoint.reasoning_effort,
                    config=endpoint.config,
                )
                candidate = LlmProviderCandidate(endpoint.provider_id, provider)
                llm_registry[key] = candidate
            keys.append(key)
            candidates.append(candidate)
        chain_key = tuple(keys)
        chain = llm_chain_registry.get(chain_key)
        if chain is None:
            chain = FailoverLlmProvider(tuple(candidates))
            llm_chain_registry[chain_key] = chain
        return chain

    def tts_for_profile(profile: SessionProfile) -> TtsProvider:
        endpoint = profile.tts_endpoint
        voice = profile.voice
        default_tts = runtime.get("tts")
        if endpoint is None or voice is None:
            if default_tts is None:
                raise RuntimeError("voice runtime is not ready")
            return cast(TtsProvider, default_tts)
        config = dict(endpoint.config)
        config.update(
            voice=voice.voice_id,
            style=voice.style,
            rate=voice.rate,
            pitchHz=voice.pitch_hz,
            volume=voice.volume,
        )
        key = (
            endpoint.provider_id,
            endpoint.adapter,
            voice.voice_id,
            voice.style,
            json.dumps(config, sort_keys=True, ensure_ascii=False),
            voice.rate,
            voice.pitch_hz,
            voice.volume,
        )
        provider = tts_registry.get(key)
        if provider is not None:
            return provider
        if endpoint.adapter.lower() == "vieneu-local" and isinstance(
            default_tts, VieNeuTtsProvider
        ):
            provider = default_tts.with_profile(
                voice=voice.voice_id,
                style=voice.style,
                speed=voice.rate,
                pitch_hz=voice.pitch_hz,
                volume=voice.volume,
            )
        else:
            raise RuntimeError(f"Unsupported TTS adapter: {endpoint.adapter}")
        tts_registry[key] = provider
        return provider

    def engine_factory(
        arbiter: TurnArbiter,
        sink: ConversationSink,
        profile: SessionProfile,
        tool_broker: ToolBroker,
    ) -> ConversationEngine:
        tools = with_session_context_tools(profile, tool_broker)
        asr_llm = llm_for_profile(profile)
        asr_tts = tts_for_profile(profile)
        gate_artifacts = _ConversationGateArtifacts(profile, tools)

        async def gate_json(
            payload: dict[str, object], context: OperationContext
        ) -> dict[str, Any]:
            (
                gate_schema,
                gate_system_prompt,
                gate_schema_chars,
                gate_tool_count,
            ) = gate_artifacts.resolve()
            return await _complete_conversation_gate_json(
                asr_llm,
                profile,
                tools,
                payload,
                context,
                schema=gate_schema,
                system_prompt=gate_system_prompt,
                schema_chars=gate_schema_chars,
                tool_count=gate_tool_count,
            )

        gate = StructuredConversationGate(gate_json, locale=profile.locale)
        system_prompt = _response_system_prompt(profile, tools)
        return ConversationEngine(
            arbiter=arbiter,
            admission=gate,
            planner=gate,
            llm=asr_llm,
            tts=asr_tts,
            tools=tools,
            sink=sink,
            policy=profile.policy,
            system_prompt=system_prompt,
            error_text=profile.conversation_error_text,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if resolved_settings.environment != "test":
            asr = SherpaZipformerAsrProvider(
                resolved_settings.models_root / "sherpa-onnx-zipformer-vi-30m-int8",
                num_threads=resolved_settings.asr_threads,
            )
            vad_model = SileroVadModel(
                resolved_settings.models_root / "silero-vad/silero_vad.onnx",
                num_threads=resolved_settings.vad_threads,
            )
            tts = VieNeuTtsProvider(
                resolved_settings.models_root / "vieneu-v3-turbo",
                voice=resolved_settings.tts_voice,
                style=resolved_settings.tts_style,
                speed=resolved_settings.tts_speed,
                output_sample_rate=resolved_settings.tts_output_sample_rate,
                num_threads=resolved_settings.tts_threads,
                apply_watermark=resolved_settings.tts_apply_watermark,
                stream_leadin_frames=resolved_settings.tts_stream_leadin_frames,
                backend=resolved_settings.tts_backend,
                native_model_dir=resolved_settings.tts_native_model_dir,
                native_library_path=resolved_settings.tts_native_library_path,
                native_realtime_headroom=resolved_settings.tts_native_realtime_headroom,
            )
            default_llm = llm_for_profile(SessionProfile.defaults(resolved_settings))
            runtime.update(asr=asr, vad_model=vad_model, tts=tts)
            llm_prewarm_task: asyncio.Task[bool] | None = None
            async with asyncio.TaskGroup() as prewarm_group:
                prewarm_group.create_task(asr.prewarm())
                prewarm_group.create_task(tts.prewarm())
                if resolved_settings.llm_prewarm:
                    llm_prewarm_task = prewarm_group.create_task(
                        _prewarm_llm(default_llm, resolved_settings.llm_prewarm_seconds)
                    )
            llm_prewarmed = llm_prewarm_task.result() if llm_prewarm_task is not None else True
            readiness.register(lambda: _healthy("asr"))
            readiness.register(lambda: _healthy("vad"))
            readiness.register(lambda: _healthy("tts"))
            readiness.register(
                _LlmReadinessProbe(
                    default_llm,
                    resolved_settings.llm_prewarm_seconds,
                    prewarmed=llm_prewarmed,
                )
            )
        manager = ManagerClient(resolved_settings)
        runtime["manager"] = manager
        if resolved_settings.require_device_auth:
            readiness.register(lambda: _manager_health(manager))
        logger.info(
            "voice_server_started",
            environment=resolved_settings.environment,
            bind_host=resolved_settings.host,
            bind_port=resolved_settings.port,
        )
        yield
        await asyncio.gather(
            *(candidate.provider.close() for candidate in llm_registry.values())
        )
        runtime_tts = runtime.get("tts")
        if isinstance(runtime_tts, VieNeuTtsProvider):
            await runtime_tts.close()
        await manager.close()
        logger.info("voice_server_stopped")

    application = FastAPI(
        title="Veetee Voice Server",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.readiness = readiness
    application.state.runtime = runtime
    application.state.engine_factory = engine_factory
    application.state.device_sessions = device_sessions

    @application.websocket(resolved_settings.websocket_path)
    async def websocket_voice(websocket: WebSocket) -> None:
        profile = SessionProfile.defaults(resolved_settings)
        manager_device_id: str | None = None
        if resolved_settings.require_device_auth:
            protocol_version = websocket.headers.get("protocol-version")
            hardware_id = websocket.headers.get("device-id")
            client_id = websocket.headers.get("client-id")
            authorization = websocket.headers.get("authorization", "")
            has_device_token = (
                authorization.startswith("Bearer ")
                and 8 <= len(authorization) <= 264
                and authorization[7:].isascii()
            )
            if (
                protocol_version != "1"
                or not _valid_device_header(hardware_id)
                or not _valid_device_header(client_id)
                or not has_device_token
            ):
                await websocket.close(code=1008, reason="device authentication required")
                return
            assert hardware_id is not None
            manager = cast(ManagerClient, runtime["manager"])
            try:
                device = await manager.authenticate_device(hardware_id, authorization[7:])
                profile = await manager.session_profile(device)
                manager_device_id = device.device_id
            except (ManagerAuthenticationError, httpx.HTTPError, KeyError, ValueError):
                await websocket.close(code=1008, reason="device authentication failed")
                return
        session = VoiceSession(
            websocket,
            settings=resolved_settings,
            profile=profile,
            asr=cast(SherpaZipformerAsrProvider, runtime["asr"]),
            vad_model=cast(SileroVadModel, runtime["vad_model"]),
            tts=tts_for_profile(profile),
            telemetry=(
                ConversationTelemetryBuffer(
                    cast(ManagerClient, runtime["manager"]),
                    manager_device_id,
                    queue_capacity=resolved_settings.telemetry_queue_capacity,
                    batch_size=resolved_settings.telemetry_batch_size,
                    flush_seconds=resolved_settings.telemetry_flush_seconds,
                    shutdown_seconds=resolved_settings.telemetry_shutdown_seconds,
                )
                if manager_device_id is not None
                else None
            ),
            engine_factory=engine_factory,
        )
        registration_id: str | None = None
        session_task = asyncio.create_task(session.run())
        ready_task: asyncio.Task[bool] | None = None
        registered_device_id: str | None = None
        if manager_device_id is not None:
            registered_device_id = manager_device_id
            ready_task = asyncio.create_task(session.mcp_ready.wait())
        try:
            if ready_task is not None:
                done, _ = await asyncio.wait(
                    {session_task, ready_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if ready_task in done and ready_task.result():
                    assert registered_device_id is not None
                    registration_id = await device_sessions.register(
                        registered_device_id, session.mcp
                    )
            await session_task
        finally:
            if ready_task is not None and not ready_task.done():
                ready_task.cancel()
                await asyncio.gather(ready_task, return_exceptions=True)
            if registered_device_id is not None and registration_id is not None:
                await device_sessions.unregister(registered_device_id, registration_id)

    if resolved_settings.websocket_path != "/veetee/v1/":
        application.add_api_websocket_route("/veetee/v1/", websocket_voice)

    @application.websocket(resolved_settings.lab_websocket_path)
    async def websocket_lab(websocket: WebSocket) -> None:
        nonlocal active_lab_sessions
        origin = websocket.headers.get("origin")
        if not _lab_origin_allowed(origin, resolved_settings.lab_allowed_origins):
            await websocket.close(code=1008, reason="Lab origin is not allowed")
            return
        async with lab_capacity_lock:
            if active_lab_sessions >= resolved_settings.lab_max_sessions:
                await websocket.close(code=1013, reason="Lab capacity is full")
                return
            active_lab_sessions += 1
        await websocket.accept()
        try:
            auth_message = await asyncio.wait_for(
                websocket.receive(), timeout=resolved_settings.hello_timeout_seconds
            )
            token = _parse_lab_auth(auth_message)
            manager = cast(ManagerClient, runtime["manager"])
            lab_context = await manager.consume_lab_session(token)
            profile = await manager.session_profile(
                DeviceContext(
                    device_id=f"lab:{lab_context.session_id}",
                    tenant_id=lab_context.tenant_id,
                    agent_id=lab_context.agent_id,
                    config_version=lab_context.config_version,
                )
            )
            if lab_context.mcp_mode == "simulated":
                tool_broker: ToolBroker = SimulatedLabToolBroker()
            elif lab_context.mcp_mode == "disabled":
                tool_broker = EmptyLabToolBroker()
            else:
                if lab_context.device_id is None:
                    raise ValueError("Selected-device Lab session is missing device id")
                catalog = await device_sessions.regular_tools(lab_context.device_id)
                tool_broker = SelectedDeviceLabToolBroker(
                    device_sessions, lab_context.device_id, catalog
                )
            session = LabSession(
                websocket,
                settings=resolved_settings,
                context=lab_context,
                profile=profile,
                asr=cast(SherpaZipformerAsrProvider, runtime["asr"]),
                vad_model=cast(SileroVadModel, runtime["vad_model"]),
                tts=tts_for_profile(profile),
                tool_broker=tool_broker,
                engine_factory=engine_factory,
            )
            await session.run()
        except TimeoutError:
            await websocket.close(code=1008, reason="Lab authentication timeout")
        except (ManagerAuthenticationError, httpx.HTTPError, KeyError, ValueError):
            await websocket.close(code=1008, reason="Lab authentication failed")
        except DeviceSessionUnavailableError:
            await websocket.close(code=1013, reason="Selected device is not connected")
        finally:
            async with lab_capacity_lock:
                active_lab_sessions = max(0, active_lab_sessions - 1)

    if resolved_settings.lab_websocket_path != "/veetee/lab/v1/":
        application.add_api_websocket_route("/veetee/lab/v1/", websocket_lab)

    @application.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id") or uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response: Response = await call_next(request)
            response.headers["x-request-id"] = request_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()

    @application.get("/health/live", response_model=HealthResponse)
    async def live() -> HealthResponse:
        return HealthResponse(status="ok", service="voice-server")

    @application.get(
        "/health/ready",
        response_model=ReadyResponse,
        responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadyResponse}},
    )
    async def ready(response: Response) -> ReadyResponse:
        is_ready, components = await readiness.snapshot()
        if not is_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyResponse(
            status="ready" if is_ready else "not_ready",
            components=[
                {
                    "name": component.name,
                    "healthy": component.healthy,
                    "required": component.required,
                    **({"detail": component.detail} if component.detail else {}),
                }
                for component in components
            ],
        )

    @application.get("/internal/v1/devices/{device_id}/mcp/tools")
    async def manager_device_tools(
        device_id: str,
        authorization: str = Header(default=""),
    ) -> list[dict[str, Any]]:
        _require_internal_service(authorization, resolved_settings.manager_internal_token)
        try:
            return await device_sessions.tools(device_id, timeout_seconds=8.0)
        except DeviceSessionUnavailableError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (DeviceMcpError, OperationDeadlineExceededError) as error:
            raise HTTPException(status_code=502, detail="Device MCP catalog unavailable") from error

    @application.post("/internal/v1/devices/{device_id}/mcp/tools/{tool_name:path}/call")
    async def manager_device_tool_call(
        device_id: str,
        tool_name: str,
        payload: ManagerMcpCallRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        _require_internal_service(authorization, resolved_settings.manager_internal_token)
        try:
            result = await device_sessions.call(
                device_id,
                tool_name,
                payload.arguments,
                confirmed=payload.confirmed,
                timeout_seconds=payload.timeout_seconds,
            )
        except DeviceSessionUnavailableError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except PermissionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Device MCP tool not found") from error
        except OperationDeadlineExceededError as error:
            raise HTTPException(status_code=504, detail="Device MCP call timed out") from error
        except DeviceMcpError as error:
            raise HTTPException(status_code=422, detail="Device MCP call failed") from error
        if not isinstance(result, dict):
            raise HTTPException(status_code=502, detail="Device MCP result is invalid")
        return result

    return application


def _require_internal_service(authorization: str, expected_token: str) -> None:
    supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not expected_token or not supplied or not hmac.compare_digest(supplied, expected_token):
        raise HTTPException(status_code=401, detail="Internal service authentication failed")


async def _healthy(name: str) -> ComponentHealth:
    return ComponentHealth(name, healthy=True, required=True)


async def _manager_health(manager: ManagerClient) -> ComponentHealth:
    healthy = await manager.health()
    return ComponentHealth(
        "manager-api",
        healthy=healthy,
        required=True,
        detail=None if healthy else "unreachable",
    )


def _llm_context(operation: str, timeout_seconds: float) -> OperationContext:
    return OperationContext(
        session_id="voice-server",
        turn_id=f"voice-server:{operation}",
        generation=0,
        token=CancellationToken(),
        deadline_at=monotonic() + timeout_seconds,
    )


async def _prewarm_llm(provider: FailoverLlmProvider, timeout_seconds: float) -> bool:
    try:
        await provider.prewarm(_llm_context("llm-prewarm", timeout_seconds))
        logger.info("llm_prewarm_complete")
        return True
    except Exception as error:
        logger.warning("llm_prewarm_failed", error=type(error).__name__)
        return False


class _LlmReadinessProbe:
    def __init__(
        self,
        provider: FailoverLlmProvider,
        timeout_seconds: float,
        *,
        prewarmed: bool,
    ) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._prewarmed = prewarmed
        self._lock = asyncio.Lock()

    async def __call__(self) -> ComponentHealth:
        if not self._prewarmed:
            async with self._lock:
                if not self._prewarmed:
                    self._prewarmed = await _prewarm_llm(self._provider, self._timeout_seconds)
            if not self._prewarmed:
                return ComponentHealth("llm", healthy=False, required=True, detail="prewarm_failed")
        return await _llm_health(self._provider, self._timeout_seconds)


async def _llm_health(
    provider: FailoverLlmProvider,
    timeout_seconds: float,
) -> ComponentHealth:
    try:
        healthy = await provider.check_health(_llm_context("llm-health", timeout_seconds))
    except Exception:
        healthy = False
    return ComponentHealth(
        "llm",
        healthy=healthy,
        required=True,
        detail=None if healthy else "unreachable",
    )


def _valid_device_header(value: str | None) -> bool:
    return (
        value is not None
        and 4 <= len(value) <= 128
        and value == value.strip()
        and all(
            character.isascii() and (character.isalnum() or character in "-_.:")
            for character in value
        )
    )


def _lab_origin_allowed(origin: str | None, configured: str) -> bool:
    allowed = {item.strip() for item in configured.split(",") if item.strip()}
    return origin is not None and origin in allowed


def _parse_lab_auth(message: Mapping[str, Any]) -> str:
    if message.get("type") == "websocket.disconnect":
        raise ManagerAuthenticationError("Lab disconnected before authentication")
    raw = message.get("text")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4_096:
        raise ManagerAuthenticationError("Lab authentication frame is invalid")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ManagerAuthenticationError("Lab authentication frame is invalid") from error
    token = payload.get("token") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("type") != "lab.auth"
        or not isinstance(token, str)
        or not 64 <= len(token) <= 2_048
        or not token.isascii()
    ):
        raise ManagerAuthenticationError("Lab authentication frame is invalid")
    return token


app = create_app()
