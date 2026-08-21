"""Device WebSocket router implementation for Veetee Server M1.3 (M1.6 wiring)."""

import asyncio
import logging
from time import monotonic
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from veetee_server.audio import (
    AudioProtocolError,
    AudioQueueItem,
    BoundedAudioQueue,
    OverflowPolicy,
    OversizedAudioFrameError,
    PacketPacer,
    SlowClientQueueOverflowError,
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
)
from veetee_server.pipeline.orchestrator import FakePipeline

from .auth import validate_handshake_headers
from .downlink import GatewayEventSink, run_downlink_sender, supervise_pipeline
from .ota import ota_router
from .protocol import (
    HelloMessage,
    ListenMessage,
    SessionMessage,
    make_error_envelope,
    parse_and_validate_json,
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
    return build_fake_pipeline(session, settings, vad_runtime=vad_runtime)


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


@router.websocket("/ws")
async def device_websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint handling device connection lifecycle according to M1.3 specification."""
    await websocket.accept()

    settings: Settings = getattr(websocket.app.state, "settings", None) or get_settings()
    registry: DeviceSessionRegistry | None = getattr(
        websocket.app.state, "device_session_registry", None
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
            await websocket.send_json(env)
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
            await websocket.send_json(env)
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
            await websocket.send_json(env)
            await websocket.close(code=1002)  # Protocol error
            return

        raw_text = first_frame.get("text")
        if not raw_text:
            env = make_error_envelope("veetee_invalid_input", "Empty frame", session_id=None)
            await websocket.send_json(env)
            await websocket.close(code=1008)
            return

        # Check JSON size limit
        if len(raw_text.encode("utf-8")) > settings.json_max_bytes:
            env = make_error_envelope(
                "veetee_invalid_input", "JSON payload size limit exceeded", session_id=None
            )
            await websocket.send_json(env)
            await websocket.close(code=1009)  # Message too big
            return

        # Parse JSON and validate depth limit
        try:
            payload = parse_and_validate_json(raw_text, max_depth=settings.json_max_depth)
        except Exception:
            env = make_error_envelope(
                "veetee_invalid_input", "Malformed JSON or depth limit exceeded", session_id=None
            )
            await websocket.send_json(env)
            await websocket.close(code=1008)
            return

        # Validate Hello schema
        try:
            HelloMessage.model_validate(payload)
        except ValidationError:
            env = make_error_envelope(
                "veetee_invalid_input", "Invalid hello schema or audio params", session_id=None
            )
            await websocket.send_json(env)
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
        session.accept()
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
        await websocket.send_json(server_hello)

        # One downlink sender per session drains the egress queue (stt/tts
        # control messages + paced audio frames) in FIFO order.
        session.cancellation.create_task(run_downlink_sender(session, websocket))

        # 3. Message Loop
        last_activity = monotonic()
        while True:
            idle_remaining = settings.idle_timeout_seconds - (monotonic() - last_activity)
            if idle_remaining <= 0:
                env = make_error_envelope(
                    "veetee_timeout", "Idle timeout", session_id=str(session.id)
                )
                await websocket.send_json(env)
                await websocket.close(code=1001)
                break
            try:
                frame = await asyncio.wait_for(
                    websocket.receive(),
                    timeout=min(settings.ping_interval_seconds, idle_remaining),
                )
            except TimeoutError:
                if monotonic() - last_activity >= settings.idle_timeout_seconds:
                    env = make_error_envelope(
                        "veetee_timeout", "Idle timeout", session_id=str(session.id)
                    )
                    await websocket.send_json(env)
                    await websocket.close(code=1001)
                    break
                await websocket.send_json({"type": "ping", "session_id": str(session.id)})
                try:
                    frame = await asyncio.wait_for(
                        websocket.receive(), timeout=settings.pong_timeout_seconds
                    )
                except TimeoutError:
                    env = make_error_envelope(
                        "veetee_timeout", "Pong timeout", session_id=str(session.id)
                    )
                    await websocket.send_json(env)
                    await websocket.close(code=1001)
                    break

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
                    await websocket.send_json(env)
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
                    await websocket.send_json(env)
                    await websocket.close(code=1009)
                    break
                except AudioProtocolError as exc:
                    env = make_error_envelope(
                        "veetee_invalid_input",
                        f"Malformed binary audio frame: {exc}",
                        session_id=str(session.id),
                    )
                    await websocket.send_json(env)
                    await websocket.close(code=1002)
                    break

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
                    item = AudioQueueItem(
                        payload=packet.payload,
                        duration_ms=60.0,
                        generation=session.ingress_queue.generation,
                        timestamp_ms=packet.timestamp_ms,
                    )
                    await session.ingress_queue.put(item)
                except SlowClientQueueOverflowError:
                    env = make_error_envelope(
                        "veetee_invalid_input",
                        "Audio queue overflow",
                        session_id=str(session.id),
                    )
                    await websocket.send_json(env)
                    await websocket.close(code=1009)
                    break
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
                await websocket.send_json(env)
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
                await websocket.send_json(env)
                await websocket.close(code=1008)
                break

            # Session ID verification if present
            msg_session_id = payload.get("session_id")
            if msg_session_id is not None and str(msg_session_id) != str(session.id):
                env = make_error_envelope(
                    "veetee_invalid_input",
                    "Session ID mismatch",
                    session_id=str(session.id),
                )
                await websocket.send_json(env)
                await websocket.close(code=1008)
                break

            msg_type = payload.get("type")

            if msg_type == "hello":
                env = make_error_envelope(
                    "veetee_invalid_input",
                    "Duplicate hello message",
                    session_id=str(session.id),
                )
                await websocket.send_json(env)
                await websocket.close(code=1008)
                break

            elif msg_type == "ping":
                try:
                    SessionMessage.model_validate(payload)
                except ValidationError:
                    await websocket.send_json(
                        make_error_envelope(
                            "veetee_invalid_input",
                            "Invalid control message",
                            session_id=str(session.id),
                        )
                    )
                    await websocket.close(code=1008)
                    break
                await websocket.send_json({"type": "pong", "session_id": str(session.id)})

            elif msg_type == "pong":
                try:
                    SessionMessage.model_validate(payload)
                except ValidationError:
                    await websocket.send_json(
                        make_error_envelope(
                            "veetee_invalid_input",
                            "Invalid control message",
                            session_id=str(session.id),
                        )
                    )
                    await websocket.close(code=1008)
                    break

            elif msg_type == "goodbye":
                try:
                    SessionMessage.model_validate(payload)
                except ValidationError:
                    await websocket.send_json(
                        make_error_envelope(
                            "veetee_invalid_input",
                            "Invalid control message",
                            session_id=str(session.id),
                        )
                    )
                    await websocket.close(code=1008)
                    break
                await websocket.send_json({"type": "goodbye", "session_id": str(session.id)})
                await websocket.close(code=1000)
                break

            elif msg_type == "abort":
                try:
                    SessionMessage.model_validate(payload)
                except ValidationError:
                    await websocket.send_json(
                        make_error_envelope(
                            "veetee_invalid_input",
                            "Invalid control message",
                            session_id=str(session.id),
                        )
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

            elif msg_type == "listen":
                try:
                    listen_msg = ListenMessage.model_validate(payload)
                except ValidationError:
                    env = make_error_envelope(
                        "veetee_invalid_input",
                        "Invalid listen schema",
                        session_id=str(session.id),
                    )
                    await websocket.send_json(env)
                    await websocket.close(code=1008)
                    break

                try:
                    if listen_msg.state == "start":
                        # Barge-in: a start while speaking or while the
                        # previous turn is still processing aborts it inside
                        # start_turn and begins a fresh capture.
                        if session.state in {SessionState.IDLE, SessionState.SPEAKING}:
                            await session.start_turn()
                    elif listen_msg.state == "stop":
                        if session.state is SessionState.LISTENING:
                            session.begin_processing()
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
                # Unsupported message type (mcp or others) -> return safe typed error
                env = make_error_envelope(
                    "veetee_invalid_input",
                    "Unsupported frame type",
                    session_id=str(session.id),
                )
                await websocket.send_json(env)

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
            await websocket.send_json(env)
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass
            if registry is not None:
                await registry.unregister(str(session.id))
