"""Tests for MCP JSON-RPC, pagination, and stale-session validation."""

import json

import pytest

from veetee_server.mcp import (
    MCPAdapter,
    MCPErrorCode,
)
from veetee_server.tools import ToolPolicy, ToolRegistry, register_default_local_tools


@pytest.fixture
def mcp_adapter():
    registry = ToolRegistry()
    register_default_local_tools(registry)
    return MCPAdapter(tool_registry=registry)


@pytest.mark.asyncio
async def test_mcp_initialize(mcp_adapter):
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "test-client"},
        },
    }
    resp = await mcp_adapter.handle_request(req)
    assert resp.id == 1
    assert resp.error is None
    assert resp.result["protocolVersion"] == "2024-11-05"
    assert "capabilities" in resp.result


@pytest.mark.asyncio
async def test_mcp_tools_list_paginated(mcp_adapter):
    # Page 1: limit=2
    req_p1 = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {"limit": 2},
    }
    resp_p1 = await mcp_adapter.handle_request(req_p1)
    assert resp_p1.error is None
    tools_p1 = resp_p1.result["tools"]
    assert len(tools_p1) == 2
    next_cursor = resp_p1.result["nextCursor"]
    assert next_cursor == "2"

    # Page 2: cursor=2, limit=2
    req_p2 = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/list",
        "params": {"cursor": next_cursor, "limit": 2},
    }
    resp_p2 = await mcp_adapter.handle_request(req_p2)
    assert resp_p2.error is None
    tools_p2 = resp_p2.result["tools"]
    assert len(tools_p2) >= 1
    assert tools_p1[0]["name"] != tools_p2[0]["name"]


@pytest.mark.asyncio
async def test_mcp_tools_call_success(mcp_adapter):
    req = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {
            "name": "local.get_time",
            "arguments": {"timezone": "UTC"},
            "session_id": "sess-100",
            "generation_id": "gen-1",
        },
    }
    resp = await mcp_adapter.handle_request(
        req, active_session_id="sess-100", active_generation_id="gen-1"
    )
    assert resp.error is None
    assert resp.result["isError"] is False
    assert "timestamp_iso" in resp.result["content"][0]["text"]


@pytest.mark.asyncio
async def test_mcp_stale_session_rejection(mcp_adapter):
    req = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {
            "name": "local.get_time",
            "arguments": {},
            "session_id": "stale-session-999",
            "generation_id": "gen-1",
        },
    }
    resp = await mcp_adapter.handle_request(
        req, active_session_id="active-session-100", active_generation_id="gen-1"
    )
    assert resp.error is not None
    assert resp.error.code == MCPErrorCode.STALE_SESSION
    assert "Session mismatch" in resp.error.message


@pytest.mark.asyncio
async def test_mcp_stale_generation_rejection(mcp_adapter):
    req = {
        "jsonrpc": "2.0",
        "id": 12,
        "method": "tools/call",
        "params": {
            "name": "local.get_time",
            "arguments": {},
            "session_id": "sess-100",
            "generation_id": "old-gen-0",
        },
    }
    resp = await mcp_adapter.handle_request(
        req, active_session_id="sess-100", active_generation_id="current-gen-2"
    )
    assert resp.error is not None
    assert resp.error.code == MCPErrorCode.STALE_SESSION
    assert "Generation mismatch" in resp.error.message


@pytest.mark.asyncio
async def test_mcp_malformed_json_and_method_not_found(mcp_adapter):
    # Malformed JSON
    raw_invalid = "{bad_json"
    res_raw = await mcp_adapter.handle_request_json(raw_invalid)
    parsed_err = json.loads(res_raw)
    assert parsed_err["error"]["code"] == MCPErrorCode.PARSE_ERROR

    # Method not found
    req_unknown = {"jsonrpc": "2.0", "id": 99, "method": "unknown/method"}
    resp_unknown = await mcp_adapter.handle_request(req_unknown)
    assert resp_unknown.error.code == MCPErrorCode.METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_mcp_policy_violation(mcp_adapter):
    policy = ToolPolicy(allowlist={"local.get_time"})
    req = {
        "jsonrpc": "2.0",
        "id": 20,
        "method": "tools/call",
        "params": {
            "name": "local.get_weather",
            "arguments": {"location": "Hà Nội"},
            "session_id": "s1",
        },
    }
    resp = await mcp_adapter.handle_request(req, active_session_id="s1", policy=policy)
    assert resp.error is not None
    assert resp.error.code == MCPErrorCode.POLICY_VIOLATION
