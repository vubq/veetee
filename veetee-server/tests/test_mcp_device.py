import asyncio
import unittest

from core.tools.executor import ToolExecutor
from core.tools.mcp_device import MCPDeviceClient, MCP_PROTOCOL_VERSION
from core.tools.registry import ToolRegistry
from core.tools.results import ToolStatus


class MCPDeviceTests(unittest.IsolatedAsyncioTestCase):
    async def test_numeric_init_paginated_list_and_tool_call(self):
        registry = ToolRegistry()
        requests = []
        client = None

        async def send_payload(payload):
            requests.append(payload)
            self.assertIs(type(payload["id"]), int)
            method = payload["method"]
            if method == "initialize":
                result = {"protocolVersion": MCP_PROTOCOL_VERSION}
            elif method == "tools/list":
                self.assertFalse(payload["params"]["withUserTools"])
                if "cursor" not in payload["params"]:
                    result = {"tools": [], "nextCursor": "page-2"}
                else:
                    result = {"tools": [{
                        "name": "self.get_device_status",
                        "description": "status",
                        "inputSchema": {"type": "object", "additionalProperties": False},
                    }]}
            elif method == "tools/call":
                result = {"content": [{"type": "text", "text": '{"volume":42}'}], "isError": False}
            else:
                self.fail(f"unexpected MCP method: {method}")
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.create_task(client.handle_message({
                    "type": "mcp",
                    "payload": {"jsonrpc": "2.0", "id": payload["id"], "result": result},
                }))
            )
            return True

        client = MCPDeviceClient(send_payload=send_payload, registry=registry, request_timeout_ms=200)
        await client.on_hello(True)
        for _ in range(100):
            if client.ready:
                break
            await asyncio.sleep(0.005)
        self.assertTrue(client.ready, client.snapshot())
        self.assertIsNotNone(registry.get("device_get_status"))

        executor = ToolExecutor(registry)
        result = await executor.execute("call-1", "device_get_status", {})
        self.assertEqual(result.status, ToolStatus.SUCCEEDED)
        self.assertEqual(result.data, {"volume": 42})
        self.assertEqual([item["method"] for item in requests[:3]], ["initialize", "tools/list", "tools/list"])
        await client.close()

    async def test_missing_mcp_feature_leaves_registry_unchanged(self):
        registry = ToolRegistry()

        async def send_payload(payload):
            self.fail("MCP request should not be sent")

        client = MCPDeviceClient(send_payload=send_payload, registry=registry)
        await client.on_hello(False)
        self.assertFalse(client.enabled)
        self.assertFalse(client.ready)
        self.assertEqual(registry.descriptors(), [])


if __name__ == "__main__":
    unittest.main()
