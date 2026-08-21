"""Unit tests for intent routing strategy abstraction without keyword hardcoding."""

import pytest

from veetee_server.intent import (
    DirectChatStrategy,
    FunctionCallStrategy,
    IntentModelStrategy,
    IntentRouter,
    IntentRoutingContext,
    IntentRoutingResult,
    IntentStrategyNotFoundError,
    IntentType,
)


@pytest.mark.asyncio
async def test_direct_chat_strategy():
    strategy = DirectChatStrategy()
    ctx = IntentRoutingContext(utterance="Xin chào bạn")
    res = await strategy.route(ctx)

    assert res.intent == IntentType.DIRECT_CHAT
    assert res.strategy_name == "direct_chat"
    assert res.confidence == 1.0


@pytest.mark.asyncio
async def test_function_call_strategy():
    strategy = FunctionCallStrategy()

    # When tools exist
    ctx_tools = IntentRoutingContext(
        utterance="Mấy giờ rồi",
        available_tools=["local.get_time"],
    )
    res_tools = await strategy.route(ctx_tools)
    assert res_tools.intent == IntentType.FUNCTION_CALL

    # When no tools exist
    ctx_no_tools = IntentRoutingContext(utterance="Mấy giờ rồi", available_tools=[])
    res_no_tools = await strategy.route(ctx_no_tools)
    assert res_no_tools.intent == IntentType.DIRECT_CHAT


@pytest.mark.asyncio
async def test_intent_model_strategy_with_classifier():
    async def mock_classifier(context: IntentRoutingContext) -> IntentRoutingResult:
        return IntentRoutingResult(
            intent=IntentType.FUNCTION_CALL,
            strategy_name="intent_model",
            target_tool_name="local.get_weather",
            confidence=0.98,
        )

    strategy = IntentModelStrategy(classifier_fn=mock_classifier)
    ctx = IntentRoutingContext(utterance="Thời tiết Hà Nội hôm nay thế nào")
    res = await strategy.route(ctx)

    assert res.intent == IntentType.FUNCTION_CALL
    assert res.target_tool_name == "local.get_weather"
    assert res.confidence == 0.98


@pytest.mark.asyncio
async def test_intent_router_protocol_fast_path():
    router = IntentRouter()

    # Protocol command fast-path ONLY applies to deterministic system protocol signals
    ctx_proto = IntentRoutingContext(
        utterance="Hệ thống dừng",
        protocol_command="__sys_mute__",
    )
    res_proto = await router.route(ctx_proto)

    assert res_proto.intent == IntentType.PROTOCOL_COMMAND
    assert res_proto.fast_path is True
    assert res_proto.target_tool_name == "__sys_mute__"


@pytest.mark.asyncio
async def test_intent_router_no_keyword_hardcode_for_natural_text():
    """Verifies that natural language text does not trigger fake keyword matching."""
    router = IntentRouter(default_strategy="function_call")

    ctx_natural = IntentRoutingContext(
        utterance="Thời tiết là gì hả bạn",
        available_tools=["local.get_weather"],
    )
    res = await router.route(ctx_natural)

    # Should default to function_call strategy for native LLM handling, NOT keyword regex match
    assert res.intent == IntentType.FUNCTION_CALL
    assert res.fast_path is False
    assert res.target_tool_name is None


def test_intent_router_rejects_unregistered_strategy() -> None:
    with pytest.raises(IntentStrategyNotFoundError):
        IntentRouter(default_strategy="missing")
