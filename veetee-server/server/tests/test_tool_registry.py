"""Tests for tool registry, policy, execution bounds, cancellation, and audit."""

import asyncio

import pytest

from veetee_server.tools import (
    ConfirmationRequiredError,
    PolicyViolationError,
    ToolCollisionError,
    ToolContext,
    ToolDefinition,
    ToolExecutor,
    ToolPolicy,
    ToolRegistry,
    ToolTimeoutError,
    register_default_local_tools,
)


@pytest.mark.asyncio
async def test_tool_registry_collision_fail_fast():
    registry = ToolRegistry()
    tool1 = ToolDefinition(
        name="local.test",
        description="Test tool",
        parameters_schema={"type": "object"},
    )
    tool2 = ToolDefinition(
        name="local.test",
        description="Duplicate name tool",
        parameters_schema={"type": "object"},
    )

    registry.register(tool1)
    with pytest.raises(ToolCollisionError):
        registry.register(tool2, override=False)

    # Allowed with explicit override=True
    registry.register(tool2, override=True)
    assert registry.get_tool("local.test").description == "Duplicate name tool"


def test_tool_definition_invalid_name():
    with pytest.raises(ValueError, match="Invalid tool name"):
        ToolDefinition(
            name="invalid name with spaces!",
            description="Bad name",
            parameters_schema={},
        )


@pytest.mark.asyncio
async def test_tool_executor_policy_violation():
    registry = ToolRegistry()
    register_default_local_tools(registry)

    executor = ToolExecutor()
    policy = ToolPolicy(allowlist={"local.get_time"})  # local.get_weather is denied

    tool_def = registry.get_tool("local.get_weather")
    context = ToolContext(session_id="s1")

    with pytest.raises(PolicyViolationError):
        await executor.execute(tool_def, {"location": "Hà Nội"}, context, policy=policy)

    audits = executor.get_audit_log()
    assert len(audits) == 1
    assert audits[0].status == "policy_denied"


@pytest.mark.asyncio
async def test_tool_executor_confirmation_required():
    registry = ToolRegistry()
    register_default_local_tools(registry)

    executor = ToolExecutor()
    tool_def = registry.get_tool("local.control_device")  # requires_confirmation=True

    # 1. Unconfirmed execution fails
    ctx_unconfirmed = ToolContext(confirmation_granted=False)
    with pytest.raises(ConfirmationRequiredError):
        await executor.execute(tool_def, {"action": "reboot"}, ctx_unconfirmed)

    # 2. Confirmed execution succeeds
    ctx_confirmed = ToolContext(confirmation_granted=True)
    res = await executor.execute(tool_def, {"action": "reboot"}, ctx_confirmed)
    assert res.status == "success"
    assert "reboot" in res.output


@pytest.mark.asyncio
async def test_tool_executor_timeout_enforcement():
    async def slow_handler(args, ctx):
        await asyncio.sleep(2.0)
        return "Done"

    slow_tool = ToolDefinition(
        name="local.slow",
        description="Slow tool",
        parameters_schema={},
        handler=slow_handler,
    )

    executor = ToolExecutor(default_timeout_seconds=0.1)
    context = ToolContext()

    with pytest.raises(ToolTimeoutError):
        await executor.execute(slow_tool, {}, context)

    audits = executor.get_audit_log()
    assert audits[-1].status == "timeout"


@pytest.mark.asyncio
async def test_tool_executor_output_truncation():
    async def verbose_handler(args, ctx):
        return "A" * 5000

    verbose_tool = ToolDefinition(
        name="local.verbose",
        description="Verbose tool",
        parameters_schema={},
        handler=verbose_handler,
    )

    executor = ToolExecutor(max_output_chars=100)
    context = ToolContext()

    res = await executor.execute(verbose_tool, {}, context)
    assert res.truncated is True
    assert len(res.output) < 5000
    assert "Output truncated to 100 chars" in res.output


@pytest.mark.asyncio
async def test_tool_executor_cancellation():
    async def cancelling_handler(args, ctx):
        await asyncio.sleep(5.0)

    tool = ToolDefinition(
        name="local.cancel",
        description="Cancel tool",
        parameters_schema={},
        handler=cancelling_handler,
    )
    executor = ToolExecutor()
    context = ToolContext()

    task = asyncio.create_task(executor.execute(tool, {}, context))
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    audits = executor.get_audit_log()
    assert audits[-1].status == "cancelled"


@pytest.mark.asyncio
async def test_openai_schema_export():
    registry = ToolRegistry()
    register_default_local_tools(registry)

    schemas = registry.to_openai_schemas()
    assert len(schemas) == 4
    tool_names = [s["function"]["name"] for s in schemas]
    assert "local.get_time" in tool_names
    assert "local.control_device" in tool_names
