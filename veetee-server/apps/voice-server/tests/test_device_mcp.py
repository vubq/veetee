from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

import pytest

from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
    TurnCancelledError,
)
from veetee_voice_server.transport.mcp import DeviceMcpClient, DeviceMcpError

pytestmark = pytest.mark.asyncio

McpResponder = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


def operation_context(token: CancellationToken | None = None) -> OperationContext:
    return OperationContext(
        "session-1",
        "turn-1",
        1,
        token or CancellationToken(),
        monotonic() + 2.0,
    )


def tool(name: str, *, maximum: int = 100) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"Device tool {name}",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["volume"],
            "properties": {"volume": {"type": "integer", "minimum": 0, "maximum": maximum}},
        },
    }


def initialize_result(request_id: int | str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "veetee-s3-n16r8", "version": "test"},
        },
    }


def client_with_responder(
    responder: McpResponder,
) -> tuple[DeviceMcpClient, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []
    client: DeviceMcpClient

    async def sender(payload: dict[str, Any]) -> None:
        sent.append(payload)
        response = await responder(payload)
        if response is not None:
            await client.handle_payload(response)

    client = DeviceMcpClient(sender, session_id="session-1")
    return client, sent


async def test_initialize_discovers_paginated_regular_tools() -> None:
    async def responder(request: dict[str, Any]) -> dict[str, Any]:
        request_id = request["id"]
        if request["method"] == "initialize":
            return initialize_result(request_id)
        cursor = request["params"]["cursor"]
        if cursor == "":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [tool("self.audio_speaker.set_volume")],
                    "nextCursor": "page-2",
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [tool("self.audio_speaker.set_night_volume", maximum=50)],
                "nextCursor": "",
            },
        }

    client, sent = client_with_responder(responder)
    await client.initialize()

    assert [request["method"] for request in sent] == [
        "initialize",
        "tools/list",
        "tools/list",
    ]
    assert sent[1]["params"] == {"cursor": "", "withUserTools": False}
    assert sent[2]["params"] == {"cursor": "page-2", "withUserTools": False}
    assert [item["name"] for item in client.list_tools()] == [
        "self.audio_speaker.set_volume",
        "self.audio_speaker.set_night_volume",
    ]


async def test_schema_rejection_happens_before_device_dispatch() -> None:
    async def responder(request: dict[str, Any]) -> dict[str, Any]:
        if request["method"] == "initialize":
            return initialize_result(request["id"])
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "tools": [tool("self.audio_speaker.set_volume")],
                "nextCursor": "",
            },
        }

    client, sent = client_with_responder(responder)
    await client.initialize()

    with pytest.raises(DeviceMcpError, match="Invalid arguments"):
        await client.call("self.audio_speaker.set_volume", {"volume": 101}, operation_context())
    assert [request["method"] for request in sent].count("tools/call") == 0


async def test_manager_catalog_requires_confirmation_for_user_only_tool() -> None:
    async def responder(request: dict[str, Any]) -> dict[str, Any]:
        if request["method"] == "initialize":
            return initialize_result(request["id"])
        if request["method"] == "tools/list":
            regular = tool("self.audio_speaker.set_volume")
            regular.update(
                audience="regular",
                safetyClass="reversible",
                requiresConfirmation=False,
            )
            tools = [regular]
            if request["params"]["withUserTools"]:
                tools.append(
                    {
                        "name": "self.get_system_info",
                        "description": "Read diagnostic system information.",
                        "inputSchema": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {},
                        },
                        "audience": "user",
                        "safetyClass": "read_only",
                        "requiresConfirmation": True,
                    }
                )
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"tools": tools, "nextCursor": ""},
            }
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "content": [{"type": "text", "text": "diagnostic"}],
                "isError": False,
            },
        }

    client, sent = client_with_responder(responder)
    catalog = await client.manager_tools()

    assert [item["name"] for item in catalog] == [
        "self.audio_speaker.set_volume",
        "self.get_system_info",
    ]
    assert catalog[1]["audience"] == "user"
    assert catalog[1]["requiresConfirmation"] is True

    with pytest.raises(PermissionError, match="requires confirmation"):
        await client.manager_call(
            "self.get_system_info", {}, confirmed=False, context=operation_context()
        )
    assert [request["method"] for request in sent].count("tools/call") == 0

    result = await client.manager_call(
        "self.get_system_info", {}, confirmed=True, context=operation_context()
    )
    assert result["result"]["content"][0]["text"] == "diagnostic"
    assert [request["method"] for request in sent].count("tools/call") == 1


