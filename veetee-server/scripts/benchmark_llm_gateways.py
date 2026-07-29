from __future__ import annotations

import asyncio
import json
import os
import statistics
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from time import monotonic, perf_counter

from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
    iterate_operation,
)
from veetee_voice_server.conversation.types import (
    ConversationPlan,
    DialogueAct,
    PlanAction,
    Transcript,
)
from veetee_voice_server.providers.cliproxy import CliProxyApiLlmProvider
from veetee_voice_server.providers.contracts import LlmEvent, LlmRequest, LlmTextDelta
from veetee_voice_server.providers.nine_router import (
    NineRouterLlmProvider,
    NineRouterProviderError,
)

STRUCTURED_SCHEMA = {
    "type": "object",
    "properties": {
        "accepted": {"type": "boolean"},
        "answer": {"type": "string"},
    },
    "required": ["accepted", "answer"],
    "additionalProperties": False,
}


@dataclass
class GatewaySamples:
    structured_ms: list[float] = field(default_factory=list)
    first_token_ms: list[float] = field(default_factory=list)
    prose_total_ms: list[float] = field(default_factory=list)
    errors: dict[str, int] = field(default_factory=dict)

    def record_error(self, error: Exception) -> None:
        if isinstance(error, NineRouterProviderError):
            key = f"{error.code}:{error.status_code or 'none'}"
        else:
            key = type(error).__name__
        self.errors[key] = self.errors.get(key, 0) + 1

    def report(self) -> dict[str, object]:
        return {
            "structured": metric_report(self.structured_ms),
            "first_token": metric_report(self.first_token_ms),
            "prose_total": metric_report(self.prose_total_ms),
            "errors": self.errors,
        }


def operation_context(label: str, seconds: float = 20.0) -> OperationContext:
    return OperationContext(
        session_id="llm-gateway-benchmark",
        turn_id=label,
        generation=1,
        token=CancellationToken(),
        deadline_at=monotonic() + seconds,
    )


def prose_request() -> LlmRequest:
    return LlmRequest(
        transcript=Transcript(
            "Một cộng một bằng mấy? Trả lời đúng một câu ngắn.",
            "vi-VN",
        ),
        plan=ConversationPlan(
            action=PlanAction.RESPOND,
            dialogue_act=DialogueAct.QUESTION,
            locale="vi-VN",
            intent="benchmark.everyday_question",
            response_required=True,
        ),
        system_prompt="Trả lời tự nhiên, ngắn gọn bằng tiếng Việt.",
    )


async def benchmark_structured(
    provider: NineRouterLlmProvider,
    label: str,
) -> float:
    started = perf_counter()
    value = await provider.complete_json(
        system_prompt="Return JSON matching the supplied schema.",
        user_prompt='Set accepted=true and answer="ok".',
        context=operation_context(label),
        schema=STRUCTURED_SCHEMA,
        schema_transport="json_schema",
        max_output_tokens=256,
    )
    if value.get("accepted") is not True:
        raise RuntimeError("structured_probe_rejected")
    return (perf_counter() - started) * 1_000


async def benchmark_prose(
    provider: NineRouterLlmProvider,
    label: str,
) -> tuple[float, float]:
    context = operation_context(label)
    stream: AsyncIterator[LlmEvent] = iterate_operation(
        provider.stream(prose_request(), context),
        context,
    )
    started = perf_counter()
    first_token_ms: float | None = None
    output_characters = 0
    async for event in stream:
        if isinstance(event, LlmTextDelta) and event.text:
            output_characters += len(event.text)
            if first_token_ms is None:
                first_token_ms = (perf_counter() - started) * 1_000
    if first_token_ms is None or output_characters == 0:
        raise RuntimeError("empty_prose_stream")
    return first_token_ms, (perf_counter() - started) * 1_000


async def benchmark_gateway(
    provider: NineRouterLlmProvider,
    label: str,
    iteration: int,
    samples: GatewaySamples,
) -> None:
    try:
        samples.structured_ms.append(
            await benchmark_structured(provider, f"{label}:structured:{iteration}")
        )
    except Exception as error:
        samples.record_error(error)
    try:
        first_token_ms, total_ms = await benchmark_prose(
            provider,
            f"{label}:prose:{iteration}",
        )
        samples.first_token_ms.append(first_token_ms)
        samples.prose_total_ms.append(total_ms)
    except Exception as error:
        samples.record_error(error)


def metric_report(samples: list[float]) -> dict[str, object]:
    if not samples:
        return {"samples": 0, "median_ms": None, "p95_ms": None}
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, max(0, round(0.95 * len(ordered) - 1)))
    return {
        "samples": len(samples),
        "median_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[p95_index], 2),
        "values_ms": [round(value, 2) for value in samples],
    }


def benchmark_iterations() -> int:
    configured = int(os.getenv("VEETEE_LLM_BENCHMARK_ITERATIONS", "5"))
    return max(1, min(configured, 20))


async def main() -> None:
    providers: dict[str, NineRouterLlmProvider] = {
        "cliproxyapi": CliProxyApiLlmProvider(
            base_url=os.getenv(
                "VEETEE_CLIPROXY_BASE_URL",
                "http://127.0.0.1:8317/v1",
            ),
            model=os.getenv("VEETEE_CLIPROXY_MODEL", "gpt-5.6-terra"),
            api_key=os.getenv("VEETEE_CLIPROXY_API_KEY", ""),
            reasoning_effort="none",
            config={"responseFormat": "json_schema"},
        )
    }
    if os.getenv("VEETEE_LLM_BENCHMARK_INCLUDE_9ROUTER", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        providers["9router"] = NineRouterLlmProvider(
            base_url=os.getenv(
                "VEETEE_9ROUTER_BASE_URL",
                "http://127.0.0.1:20128/v1",
            ),
            model=os.getenv("VEETEE_9ROUTER_MODEL", "cx/gpt-5.6-terra"),
            api_key=os.getenv("VEETEE_9ROUTER_API_KEY", ""),
            reasoning_effort="none",
            provider_label="9router",
            config={"responseFormat": "json_schema"},
        )
    results = {name: GatewaySamples() for name in providers}
    try:
        for iteration in range(benchmark_iterations()):
            names = list(providers)
            if iteration % 2:
                names.reverse()
            for name in names:
                await benchmark_gateway(
                    providers[name],
                    name,
                    iteration,
                    results[name],
                )
    finally:
        await asyncio.gather(*(provider.close() for provider in providers.values()))
    print(
        json.dumps(
            {name: samples.report() for name, samples in results.items()},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
