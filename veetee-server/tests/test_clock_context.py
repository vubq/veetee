import json
import unittest
from unittest.mock import patch

from core.clock_context import CLOCK_CONTEXT_PREFIX, clock_context
from core.context_builder import ContextBuilder, ContextBudgetError
from core.tools.builtin.time_tool import clock_snapshot, time_descriptor


class ClockContextTests(unittest.TestCase):
    def test_fresh_snapshot_each_request(self):
        with patch("core.clock_context.clock_snapshot", side_effect=[{"time": "10:00"}, {"time": "10:01"}]):
            first = clock_context("UTC")
            second = clock_context("UTC")
        self.assertNotEqual(first, second)
        self.assertEqual(json.loads(second["content"][len(CLOCK_CONTEXT_PREFIX):]), {"time": "10:01"})

    def test_timezone_and_weekday_from_real_clock(self):
        snapshot = clock_snapshot("UTC")
        self.assertEqual(snapshot["timezone"], "UTC")
        self.assertIn(snapshot["weekday_iso"], range(1, 8))
        self.assertEqual(time_descriptor("UTC").handler({})["timezone"], "UTC")

    def test_clock_cannot_be_silently_trimmed(self):
        builder = ContextBuilder()
        clock = clock_context("UTC")
        messages = [clock, {"role": "user", "content": "old" * 2000},
                    {"role": "user", "content": "today?"}]
        fitted = builder.fit_to_budget(messages, system_messages=[], tools=[],
                                       max_context_tokens=300, reserve_output_tokens=20,
                                       chars_per_token=4)
        self.assertEqual(fitted, [clock, messages[-1]])
        with self.assertRaises(ContextBudgetError):
            builder.fit_to_budget(messages, system_messages=[], tools=[],
                                  max_context_tokens=20, reserve_output_tokens=10,
                                  chars_per_token=4)
