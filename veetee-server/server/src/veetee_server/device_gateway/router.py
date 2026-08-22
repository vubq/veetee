"""Device WebSocket router implementation for Veetee Server M1.3 (M1.6 wiring)."""

import asyncio
import logging
from time import monotonic
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from veetee_server.audio import (
    UPLINK_PCM_FORMAT,
    AudioProtocolError,
    AudioQueueItem,
    BoundedAudioQueue,
    OverflowPolicy,
    OversizedAudioFrameError,
    PacketPacer,
    SlowClientQueueOverflowError,
    build_opus_decoder,
    parse_audio_frame,
)
from veetee_server.config import Settings, get_settings
from veetee_server.domain.errors import InvalidTransitionError
from veetee_server.domain.session import DeviceSession, SessionState
from veetee_server.pipeline.downlink import DownlinkQueue
from veetee_server.pipeline.factory import (
    PacerFactory,
    PipelineFactory,
    build_downlink_pacer,
    build_fake_pipeline,
    build_vad_stream,
)
from veetee_server.pipeline.orchestrator import FakePipeline

from .auth import validate_handshake_headers
from .barge_in import AutoEndpointDetector, BargeInCoordinator, BargeInDetector
from .downlink import (
    GatewayEventSink,
    run_downlink_sender,
    send_ws_json,
    supervise_pipeline,
)
from .mcp_broker import DeviceMCPBroker
from .ota import ota_router
from .protocol import (
    AbortMessage,
    HelloMessage,
    ListenMessage,
    McpMessage,
    SessionMessage,
    make_error_envelope,
    parse_and_validate_json,
    parse_device_mcp_response,
)
from .registry import DeviceSessionRegistry

logger = logging.getLogger("veetee.device_gateway")

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])
router.include_router(ota_router)


def _make_pacer(settings: Settings, app_state: Any) -> PacketPacer:
    """Builds the per-session downlink pacer, honoring test injection."""
    factory: PacerFactory | None = getattr(app_state, "pacer_factory", None)
    if factory is not None:
        return factory(settings)
    return build_downlink_pacer(settings)


def _make_pipeline(session: DeviceSession, settings: Settings, app_state: Any) -> FakePipeline:
    """Builds the pipeline for a turn, honoring test injection."""
    factory: PipelineFactory | None = getattr(app_state, "pipeline_factory", None)
    if factory is not None:
        return factory(session, settings)
    vad_runtime = getattr(app_state, "vad_runtime", None)
    asr_runtime = getattr(app_state, "asr_runtime", None)
    llm_runtime = getattr(app_state, "llm_runtime", None)
    tts_runtime = getattr(app_state, "tts_runtime", None)
    vieneu_runtime = getattr(app_state, "vieneu_runtime", None)
    transcript_recorder = None
    conversation_recorder = getattr(app_state, "conversation_recorder", None)
    if (
        conversation_recorder is not None
        and session.transcript_consent
        and session.owner_user_id is not None
        and session.device_pk is not None
        and session.consent_version
    ):
        from veetee_server.dialogue.recorder import SessionTranscriptRecorder

        transcript_recorder = SessionTranscriptRecorder(
            conversation_recorder,
            owner_user_id=session.owner_user_id,
            agent_id=session.agent_id,
            device_id=session.device_pk,
            consent_version=session.consent_version,
            session_id=str(session.id),
        )
    return build_fake_pipeline(
        session,
        settings,
        vad_runtime=vad_runtime,
        asr_runtime=asr_runtime,
        llm_runtime=llm_runtime,
        tts_runtime=tts_runtime,
        vieneu_runtime=vieneu_runtime,
        correction_repository=getattr(app_state, "correction_repository", None),
        context_provider_registry=getattr(app_state, "context_provider_registry", None),
        quota_service=getattr(app_state, "quota_service", None),
        transcript_recorder=transcript_recorder,
    )


def _start_pipeline(
    session: DeviceSession,
    settings: Settings,
    websocket: WebSocket,
    app_state: Any,
) -> None:
    """Starts the fake pipeline for the current processing turn.

    The pipeline task is owned by the turn's cancellation scope, so an abort
    or a barge-in cancels it at its next await point; the supervisor lives in
    the session scope and reacts to terminal outcomes.
    """
    turn = session.current_turn
    if turn is None:
        return
    pipeline = _make_pipeline(session, settings, app_state)
    sink = GatewayEventSink(session, generation=session.egress_queue.generation)
    pipeline_task = turn.cancellation.create_task(pipeline.run(session, sink))
    session.cancellation.create_task(supervise_pipeline(session, websocket, pipeline_task, turn))


