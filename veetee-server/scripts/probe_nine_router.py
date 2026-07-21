from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from time import monotonic, perf_counter

from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
    TurnCancelledError,
    iterate_operation,
)
from veetee_voice_server.conversation.types import (
    ConversationPlan,
    DialogueAct,
    PlanAction,
    Transcript,
)
from veetee_voice_server.providers.contracts import (
    LlmEvent,
    LlmRequest,
    LlmStreamDone,
    LlmTextDelta,
)
from veetee_voice_server.providers.nine_router import NineRouterLlmProvider


def operation_context(turn: str, seconds: float = 30.0) -> OperationContext:
    return OperationContext(
        session_id="9router-probe",
        turn_id=f"9router-probe:{turn}",
        generation=1,
        token=CancellationToken(),
        deadline_at=monotonic() + seconds,
    )


def llm_request(text: str) -> LlmRequest:
    return LlmRequest(
        transcript=Transcript(text=text, locale="vi-VN"),
        plan=ConversationPlan(
            action=PlanAction.RESPOND,
            dialogue_act=DialogueAct.QUESTION,
            locale="vi-VN",
            intent="probe.provider_conformance",
            response_required=True,
        ),
        system_prompt="Trả lời ngắn gọn bằng tiếng Việt.",
    )


async def collect_stream(
    provider: NineRouterLlmProvider, request: LlmRequest, context: OperationContext
) -> tuple[str, str | None]:
    text_parts: list[str] = []
    finish_reason: str | None = None
    async for event in iterate_operation(provider.stream(request, context), context):
        if isinstance(event, LlmTextDelta):
            text_parts.append(event.text)
        elif isinstance(event, LlmStreamDone):
            finish_reason = event.finish_reason
    return "".join(text_parts), finish_reason


async def probe_cancellation(
    provider: NineRouterLlmProvider,
) -> tuple[float, bool]:
    context = operation_context("cancel")
    stream: AsyncIterator[LlmEvent] = iterate_operation(
        provider.stream(
            llm_request("Hãy liệt kê 100 cách tối ưu một hệ thống hội thoại realtime."),
            context,
        ),
        context,
    )
    async for event in stream:
        if not isinstance(event, LlmTextDelta) or not event.text:
            continue
        started = perf_counter()
        context.token.cancel("conformance_probe")
        try:
            await anext(stream)
        except TurnCancelledError:
            return (perf_counter() - started) * 1000, True
        finally:
            await stream.aclose()
        return (perf_counter() - started) * 1000, False
    await stream.aclose()
    return 0.0, False


async def main() -> None:
    provider = NineRouterLlmProvider(
        base_url=os.getenv("VEETEE_9ROUTER_BASE_URL", "http://127.0.0.1:20128/v1"),
        model=os.getenv("VEETEE_9ROUTER_MODEL", "cx/gpt-5.4-mini"),
        api_key=os.getenv("VEETEE_9ROUTER_API_KEY", ""),
        reasoning_effort=os.getenv("VEETEE_9ROUTER_REASONING_EFFORT", "none"),
    )
    result: dict[str, object] = {}
    try:
        started = perf_counter()
        result["health"] = {
            "ok": await provider.health(operation_context("health", 5.0)),
            "latency_ms": round((perf_counter() - started) * 1000, 2),
        }

        started = perf_counter()
        structured = await provider.complete_json(
            system_prompt='Chỉ trả JSON object có hai field: "ok" và "language".',
            user_prompt='Trả {"ok":true,"language":"vi"}.',
            context=operation_context("json"),
        )
        result["structured_json"] = {
            "ok": structured.get("ok") is True and structured.get("language") == "vi",
            "latency_ms": round((perf_counter() - started) * 1000, 2),
        }

        started = perf_counter()
        text, finish_reason = await collect_stream(
            provider,
            llm_request("Veetee là gì? Trả lời đúng một câu."),
            operation_context("stream"),
        )
        result["stream"] = {
            "ok": bool(text.strip()),
            "characters": len(text),
            "finish_reason": finish_reason,
            "latency_ms": round((perf_counter() - started) * 1000, 2),
        }

        cancellation_ms, cancelled = await probe_cancellation(provider)
        result["cancellation"] = {
            "ok": cancelled,
            "latency_ms": round(cancellation_ms, 2),
        }
    finally:
        await provider.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