async def test_successful_call_and_json_rpc_error() -> None:
    fail_call = False

    async def responder(request: dict[str, Any]) -> dict[str, Any]:
        if request["method"] == "initialize":
            return initialize_result(request["id"])
        if request["method"] == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "tools": [tool("self.audio_speaker.set_volume")],
                    "nextCursor": "",
                },
            }
        if fail_call:
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -32602, "message": "Invalid volume"},
            }
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "content": [{"type": "text", "text": "true"}],
                "isError": False,
            },
        }

    client, _ = client_with_responder(responder)
    await client.initialize()
    result = await client.call("self.audio_speaker.set_volume", {"volume": 55}, operation_context())
    assert result == {
        "tool": "self.audio_speaker.set_volume",
        "arguments": {"volume": 55},
        "result": {
            "content": [{"type": "text", "text": "true"}],
            "isError": False,
        },
    }

    fail_call = True
    with pytest.raises(DeviceMcpError, match="Device MCP error -32602"):
        await client.call("self.audio_speaker.set_volume", {"volume": 55}, operation_context())


async def test_malformed_tool_result_is_rejected() -> None:
    async def responder(request: dict[str, Any]) -> dict[str, Any]:
        if request["method"] == "initialize":
            return initialize_result(request["id"])
        if request["method"] == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "tools": [tool("self.audio_speaker.set_volume")],
                    "nextCursor": "",
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"content": [{"type": "text", "value": "true"}]},
        }

    client, _ = client_with_responder(responder)
    await client.initialize()
    with pytest.raises(DeviceMcpError, match="tool result is invalid"):
        await client.call("self.audio_speaker.set_volume", {"volume": 55}, operation_context())


async def test_turn_cancellation_drops_late_device_result() -> None:
    pending_call: dict[str, Any] | None = None
    call_sent = asyncio.Event()

    async def responder(request: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal pending_call
        if request["method"] == "initialize":
            return initialize_result(request["id"])
        if request["method"] == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "tools": [tool("self.audio_speaker.set_volume")],
                    "nextCursor": "",
                },
            }
        pending_call = request
        call_sent.set()
        return None

    client, _ = client_with_responder(responder)
    await client.initialize()
    token = CancellationToken()
    call = asyncio.create_task(
        client.call("self.audio_speaker.set_volume", {"volume": 55}, operation_context(token))
    )
    await call_sent.wait()
    assert pending_call is not None
    token.cancel("button_interrupt")
    with pytest.raises(TurnCancelledError):
        await call

    await client.handle_payload(
        {
            "jsonrpc": "2.0",
            "id": pending_call["id"],
            "result": {
                "content": [{"type": "text", "text": "late"}],
                "isError": False,
            },
        }
    )


@pytest.mark.parametrize("case", ["duplicate", "cursor_cycle", "catalog_limit", "page_limit"])
async def test_invalid_tool_catalog_is_rejected(case: str) -> None:
    page = 0

    async def responder(request: dict[str, Any]) -> dict[str, Any]:
        nonlocal page
        if request["method"] == "initialize":
            return initialize_result(request["id"])
        page += 1
        if case == "duplicate":
            tools = [tool("self.audio_speaker.set_volume")] * 2
            next_cursor = ""
        elif case == "cursor_cycle":
            tools = []
            next_cursor = "same"
        elif case == "catalog_limit":
            tools = [tool(f"self.test.tool_{index}") for index in range(129)]
            next_cursor = ""
        else:
            tools = []
            next_cursor = f"page-{page}"
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"tools": tools, "nextCursor": next_cursor},
        }

    client, _ = client_with_responder(responder)
    with pytest.raises(DeviceMcpError):
        await client.initialize()
    if case == "cursor_cycle":
        assert page == 2
    elif case == "page_limit":
        assert page == 32


async def test_initialize_rejects_incompatible_protocol() -> None:
    async def responder(request: dict[str, Any]) -> dict[str, Any]:
        response = initialize_result(request["id"])
        response["result"]["protocolVersion"] = "2099-01-01"
        return response

    client, _ = client_with_responder(responder)
    with pytest.raises(DeviceMcpError, match="initialize result"):
        await client.initialize()
