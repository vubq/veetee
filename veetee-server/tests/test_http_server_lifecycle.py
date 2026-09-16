import unittest

from config.settings import AppConfig
from http_server import HttpServer


class HttpServerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_stop_owns_and_cleans_runner(self):
        config = AppConfig()
        config.server.host = "127.0.0.1"
        config.server.http_port = 0
        server = HttpServer(config, {})

        await server.start()
        self.assertIsNotNone(server._runner)
        self.assertIsNotNone(server._site)

        await server.stop()
        self.assertIsNone(server._runner)
        self.assertIsNone(server._site)

        # Cleanup is intentionally idempotent for partial-start/shutdown
        # paths in VeeTeeServer.start().
        await server.stop()


if __name__ == "__main__":
    unittest.main()
