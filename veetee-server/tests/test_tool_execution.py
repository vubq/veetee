import asyncio
import unittest

from core.tools.base import ToolDescriptor
from core.tools.builtin.calculator import calculator_descriptor
from core.tools.builtin.time_tool import time_descriptor
from core.tools.executor import ToolExecutor
from core.tools.registry import ToolRegistry
from core.tools.results import ToolStatus


class ToolExecutionTests(unittest.IsolatedAsyncioTestCase):
    def test_read_only_tool_schema_tells_model_to_call_without_confirmation(self):
        tool = time_descriptor().as_openai_tool()["function"]
        self.assertIn("Tool chỉ đọc", tool["description"])
        self.assertIn("không cần xin xác nhận", tool["description"])
        self.assertNotIn("required", tool["parameters"])

    async def test_current_time_uses_default_timezone_without_argument(self):
        registry = ToolRegistry([time_descriptor()])
        executor = ToolExecutor(registry)
        result = await executor.execute("time-1", "get_current_time", {})
        self.assertEqual(result.status, ToolStatus.SUCCEEDED)
        self.assertEqual(result.data["timezone"], "Asia/Bangkok")
        self.assertTrue(result.data["time"])

    async def test_schema_validation_and_safe_calculator(self):
        registry = ToolRegistry([calculator_descriptor()])
        executor = ToolExecutor(registry)
        result = await executor.execute("1", "calculate", {"expression": "2+3*4"})
        self.assertEqual(result.status, ToolStatus.SUCCEEDED)
        self.assertEqual(result.data["result"], 14)

        invalid = await executor.execute("2", "calculate", {"expression": "2+2", "extra": True})
        self.assertEqual(invalid.status, ToolStatus.FAILED)
        self.assertIn("unknown argument", invalid.error)

    async def test_concurrent_duplicate_call_executes_handler_once(self):
        calls = 0

        async def handler(arguments):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return {"ok": True}

        descriptor = ToolDescriptor(
            name="once", description="test", input_schema={"type": "object", "additionalProperties": False},
            handler=handler, read_only=False, idempotent=False,
        )
        executor = ToolExecutor(ToolRegistry([descriptor]))
        left, right = await asyncio.gather(
            executor.execute("same-call", "once", {}),
            executor.execute("same-call", "once", {}),
        )
        self.assertEqual(calls, 1)
        self.assertEqual(left, right)
        self.assertEqual(executor.snapshot()["receipt_count"], 1)

    async def test_non_idempotent_timeout_after_dispatch_is_unknown(self):
        async def handler(arguments):
            await asyncio.sleep(0.1)

        descriptor = ToolDescriptor(
            name="side_effect", description="test",
            input_schema={"type": "object", "additionalProperties": False},
            handler=handler, timeout_ms=50, read_only=False, idempotent=False,
        )
        executor = ToolExecutor(ToolRegistry([descriptor]))
        result = await executor.execute("call", "side_effect", {})
        self.assertEqual(result.status, ToolStatus.UNKNOWN)
        self.assertTrue(result.metadata["dispatched"])

    async def test_side_effects_in_same_concurrency_group_are_serialized(self):
        active = 0
        max_active = 0

        async def handler(arguments):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            return {"ok": True}

        descriptors = [
            ToolDescriptor(
                name=f"mutate_{index}",
                description="test",
                input_schema={"type": "object", "additionalProperties": False},
                handler=handler,
                read_only=False,
                idempotent=False,
                concurrency_group="device_audio",
            )
            for index in range(2)
        ]
        executor = ToolExecutor(ToolRegistry(descriptors))
        await asyncio.gather(
            executor.execute("a", "mutate_0", {}),
            executor.execute("b", "mutate_1", {}),
        )
        self.assertEqual(max_active, 1)

    async def test_independent_read_only_groups_can_run_in_parallel(self):
        both_running = asyncio.Event()
        active = 0

        async def handler(arguments):
            nonlocal active
            active += 1
            if active == 2:
                both_running.set()
            await asyncio.wait_for(both_running.wait(), timeout=0.2)
            active -= 1
            return {"ok": True}

        descriptors = [
            ToolDescriptor(
                name=f"read_{index}",
                description="test",
                input_schema={"type": "object", "additionalProperties": False},
                handler=handler,
                read_only=True,
                concurrency_group=f"read_group_{index}",
            )
            for index in range(2)
        ]
        executor = ToolExecutor(ToolRegistry(descriptors), max_parallel_read_only=2)
        results = await asyncio.gather(
            executor.execute("r1", "read_0", {}),
            executor.execute("r2", "read_1", {}),
        )
        self.assertTrue(all(result.status == ToolStatus.SUCCEEDED for result in results))

    async def test_reused_call_id_with_changed_arguments_is_rejected(self):
        registry = ToolRegistry([calculator_descriptor()])
        executor = ToolExecutor(registry)
        first = await executor.execute("same", "calculate", {"expression": "1+1"})
        second = await executor.execute("same", "calculate", {"expression": "2+2"})
        self.assertEqual(first.status, ToolStatus.SUCCEEDED)
        self.assertEqual(second.status, ToolStatus.FAILED)
        self.assertIn("reused", second.error)

    async def test_cancel_while_waiting_for_group_lock_never_dispatches(self):
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        second_called = asyncio.Event()

        async def first_handler(arguments):
            first_started.set()
            await release_first.wait()
            return {"ok": True}

        async def second_handler(arguments):
            second_called.set()
            return {"ok": True}

        descriptors = [
            ToolDescriptor(
                name="first", description="test",
                input_schema={"type": "object", "additionalProperties": False},
                handler=first_handler, read_only=False, idempotent=False,
                concurrency_group="shared",
            ),
            ToolDescriptor(
                name="second", description="test",
                input_schema={"type": "object", "additionalProperties": False},
                handler=second_handler, read_only=False, idempotent=False,
                concurrency_group="shared",
            ),
        ]
        executor = ToolExecutor(ToolRegistry(descriptors))
        first_task = asyncio.create_task(executor.execute("a", "first", {}, turn_id="turn-1"))
        await asyncio.wait_for(first_started.wait(), timeout=0.2)

        cancel_event = asyncio.Event()
        second_task = asyncio.create_task(
            executor.execute("b", "second", {}, turn_id="turn-1", cancel_event=cancel_event)
        )
        await asyncio.sleep(0)
        cancel_event.set()
        with self.assertRaises(asyncio.CancelledError):
            await second_task

        release_first.set()
        await asyncio.wait_for(first_task, timeout=0.2)
        await asyncio.sleep(0)
        self.assertFalse(second_called.is_set())
        self.assertEqual(executor.snapshot()["active_count"], 0)

    async def test_same_model_call_id_is_scoped_to_turn(self):
        calls = []

        async def handler(arguments):
            calls.append(arguments["value"])
            return {"value": arguments["value"]}

        descriptor = ToolDescriptor(
            name="mutate", description="test",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            handler=handler, read_only=False, idempotent=False,
        )
        executor = ToolExecutor(ToolRegistry([descriptor]))
        first = await executor.execute("same", "mutate", {"value": 1}, turn_id="turn-1")
        second = await executor.execute("same", "mutate", {"value": 2}, turn_id="turn-2")
        self.assertEqual(first.status, ToolStatus.SUCCEEDED)
        self.assertEqual(second.status, ToolStatus.SUCCEEDED)
        self.assertEqual(calls, [1, 2])

    async def test_receipt_retention_caps_settled_calls_and_ttl_expires_unknown(self):
        now = [100.0]

        async def ok_handler(arguments):
            return {"value": arguments["value"]}

        async def slow_handler(arguments):
            await asyncio.sleep(0.1)

        descriptors = [
            ToolDescriptor(
                name="ok", description="test",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                handler=ok_handler, read_only=False, idempotent=True,
            ),
            ToolDescriptor(
                name="slow", description="test",
                input_schema={"type": "object", "additionalProperties": False},
                handler=slow_handler, timeout_ms=50, read_only=False, idempotent=False,
            ),
        ]
        executor = ToolExecutor(
            ToolRegistry(descriptors),
            receipt_cap=2,
            receipt_ttl_seconds=10,
            retention_clock=lambda: now[0],
        )

        unknown = await executor.execute("u", "slow", {}, turn_id="turn-u")
        self.assertEqual(unknown.status, ToolStatus.UNKNOWN)
        for value in (1, 2, 3):
            await executor.execute(str(value), "ok", {"value": value}, turn_id=f"turn-{value}")

        snapshot = executor.snapshot()
        self.assertGreaterEqual(snapshot["receipt_count"], 2)
        self.assertEqual(snapshot["receipt_status_counts"].get("unknown", 0), 1)

        # Cap pressure alone cannot evict a recent UNKNOWN side effect.
        duplicate = await executor.execute("u", "slow", {}, turn_id="turn-u")
        self.assertEqual(duplicate.status, ToolStatus.UNKNOWN)

        now[0] += 11
        await executor.execute("4", "ok", {"value": 4}, turn_id="turn-4")
        self.assertEqual(executor.snapshot()["receipt_status_counts"].get("unknown", 0), 0)


if __name__ == "__main__":
    unittest.main()
