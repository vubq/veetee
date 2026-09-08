import asyncio
import unittest

from core.tools.base import ToolDescriptor
from core.tools.builtin.calculator import calculator_descriptor
from core.tools.executor import ToolExecutor
from core.tools.registry import ToolRegistry
from core.tools.results import ToolStatus


class ToolExecutionTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
