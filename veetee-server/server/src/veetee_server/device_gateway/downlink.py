"""Gateway-side translation of pipeline events into the device wire protocol (M1.6).

This module owns the boundary between the pipeline and the socket:

- ``GatewayEventSink`` converts typed pipeline events into ordered downlink
  items (control JSON + framed audio) on the session's egress queue;
- ``run_downlink_sender`` drains that queue per session: controls go out
  immediately, audio frames are paced and re-validated against the queue
  generation so aborted turns never keep streaming;
- ``supervise_pipeline`` reacts to terminal pipeline outcomes (no utterance
  -> abort the turn; queue overflow -> slow-client close 1009).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from starlette.websockets import WebSocket

from veetee_server.audio.queue import QueueClosedError, SlowClientQueueOverflowError
from veetee_server.domain.session import ConversationTurn, DeviceSession
from veetee_server.pipeline.downlink import DownlinkItem, DownlinkKind
from veetee_server.pipeline.events import (
    PipelineEvent,
    SttEvent,
    TtsChunkEvent,
    TtsSentenceStartEvent,
    TtsStartEvent,
    TtsStopEvent,
)
from veetee_server.pipeline.orchestrator import PipelineOutcome

from .protocol import make_error_envelope

logger = logging.getLogger("veetee.device_gateway")


def websocket_send_lock(websocket: WebSocket) -> asyncio.Lock | None:
    """Returns the injected session send lock, including for minimal test doubles."""
    state = getattr(websocket, "state", None)
    return getattr(state, "send_lock", None)


async def send_ws_json(websocket: WebSocket, envelope: dict[str, Any]) -> None:
    """Serializes one JSON frame onto the socket under the shared per-session lock.

    Every concurrent sender (downlink audio/control, gateway error paths and
    the device MCP broker) must go through this helper so Starlette never sees
    interleaved ``send_*`` calls on the same WebSocket.
    """
    lock = websocket_send_lock(websocket)
    if lock is not None:
        async with lock:
            await websocket.send_json(envelope)
    else:
        await websocket.send_json(envelope)


class GatewayEventSink:
    """Translates pipeline events into ordered downlink items for the session."""

    def __init__(self, session: DeviceSession, generation: int | None = None) -> None:
        self._session = session
        self._generation = (
            generation if generation is not None else session.egress_queue.generation
        )

    async def emit(self, event: PipelineEvent) -> None:
        session = self._session
        generation = self._generation
        session_id = str(session.id)
        if isinstance(event, SttEvent):
            item = self._control(
                {"type": "stt", "text": event.text, "session_id": session_id}, generation
            )
        elif isinstance(event, TtsStartEvent):
            item = self._control(
                {"type": "tts", "state": "start", "session_id": session_id}, generation
            )
        elif isinstance(event, TtsSentenceStartEvent):
            item = self._control(
                {
                    "type": "tts",
                    "state": "sentence_start",
                    "text": event.text,
                    "session_id": session_id,
                },
                generation,
            )
        elif isinstance(event, TtsChunkEvent):
            item = DownlinkItem(
                kind=DownlinkKind.AUDIO,
                payload=event.frame,
                generation=generation,
                duration_ms=event.duration_ms,
            )
        elif isinstance(event, TtsStopEvent):
            item = self._control(
                {"type": "tts", "state": "stop", "session_id": session_id}, generation
            )
        else:
            raise TypeError(f"Unsupported pipeline event: {type(event).__name__}")
        await session.egress_queue.put(item)

    @staticmethod
    def _control(payload: dict[str, Any], generation: int) -> DownlinkItem:
        return DownlinkItem(
            kind=DownlinkKind.CONTROL,
            payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            generation=generation,
        )


async def run_downlink_sender(
    session: DeviceSession, websocket: WebSocket, *, playback_grace_seconds: float = 0.0
) -> None:
    """Drains the session downlink queue and writes items to the device socket.

    Audio frames are paced through the session pacer; a generation re-check
    right before the write drops frames superseded by an abort/barge-in.
    """
    queue = session.egress_queue
    try:
        while True:
            item = await queue.get()
            if queue.is_closed:
                return
            if item.generation < queue.generation:
                continue
            if item.kind is DownlinkKind.CONTROL:
                if queue.is_closed or item.generation < queue.generation:
                    continue
                control_text = item.payload.decode("utf-8")
                send_lock = websocket_send_lock(websocket)
                if send_lock is not None:
                    async with send_lock:
                        await websocket.send_text(control_text)
                else:
                    await websocket.send_text(control_text)
                control = json.loads(control_text)
                if control.get("type") == "tts" and control.get("state") == "stop":
                    session.mark_conversation_activity(
                        playback_grace_seconds=playback_grace_seconds
                    )
            else:
                await session.pacer.pace(item.duration_ms / 1000.0)
                if queue.is_closed or item.generation < queue.generation:
                    continue
                send_lock = websocket_send_lock(websocket)
                if send_lock is not None:
                    async with send_lock:
                        await websocket.send_bytes(item.payload)
                else:
                    await websocket.send_bytes(item.payload)
    except QueueClosedError:
        return
    except asyncio.CancelledError:
        raise
    except Exception:
        # The receive loop handles disconnect/teardown; a failing send must
        # not leave an unhandled background task behind.
        logger.debug(
            "downlink_sender_stopped",
            extra={"context": {"session_id": str(session.id)}},
        )


async def supervise_pipeline(
    session: DeviceSession,
    websocket: WebSocket,
    pipeline_task: asyncio.Task[PipelineOutcome],
    turn: ConversationTurn,
) -> None:
    """Handles terminal pipeline outcomes that need gateway-level action."""
    try:
        outcome = await pipeline_task
    except asyncio.CancelledError:
        return
    except SlowClientQueueOverflowError:
        try:
            await send_ws_json(
                websocket,
                make_error_envelope(
                    "veetee_invalid_input",
                    "Audio queue overflow",
                    session_id=str(session.id),
                ),
            )
            await websocket.close(code=1009)
        except Exception:
            pass
        return
    except Exception:
        logger.exception(
            "pipeline_failed",
            extra={
                "context": {
                    "session_id": str(session.id),
                    "turn_id": str(turn.id),
                }
            },
        )
        if session.current_turn is turn:
            try:
                await session.fail_turn()
            except Exception:
                logger.exception(
                    "pipeline_failure_cleanup_failed",
                    extra={"context": {"session_id": str(session.id)}},
                )
        return

    # A listen/stop with no usable speech must not leave a dangling turn in
    # the processing state; abort it only if the session is still on it.
    if outcome is PipelineOutcome.NO_UTTERANCE and session.current_turn is turn:
        try:
            await session.abort_turn()
        except Exception:
            logger.exception(
                "no_utterance_cleanup_failed",
                extra={"context": {"session_id": str(session.id)}},
            )
    elif outcome is PipelineOutcome.QUOTA_EXCEEDED and session.current_turn is turn:
        try:
            await send_ws_json(
                websocket,
                make_error_envelope(
                    "veetee_quota_exceeded",
                    "Usage quota exceeded",
                    session_id=str(session.id),
                ),
            )
            await session.abort_turn()
        except Exception:
            logger.exception(
                "quota_exceeded_cleanup_failed",
                extra={"context": {"session_id": str(session.id)}},
            )
