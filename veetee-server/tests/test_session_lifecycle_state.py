import asyncio
import unittest

from core.session_lifecycle import SessionLifecycleState


class SessionLifecycleStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_ownership_and_close(self):
        state = SessionLifecycleState()
        self.assertTrue(state.owns_turn(0))

        self.assertEqual(state.advance_turn(), 1)
        self.assertTrue(state.owns_turn(1))
        self.assertFalse(state.owns_turn(0))

        self.assertEqual(state.advance_capture(), 1)
        self.assertEqual(state.advance_wake(), 1)

        self.assertTrue(state.begin_close())
        self.assertFalse(state.active)
        self.assertTrue(state.closed)
        self.assertFalse(state.owns_turn(state.turn_generation))
        self.assertFalse(state.begin_close())

    async def test_abort_sets_cancel_and_cancels_bound_foreign_task(self):
        state = SessionLifecycleState()
        cancel_event = asyncio.Event()
        blocker = asyncio.Event()

        async def worker():
            await blocker.wait()

        task = asyncio.create_task(worker())
        state.bind_turn(task, cancel_event)
        state.abort_turn(current_task=asyncio.current_task())
        await asyncio.sleep(0)

        self.assertEqual(state.turn_generation, 1)
        self.assertTrue(cancel_event.is_set())
        self.assertIsNone(state.current_cancel_event)
        self.assertIsNone(state.current_turn_task)
        self.assertTrue(task.cancelled())

    async def test_abort_from_bound_task_leaves_task_for_finalizer(self):
        state = SessionLifecycleState()
        cancel_event = asyncio.Event()
        current = asyncio.current_task()
        state.bind_turn(current, cancel_event)

        state.abort_turn(current_task=current)

        self.assertTrue(cancel_event.is_set())
        self.assertIs(state.current_turn_task, current)
        self.assertIsNone(state.current_cancel_event)
        self.assertTrue(state.clear_turn_if_current(current))
        self.assertIsNone(state.current_turn_task)

    async def test_clear_turn_does_not_clear_newer_owner(self):
        state = SessionLifecycleState()
        first = asyncio.create_task(asyncio.sleep(0))
        second = asyncio.create_task(asyncio.sleep(0))
        event = asyncio.Event()
        state.bind_turn(second, event)

        self.assertFalse(state.clear_turn_if_current(first))
        self.assertIs(state.current_turn_task, second)
        self.assertIs(state.current_cancel_event, event)

        await asyncio.gather(first, second)


if __name__ == "__main__":
    unittest.main()
