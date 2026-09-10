import os
import tempfile
import unittest

from aiohttp.test_utils import TestClient, TestServer

from config.settings import AppConfig
from core.providers.llm.groq_direct import GroqDirectLLM
from core.providers.llm.quota import QuotaLedger
from core.providers.llm.router import GroqRouter, RouteTarget
from http_server import HttpServer


class FakeTTS:
    def __init__(self, state_path=None):
        self.voice = "Trúc Ly"
        self.source_voice = "Xuân Vĩnh"
        self._state_path = state_path
        self._voices = [("Nữ Bắc tự nhiên", "Trúc Ly"),
                        ("Nam Bắc tự nhiên", "Xuân Vĩnh")]

    def available_voices(self):
        return list(self._voices)

    def set_voice(self, voice, persist=True):
        cleaned = (voice or "").strip()
        names = {name for _, name in self._voices}
        if not cleaned:
            raise ValueError("voice must not be empty")
        if cleaned not in names:
            raise ValueError(f"unknown voice: {cleaned!r}")
        self.voice = cleaned
        if persist and self._state_path:
            with open(self._state_path, "w", encoding="utf-8") as f:
                f.write(cleaned)
        return self.voice


class FakeLLM:
    def __init__(self, state_path=None):
        self.model = "qwen/qwen3.6-27b"
        self._models = ["qwen/qwen3.6-27b", "openai/gpt-oss-20b"]
        self._state_path = state_path

    def list_models(self):
        return list(self._models)

    def set_model(self, model, persist=True):
        cleaned = (model or "").strip()
        if not cleaned:
            raise ValueError("model must not be empty")
        if cleaned not in self._models:
            raise ValueError(f"model not allowed: {cleaned!r}")
        self.model = cleaned
        if persist and self._state_path:
            with open(self._state_path, "w", encoding="utf-8") as f:
                f.write(cleaned)
        return self.model


class ManagementVoiceModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.mkdtemp()
        config = AppConfig()
        config.management.token = "test-secret"
        self.tts = FakeTTS(state_path=os.path.join(self.tmpdir, "voice.txt"))
        self.llm = FakeLLM(state_path=os.path.join(self.tmpdir, "llm-model.txt"))
        self.http_server = HttpServer(
            config, {}, tts_engine=self.tts, llm_engine=self.llm,
            response_audio_cache=None,
        )
        self.client = TestClient(TestServer(self.http_server.app))
        await self.client.start_server()
        self.headers = {"X-Veetee-Management-Token": "test-secret"}

    async def asyncTearDown(self):
        await self.client.close()

    async def test_voice_endpoints_require_auth(self):
        for method, path in (("GET", "/api/voice"), ("POST", "/api/voice"),
                             ("GET", "/api/model"), ("POST", "/api/model")):
            if method == "GET":
                resp = await self.client.get(path)
            else:
                resp = await self.client.post(path, json={})
            self.assertEqual(resp.status, 401, path)
            await resp.release()

    async def test_get_voice_lists_options(self):
        resp = await self.client.get("/api/voice", headers=self.headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["voice"], "Trúc Ly")
        self.assertEqual(len(data["voices"]), 2)

    async def test_set_voice_validates_and_persists(self):
        resp = await self.client.post("/api/voice", headers=self.headers,
                                      json={"voice": "Nope"})
        self.assertEqual(resp.status, 400)
        resp = await self.client.post("/api/voice", headers=self.headers,
                                      json={"voice": "Xuân Vĩnh"})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["voice"], "Xuân Vĩnh")
        self.assertEqual(self.tts.voice, "Xuân Vĩnh")
        with open(os.path.join(self.tmpdir, "voice.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "Xuân Vĩnh")

    async def test_set_model_validates_and_persists(self):
        resp = await self.client.get("/api/model", headers=self.headers)
        data = await resp.json()
        self.assertEqual(data["model"], "qwen/qwen3.6-27b")
        self.assertIn("openai/gpt-oss-20b", data["models"])
        resp = await self.client.post("/api/model", headers=self.headers,
                                      json={"model": "evil/model"})
        self.assertEqual(resp.status, 400)
        resp = await self.client.post("/api/model", headers=self.headers,
                                      json={"model": "openai/gpt-oss-20b"})
        self.assertEqual(resp.status, 200)
        self.assertEqual((await resp.json())["model"], "openai/gpt-oss-20b")
        self.assertEqual(self.llm.model, "openai/gpt-oss-20b")
        with open(os.path.join(self.tmpdir, "llm-model.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "openai/gpt-oss-20b")


class GroqDirectModelSwitchTests(unittest.TestCase):
    def _engine(self, state_path=None):
        ledger = QuotaLedger({"gA": {"rpm": 100, "tpm": 100000}})
        router = GroqRouter(
            [RouteTarget(alias="A", api_key="kA", quota_group="gA",
                         base_url="https://x")],
            ledger)
        return GroqDirectLLM(
            router, model="qwen/qwen3.6-27b", base_prompt="Hi.",
            allowed_models=["qwen/qwen3.6-27b", "openai/gpt-oss-20b"],
            model_state_path=state_path)

    def test_list_models(self):
        engine = self._engine()
        self.assertEqual(engine.list_models(),
                         ["qwen/qwen3.6-27b", "openai/gpt-oss-20b"])

    def test_set_model_rejects_unknown(self):
        engine = self._engine()
        with self.assertRaises(ValueError):
            engine.set_model("evil/model")
        with self.assertRaises(ValueError):
            engine.set_model("  ")
        self.assertEqual(engine.model, "qwen/qwen3.6-27b")

    def test_set_model_persists_and_reloads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = os.path.join(tmpdir, "llm-model.txt")
            engine = self._engine(state_path=state)
            self.assertEqual(engine.set_model("openai/gpt-oss-20b"),
                             "openai/gpt-oss-20b")
            reloaded = self._engine(state_path=state)
            self.assertEqual(reloaded.model, "openai/gpt-oss-20b")

    def test_saved_unknown_model_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = os.path.join(tmpdir, "llm-model.txt")
            with open(state, "w", encoding="utf-8") as f:
                f.write("retired/model")
            engine = self._engine(state_path=state)
            self.assertEqual(engine.model, "qwen/qwen3.6-27b")


if __name__ == "__main__":
    unittest.main()