async def _resolve_session_binding(
    app_state: Any, session: DeviceSession, timeout_seconds: float = 2.0
) -> None:
    """Resolves the tenant binding once per connection after a valid hello.

    The lookup requires BOTH handshake ids to match one activated device row
    and that row to carry an agent assignment. Any mismatch, unbound device,
    disabled persistence or database error leaves the session unbound: the
    connection continues with default server behavior and never reads another
    tenant's profile. The result stays fixed until reconnect re-resolves it.

    The per-device transcript consent policy (M6.2) is snapshotted from the
    same row at this boundary. Consent defaults to off for every failure mode
    so a session can never start recording without an explicit stored grant;
    like the binding it is not re-read mid-connection.
    """
    repository = getattr(app_state, "device_repository", None)
    if repository is None:
        return
    try:
        stored = await asyncio.wait_for(
            asyncio.to_thread(
                repository.get_by_device_and_client_id, session.device_id, session.client_id
            ),
            timeout=timeout_seconds,
        )
    except Exception:
        logger.exception(
            "binding_resolution_failed",
            extra={"context": {"session_id": str(session.id)}},
        )
        return
    if stored is None:
        logger.debug(
            "session_unbound_default_behavior",
            extra={
                "context": {
                    "session_id": str(session.id),
                    "device_id": session.device_id,
                }
            },
        )
        return
    session.owner_user_id = stored.owner_user_id
    session.agent_id = stored.agent_id
    session.device_pk = stored.id
    consented = bool(stored.transcript_consent) and bool(stored.consent_version.strip())
    session.transcript_consent = consented
    session.consent_version = stored.consent_version if consented else ""


async def _begin_processing_turn(
    session: DeviceSession, app_state: Any, timeout_seconds: float = 2.0
) -> None:
    """Single LISTENING->PROCESSING boundary shared by manual stop and auto endpoint.

    Transitions the current capture turn, then resolves the latest immutable
    agent runtime snapshot for the bound owner+agent pair and attaches it to
    the turn. Every failure mode is fail-safe (persistence disabled, binding
    absent, database error, agent deleted between turns): the turn keeps
    ``snapshot=None`` and the pipeline uses default server behavior instead of
    raising into the receive loop. A new turn (including a fresh barge-in
    capture) always resolves a fresh snapshot at this boundary.
    """
    session.begin_processing()
    turn = session.current_turn
    if turn is None or session.owner_user_id is None or session.agent_id is None:
        return
    repository = getattr(app_state, "agent_repository", None)
    if repository is None:
        return
    try:
        snapshot = await asyncio.wait_for(
            asyncio.to_thread(
                repository.snapshot,
                session.owner_user_id,
                session.agent_id,
            ),
            timeout=timeout_seconds,
        )
    except Exception:
        logger.exception(
            "agent_snapshot_resolution_failed",
            extra={"context": {"session_id": str(session.id)}},
        )
        return
    if snapshot is not None:
        turn.snapshot = snapshot


async def _cleanup_session(
    session: DeviceSession,
    registry: DeviceSessionRegistry | None,
    broker: DeviceMCPBroker | None = None,
) -> None:
    """Stops advertising a disconnected session before draining its tasks.

    Cancelling pending device MCP calls happens first so control-plane
    callers fail with a typed disconnect error instead of waiting for their
    full call timeout.
    """
    if broker is not None:
        try:
            broker.cancel_session(str(session.id))
        except Exception:
            pass
    if registry is not None:
        await registry.unregister(str(session.id))
    try:
        await session.close()
    except Exception:
        pass


