import unittest

from core.context_builder import ContextBuilder
from core.memory.models import SessionMemoryFact


class CountingRetriever:
    def __init__(self):
        self.calls = []

    async def retrieve(self, **kwargs):
        self.calls.append(dict(kwargs))
        return []


class ContextPrefetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_snapshot_is_reused_without_second_lookup(self):
        retriever = CountingRetriever()
        builder = ContextBuilder(retriever, lookup_timeout_ms=50)
        memory = [SessionMemoryFact(id="session:1", value="thích cà phê", revision=2)]
        prefetch = await builder.prefetch_memory(
            query="Tôi thích gì?",
            owner_id="owner-a",
            session_memory=memory,
        )
        messages = [{"role": "user", "content": "Tôi thích gì?"}]
        built = await builder.build(
            messages,
            query="Tôi thích gì?",
            owner_id="owner-a",
            session_memory=memory,
            prefetched=prefetch,
        )
        self.assertEqual(len(retriever.calls), 1)
        self.assertTrue(builder.last_lookup["speculative_reused"])
        self.assertTrue(any(item.get("role") == "system" for item in built))

    async def test_changed_query_forces_fresh_lookup(self):
        retriever = CountingRetriever()
        builder = ContextBuilder(retriever, lookup_timeout_ms=50)
        prefetch = await builder.prefetch_memory(
            query="câu cũ",
            owner_id="owner-a",
            session_memory=[],
        )
        await builder.build(
            [{"role": "user", "content": "câu mới"}],
            query="câu mới",
            owner_id="owner-a",
            session_memory=[],
            prefetched=prefetch,
        )
        self.assertEqual(len(retriever.calls), 2)
        self.assertFalse(builder.last_lookup["speculative_reused"])

    async def test_changed_memory_revision_forces_fresh_lookup(self):
        retriever = CountingRetriever()
        builder = ContextBuilder(retriever, lookup_timeout_ms=50)
        before = [SessionMemoryFact(id="session:1", value="A", revision=1)]
        prefetch = await builder.prefetch_memory(
            query="nhắc lại",
            owner_id="owner-a",
            session_memory=before,
        )
        after = [SessionMemoryFact(id="session:1", value="B", revision=2)]
        await builder.build(
            [{"role": "user", "content": "nhắc lại"}],
            query="nhắc lại",
            owner_id="owner-a",
            session_memory=after,
            prefetched=prefetch,
        )
        self.assertEqual(len(retriever.calls), 2)
        self.assertFalse(builder.last_lookup["speculative_reused"])

    async def test_changed_owner_forces_fresh_lookup(self):
        retriever = CountingRetriever()
        builder = ContextBuilder(retriever, lookup_timeout_ms=50)
        prefetch = await builder.prefetch_memory(
            query="nhắc lại",
            owner_id="owner-a",
            session_memory=[],
        )
        await builder.build(
            [{"role": "user", "content": "nhắc lại"}],
            query="nhắc lại",
            owner_id="owner-b",
            session_memory=[],
            prefetched=prefetch,
        )
        self.assertEqual(len(retriever.calls), 2)
        self.assertFalse(builder.last_lookup["speculative_reused"])


if __name__ == "__main__":
    unittest.main()