def _handle_device_mcp_frame(
    broker: DeviceMCPBroker | None,
    session: DeviceSession,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Routes one inbound ``type=mcp`` frame; returns a safe error envelope or None.

    The live session id of this WebSocket connection is the only routing key.
    An envelope ``session_id`` that disagrees marks the frame stale and it is
    dropped without any response. Malformed frames - including unsolicited
    device JSON-RPC requests/notifications - get a typed error that never
    echoes payload content, and the connection stays alive.
    """
    try:
        message = McpMessage.model_validate(payload)
    except ValidationError:
        return make_error_envelope(
            "veetee_invalid_input", "Invalid MCP frame", session_id=str(session.id)
        )
    envelope_session_id = message.session_id
    if envelope_session_id is not None and envelope_session_id != str(session.id):
        logger.debug(
            "mcp_stale_session_frame_ignored",
            extra={"context": {"session_id": str(session.id)}},
        )
        return None
    try:
        rpc_id = parse_device_mcp_response(message.payload)
    except ValueError:
        return make_error_envelope(
            "veetee_invalid_input", "Invalid MCP frame", session_id=str(session.id)
        )
    if broker is None:
        return None
    if "result" in message.payload:
        routed = broker.handle_response(str(session.id), rpc_id, message.payload["result"])
    else:
        error_object = message.payload["error"]
        routed = broker.handle_error(
            str(session.id),
            rpc_id,
            int(error_object["code"]),
            str(error_object["message"]),
        )
    if not routed:
        logger.debug(
            "mcp_unmatched_response_ignored",
            extra={"context": {"session_id": str(session.id)}},
        )
    return None


@router.websocket("/ws")
async def device_websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint handling device connection lifecycle according to M1.3 specification."""
    await websocket.accept()
    websocket.state.send_lock = asyncio.Lock()

    settings: Settings = getattr(websocket.app.state, "settings", None) or get_settings()
    registry: DeviceSessionRegistry | None = getattr(
        websocket.app.state, "device_session_registry", None
    )
    mcp_broker: DeviceMCPBroker | None = getattr(
        websocket.app.state, "device_mcp_broker", None
    )

    # 1. Validate Auth & Headers
    auth_result = validate_handshake_headers(
        websocket.headers,
        expected_token=settings.device_gateway_token,
        id_max_length=settings.id_max_length,
    )
    if not auth_result.is_valid:
        env = make_error_envelope(
            auth_result.error_code or "veetee_auth_failed",
            auth_result.error_message or "Handshake validation failed",
            session_id=None,
        )
        try:
            await send_ws_json(websocket, env)
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    device_id = auth_result.device_id
    client_id = auth_result.client_id
    assert device_id is not None and client_id is not None

    session: DeviceSession | None = None

    try:
        # 2. Wait for initial Hello text frame within hello_timeout_seconds
        try:
            first_frame = await asyncio.wait_for(
                websocket.receive(), timeout=settings.hello_timeout_seconds
            )
        except TimeoutError:
            env = make_error_envelope("veetee_timeout", "Hello timeout", session_id=None)
            await send_ws_json(websocket, env)
            await websocket.close(code=1008)
            return

        if first_frame.get("type") == "websocket.disconnect":
            return

        # Check binary frame before hello
        if "bytes" in first_frame and first_frame["bytes"] is not None:
            env = make_error_envelope(
                "veetee_invalid_input",
                "Binary frame received before hello",
                session_id=None,
            )
            await send_ws_json(websocket, env)
            await websocket.close(code=1002)  # Protocol error
            return

        raw_text = first_frame.get("text")
        if not raw_text:
            env = make_error_envelope("veetee_invalid_input", "Empty frame", session_id=None)
            await send_ws_json(websocket, env)
            await websocket.close(code=1008)
            return

        # Check JSON size limit
        if len(raw_text.encode("utf-8")) > settings.json_max_bytes:
            env = make_error_envelope(
                "veetee_invalid_input", "JSON payload size limit exceeded", session_id=None
            )
            await send_ws_json(websocket, env)
            await websocket.close(code=1009)  # Message too big
            return

        # Parse JSON and validate depth limit
        try:
            payload = parse_and_validate_json(raw_text, max_depth=settings.json_max_depth)
        except Exception:
            env = make_error_envelope(
                "veetee_invalid_input", "Malformed JSON or depth limit exceeded", session_id=None
            )
            await send_ws_json(websocket, env)
            await websocket.close(code=1008)
            return

        # Validate Hello schema
        try:
            hello_msg = HelloMessage.model_validate(payload)
        except ValidationError:
            env = make_error_envelope(
                "veetee_invalid_input", "Invalid hello schema or audio params", session_id=None
            )
            await send_ws_json(websocket, env)
            await websocket.close(code=1008)
            return

        # Create DeviceSession
        session = DeviceSession(
            device_id=device_id,
            client_id=client_id,
            cleanup_timeout_seconds=settings.cleanup_timeout_seconds,
            protocol_version=auth_result.protocol_version,
            pacer=_make_pacer(settings, websocket.app.state),
            ingress_queue=BoundedAudioQueue(
                max_items=settings.audio_max_queue_items,
                max_bytes=settings.audio_max_queue_bytes,
                max_duration_ms=settings.audio_max_queue_duration_ms,
                overflow_policy=OverflowPolicy.DROP_OLDEST,
            ),
            egress_queue=DownlinkQueue(
                max_items=settings.audio_max_queue_items,
                max_bytes=settings.audio_max_queue_bytes,
                max_duration_ms=settings.audio_max_queue_duration_ms,
                overflow_policy=OverflowPolicy.FAIL_SESSION,
            ),
        )
        session.negotiate_features(hello_msg.features)
        session.accept()

        # The hello is valid: resolve the tenant binding for this connection
        # from BOTH ids before the first turn can start. Failures fall back
        # to default server behavior; the binding is fixed until reconnect.
        await _resolve_session_binding(
            websocket.app.state, session, settings.agent_snapshot_timeout_seconds
        )

        if registry is not None:
            await registry.register(session, websocket)

        # Broadcast Server Hello
        server_hello = {
            "type": "hello",
            "transport": "websocket",
            "session_id": str(session.id),
            "audio_params": {
                "format": "opus",
                "sample_rate": 24000,
                "channels": 1,
                "frame_duration": 60,
            },
        }
        await send_ws_json(websocket, server_hello)

        # One downlink sender per session drains the egress queue (stt/tts
        # control messages + paced audio frames) in FIFO order.
        session.cancellation.create_task(
            run_downlink_sender(
                session,
                websocket,
                playback_grace_seconds=settings.conversation_playback_drain_seconds,
            )
        )
        barge_in: BargeInCoordinator | None = None
        auto_endpoint: AutoEndpointDetector | None = None

        # 3. Message Loop
        last_activity = monotonic()
        session.mark_conversation_activity()
        while True:
            conversation_idle = monotonic() - session.last_conversation_activity
            if (
                session.state is SessionState.LISTENING
                and conversation_idle >= settings.conversation_idle_timeout_seconds
            ):
                await websocket.close(code=1000, reason="conversation_idle_timeout")
                break
            idle_remaining = settings.idle_timeout_seconds - (monotonic() - last_activity)
            conversation_remaining = (
                settings.conversation_idle_timeout_seconds - conversation_idle
            )
            if idle_remaining <= 0:
                env = make_error_envelope(
                    "veetee_timeout", "Idle timeout", session_id=str(session.id)
                )
                await send_ws_json(websocket, env)
                await websocket.close(code=1001)
                break
            try:
                frame = await asyncio.wait_for(
                    websocket.receive(),
                    timeout=min(
                        settings.ping_interval_seconds,
                        idle_remaining,
                        max(0.001, conversation_remaining),
                    ),
                )
            except TimeoutError:
                if monotonic() - last_activity >= settings.idle_timeout_seconds:
                    env = make_error_envelope(
                        "veetee_timeout", "Idle timeout", session_id=str(session.id)
                    )
                    await send_ws_json(websocket, env)
                    await websocket.close(code=1001)
                    break
                # Do not send an application-level JSON ping here. The ESP32
                # client accepts WebSocket control frames, not this optional
                # JSON heartbeat, and would disconnect on an unknown message.
                continue

            if frame.get("type") == "websocket.disconnect":
                break

            last_activity = monotonic()

            # Binary Frame Processing
            if "bytes" in frame and frame["bytes"] is not None:
                binary_data = frame["bytes"]
                if len(binary_data) > settings.binary_max_bytes:
                    env = make_error_envelope(
                        "veetee_invalid_input",
                        "Binary payload size limit exceeded",
                        session_id=str(session.id),
                    )
                    await send_ws_json(websocket, env)
                    await websocket.close(code=1009)
                    break
                try:
                    packet = parse_audio_frame(
                        binary_data,
                        negotiated_version=session.protocol_version,
                        max_payload_bytes=settings.binary_max_bytes,
                    )
                except OversizedAudioFrameError:
                    env = make_error_envelope(
                        "veetee_invalid_input",
                        "Audio payload size limit exceeded",
                        session_id=str(session.id),
                    )
                    await send_ws_json(websocket, env)
                    await websocket.close(code=1009)
                    break
                except AudioProtocolError as exc:
                    env = make_error_envelope(
                        "veetee_invalid_input",
                        f"Malformed binary audio frame: {exc}",
                        session_id=str(session.id),
                    )
                    await send_ws_json(websocket, env)
                    await websocket.close(code=1002)
                    break

                item = AudioQueueItem(
                    payload=packet.payload,
                    duration_ms=60.0,
                    generation=session.ingress_queue.generation,
                    timestamp_ms=packet.timestamp_ms,
                )
                if session.state is SessionState.SPEAKING and session.is_barge_in_eligible:
                    if barge_in is None:
                        barge_in = BargeInCoordinator(
                            session,
                            BargeInDetector(
                                build_opus_decoder(
                                    settings, pcm_format=UPLINK_PCM_FORMAT
                                ),
                                build_vad_stream(
                                    settings,
                                    vad_runtime=getattr(
                                        websocket.app.state, "vad_runtime", None
                                    ),
                                ),
                                max_pre_roll_frames=settings.barge_in_pre_roll_frames,
                            ),
                        )
                    expected_turn = session.current_turn
                    if expected_turn is not None and await barge_in.process(item, expected_turn):
                        barge_in.close()
                        barge_in = None
                    continue
                if session.state is not SessionState.LISTENING:
                    # Audio frames are only accepted while capturing; anything
                    # arriving during processing/streaming is dropped.
                    logger.debug(
                        "dropping_audio_outside_listening",
                        extra={
                            "context": {
                                "session_id": str(session.id),
                                "state": str(session.state),
                            }
                        },
                    )
                    continue

                try:
                    await session.ingress_queue.put(item)
                except SlowClientQueueOverflowError:
                    env = make_error_envelope(
                        "veetee_invalid_input",
                        "Audio queue overflow",
                        session_id=str(session.id),
                    )
                    await send_ws_json(websocket, env)
                    await websocket.close(code=1009)
                    break
                if auto_endpoint is not None:
                    speech_was_started = auto_endpoint.speech_started
                    endpoint_reached = await auto_endpoint.process(item)
                    if auto_endpoint.speech_started and not speech_was_started:
                        session.mark_conversation_activity()
                else:
                    endpoint_reached = False
                if auto_endpoint is not None and endpoint_reached:
                    session.mark_conversation_activity()
                    auto_endpoint.close()
                    auto_endpoint = None
                    if session.state is SessionState.LISTENING:
                        await _begin_processing_turn(
                            session,
                            websocket.app.state,
                            settings.agent_snapshot_timeout_seconds,
                        )
                        _start_pipeline(session, settings, websocket, websocket.app.state)
                continue

            # Text Frame Processing
            raw_text = frame.get("text")
            if not raw_text:
                continue

            if len(raw_text.encode("utf-8")) > settings.json_max_bytes:
                env = make_error_envelope(
                    "veetee_invalid_input",
                    "JSON payload size limit exceeded",
                    session_id=str(session.id),
                )
                await send_ws_json(websocket, env)
                await websocket.close(code=1009)
                break

            try:
                payload = parse_and_validate_json(raw_text, max_depth=settings.json_max_depth)
            except Exception:
                env = make_error_envelope(
                    "veetee_invalid_input",
                    "Malformed JSON or depth limit exceeded",
                    session_id=str(session.id),
                )
                await send_ws_json(websocket, env)
                await websocket.close(code=1008)
                break

            # Device MCP frames (M6.7) are responses to server-issued JSON-RPC
            # calls. They are routed to the broker keyed by this connection's
            # live session; malformed, stale or unsolicited frames never tear
            # the session down, so they are handled before the strict
            # session-id mismatch gate below.
            if payload.get("type") == "mcp":
                error_env = _handle_device_mcp_frame(mcp_broker, session, payload)
                if error_env is not None:
                    await send_ws_json(websocket, error_env)
                continue

            # Session ID verification if present
            msg_session_id = payload.get("session_id")
            if msg_session_id is not None and str(msg_session_id) != str(session.id):
                env = make_error_envelope(
                    "veetee_invalid_input",
                    "Session ID mismatch",
                    session_id=str(session.id),
                )
                await send_ws_json(websocket, env)
                await websocket.close(code=1008)
                break

            msg_type = payload.get("type")

            if msg_type == "hello":
                env = make_error_envelope(
                    "veetee_invalid_input",
                    "Duplicate hello message",
                    session_id=str(session.id),
                )
                await send_ws_json(websocket, env)
                await websocket.close(code=1008)
                break

            elif msg_type == "ping":
                try:
                    SessionMessage.model_validate(payload)
                except ValidationError:
                    await send_ws_json(
                        websocket,
                        make_error_envelope(
                            "veetee_invalid_input",
                            "Invalid control message",
                            session_id=str(session.id),
                        ),
                    )
                    await websocket.close(code=1008)
                    break
                await send_ws_json(websocket, {"type": "pong", "session_id": str(session.id)})

            elif msg_type == "pong":
                try:
                    SessionMessage.model_validate(payload)
                except ValidationError:
                    await send_ws_json(
                        websocket,
                        make_error_envelope(
                            "veetee_invalid_input",
                            "Invalid control message",
                            session_id=str(session.id),
                        ),
                    )
                    await websocket.close(code=1008)
                    break

            elif msg_type == "goodbye":
                try:
                    SessionMessage.model_validate(payload)
                except ValidationError:
                    await send_ws_json(
                        websocket,
                        make_error_envelope(
                            "veetee_invalid_input",
                            "Invalid control message",
                            session_id=str(session.id),
                        ),
                    )
                    await websocket.close(code=1008)
                    break
                await send_ws_json(websocket, {"type": "goodbye", "session_id": str(session.id)})
                await websocket.close(code=1000)
                break

            elif msg_type == "abort":
                try:
                    AbortMessage.model_validate(payload)
                except ValidationError:
                    await send_ws_json(
                        websocket,
                        make_error_envelope(
                            "veetee_invalid_input",
                            "Invalid control message",
                            session_id=str(session.id),
                        ),
                    )
                    await websocket.close(code=1008)
                    break
                if session.current_turn is not None or session.state in {
                    SessionState.LISTENING,
                    SessionState.SPEAKING,
                }:
                    try:
                        await session.abort_turn()
                    except Exception:
                        logger.exception(
                            "abort_cleanup_failed",
                            extra={"context": {"session_id": str(session.id)}},
                        )
                        raise
                if auto_endpoint is not None:
                    auto_endpoint.close()
                    auto_endpoint = None

            elif msg_type == "listen":
                try:
                    listen_msg = ListenMessage.model_validate(payload)
                except ValidationError:
                    env = make_error_envelope(
                        "veetee_invalid_input",
                        "Invalid listen schema",
                        session_id=str(session.id),
                    )
                    await send_ws_json(websocket, env)
                    await websocket.close(code=1008)
                    break

                try:
                    if listen_msg.state == "start":
                        session.listen_mode = listen_msg.mode or session.listen_mode or "auto"
                        if barge_in is not None:
                            barge_in.close()
                        barge_in = None
                        if auto_endpoint is not None:
                            auto_endpoint.close()
                        auto_endpoint = None
                        # Barge-in: a start while speaking or while the
                        # previous turn is still processing aborts it inside
                        # start_turn and begins a fresh capture.
                        if session.state in {SessionState.IDLE, SessionState.SPEAKING}:
                            await session.start_turn()
                            if session.listen_mode == "auto":
                                auto_endpoint = AutoEndpointDetector(
                                    build_opus_decoder(
                                        settings, pcm_format=UPLINK_PCM_FORMAT
                                    ),
                                    build_vad_stream(
                                        settings,
                                        vad_runtime=getattr(
                                            websocket.app.state, "vad_runtime", None
                                        ),
                                    ),
                                )
                    elif listen_msg.state == "stop":
                        if auto_endpoint is not None:
                            auto_endpoint.close()
                            auto_endpoint = None
                        if session.state is SessionState.LISTENING:
                            await _begin_processing_turn(
                                session,
                                websocket.app.state,
                                settings.agent_snapshot_timeout_seconds,
                            )
                            _start_pipeline(session, settings, websocket, websocket.app.state)
                except InvalidTransitionError:
                    # Out-of-sync listen frames (e.g. stop before a streamable
                    # turn) must not tear down the connection; the device can
                    # resync with a subsequent listen/start.
                    logger.debug(
                        "ignoring_listen_state_mismatch",
                        extra={"context": {"session_id": str(session.id)}},
                    )

            else:
                # Unsupported message type -> return safe typed error
                env = make_error_envelope(
                    "veetee_invalid_input",
                    "Unsupported frame type",
                    session_id=str(session.id),
                )
                await send_ws_json(websocket, env)

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception(
            "unhandled_websocket_error",
            extra={"context": {"session_id": str(session.id) if session else None}},
        )
        try:
            sid = str(session.id) if session else None
            env = make_error_envelope("veetee_internal", "Internal server error", session_id=sid)
            await send_ws_json(websocket, env)
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        if "barge_in" in locals() and barge_in is not None:
            barge_in.close()
        if "auto_endpoint" in locals() and auto_endpoint is not None:
            auto_endpoint.close()
        if session is not None:
            await _cleanup_session(session, registry, mcp_broker)
