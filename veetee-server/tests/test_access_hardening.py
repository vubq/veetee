import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import WSServerHandshakeError, WSMsgType
from aiohttp.test_utils import TestClient, TestServer
import websockets
from config.settings import AppConfig
from core.access import WebSocketAccess
from core.management_store import ManagementStore
from core.providers.llm.unavailable import UnavailableLLM
from core.session import ClientSession
from core.tools.registry import validate_arguments, ToolValidationError
from core.tools.builtin.music_tool import ytdlp_resolve_url
from http_server import HttpServer
from server import VeeTeeServer
from tests.test_conversation_websocket_integration import FakeASR, CountingLLM, CountingTTS
from tests.test_memory import _MemorySession, _FakeWebSocket, _UnusedLLM, _UnusedTTS


class AccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = AppConfig()
        self.config.management.token = 'test-token'
        self.config.server.ws_max_sessions = 1
        self.config.server.ws_hello_timeout_seconds = 0.05
        self.sessions = {}
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = ManagementStore(self.tempdir.name + '/manager-state.json')
        self.access = WebSocketAccess(self.config.server, self.config.management, self.store)
        self.llm = CountingLLM()
        self.server = HttpServer(self.config, self.sessions, tts_engine=CountingTTS(),
                                 llm_engine=self.llm, websocket_access=self.access,
                                 management_store=self.store)
        self.client = TestClient(TestServer(self.server.app))
        await self.client.start_server()
        self.patch = patch.object(ClientSession, '_create_asr', lambda _: FakeASR())
        self.patch.start()

    async def asyncTearDown(self):
        await self.client.close()
        self.patch.stop()
        self.tempdir.cleanup()

    async def test_runtime_config_endpoint_uses_hot_applier(self):
        applied = []

        async def apply(changes):
            applied.append(dict(changes))
            values = self.store.update_runtime(changes)
            return {
                'values': values,
                'llm_reloaded': True,
                'sessions_updated': 2,
            }

        self.server.runtime_config_applier = apply
        response = await self.client.patch(
            '/api/runtime-config',
            headers={'X-Veetee-Management-Token': 'test-token'},
            json={'values': {'GROQ_API_KEY_7': 'gsk_endpoint_test'}},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertFalse(payload['restart_required'])
        self.assertTrue(payload['applied'])
        self.assertTrue(payload['llm_reloaded'])
        self.assertEqual(payload['sessions_updated'], 2)
        self.assertEqual(applied, [{'GROQ_API_KEY_7': 'gsk_endpoint_test'}])
        self.assertTrue(payload['values']['GROQ_API_KEY_7']['configured'])
        self.assertNotEqual(payload['values']['GROQ_API_KEY_7']['masked'], 'gsk_endpoint_test')

    async def test_missing_wrong_token_and_cross_origin_rejected(self):
        manager_cookie = self.access.manager_cookie()
        for headers, status in [({}, 401), ({'Authorization': 'Bearer wrong'}, 401),
                                ({'Cookie': f'veetee_session={manager_cookie}', 'Origin': 'https://evil.test'}, 403)]:
            with self.subTest(headers=headers):
                with self.assertRaises(WSServerHandshakeError) as caught:
                    await self.client.ws_connect('/ws', headers=headers)
                self.assertEqual(caught.exception.status, status)
        self.assertEqual(self.access.active, 0)
        self.assertEqual(self.sessions, {})

    async def test_unpaired_device_fails_closed(self):
        with self.assertRaises(WSServerHandshakeError):
            await self.client.ws_connect('/ws', headers={
                'Authorization': 'Bearer random-device-token',
                'Device-Id': 'aa:bb:cc:dd:ee:ff',
                'Client-Id': 'client-a',
            })

    async def test_login_cookie_and_hello(self):
        response = await self.client.post('/api/session', headers={'Authorization': 'Bearer test-token'})
        self.assertEqual(response.status, 200)
        cookie = response.cookies['veetee_session']
        self.assertTrue(cookie['httponly'])
        ws = await self.client.ws_connect('/ws', headers={'Cookie': f'veetee_session={cookie.value}'})
        await ws.send_json({'type': 'hello', 'version': 1})
        self.assertEqual((await ws.receive_json())['type'], 'hello')
        await ws.close()

    async def test_chat_before_hello_and_idle_release_capacity(self):
        manager_cookie = self.access.manager_cookie()
        for first in ({'type': 'chat', 'text': 'test'}, None):
            ws = await self.client.ws_connect('/ws', headers={'Cookie': f'veetee_session={manager_cookie}'})
            if first:
                await ws.send_json(first)
            msg = await ws.receive(timeout=1)
            self.assertEqual(msg.type, WSMsgType.CLOSE)
            await ws.close()
            await asyncio.sleep(0.01)
            self.assertEqual(self.access.active, 0)
            self.assertFalse(self.sessions)
        self.assertFalse(self.llm.calls)

    async def test_capacity_shared_with_standalone_and_auth_before_asr(self):
        standalone = VeeTeeServer.__new__(VeeTeeServer)
        standalone.config = self.config
        standalone.websocket_access = self.access
        standalone.active_sessions = self.sessions
        standalone.tts_engine = CountingTTS()
        standalone.llm_engine = self.llm
        standalone.response_audio_cache = None
        standalone.recent_turn_store = None
        async with websockets.serve(standalone.handle_ws_connection, '127.0.0.1', 0,
                                    process_request=standalone.authorize_ws) as listener:
            uri = f'ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}'
            with self.assertRaises(websockets.exceptions.InvalidStatus):
                async with websockets.connect(uri):
                    pass
            manager_cookie = self.access.manager_cookie()
            async with websockets.connect(
                uri, additional_headers={'Cookie': f'veetee_session={manager_cookie}'}
            ) as ws:
                await ws.send(json.dumps({'type': 'hello'}))
                self.assertEqual(json.loads(await ws.recv())['type'], 'hello')
                with self.assertRaises(WSServerHandshakeError) as caught:
                    await self.client.ws_connect(
                        '/ws', headers={'Cookie': f'veetee_session={manager_cookie}'}
                    )
                self.assertEqual(caught.exception.status, 503)

    async def test_owner_requires_authenticated_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            self.config.memory.durable_enabled = True
            self.config.memory.trusted_owner_id = 'owner-a'
            self.config.memory.database_path = directory + '/memory.sqlite3'
            guest = _MemorySession(_FakeWebSocket(), self.config, _UnusedTTS(), _UnusedLLM())
            owner = _MemorySession(_FakeWebSocket(), self.config, _UnusedTTS(), _UnusedLLM(),
                                   authenticated_owner_id='owner-a')
            self.assertIsNone(guest._memory_owner_id)
            self.assertIsNone(guest._memory_store)
            self.assertEqual(owner._memory_owner_id, 'owner-a')
            await guest.close()
            await owner.close()

    async def test_paired_credential_is_delivered_once_then_never_redisclosed(self):
        headers = {'Device-Id': 'device-a', 'Client-Id': 'client-a'}
        first = await self.client.get('/ota/', headers=headers)
        self.assertEqual(first.status, 200)
        code = (await first.json())['activation']['code']
        self.store.pair_code(code, 'default')

        issued = await self.client.get('/ota/', headers=headers)
        issued_payload = await issued.json()
        self.assertIn('websocket', issued_payload)
        token = issued_payload['websocket']['token']
        self.assertTrue(token)

        routine = await self.client.get('/ota/', headers=headers)
        routine_payload = await routine.json()
        self.assertNotIn('websocket', routine_payload)
        self.assertNotIn('activation', routine_payload)

    async def test_pending_pairing_is_bounded_and_identity_lengths_are_capped(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ManagementStore(
                directory + '/state.json',
                pending_max=2,
                pending_per_source_per_minute=1,
            )
            store.ensure_pending('device-a', 'client-a', {'board': 'x' * 999}, source_key='source-a')
            pending = store.pending_for_device('device-a', 'client-a')
            self.assertLessEqual(len(pending['metadata']['board']), store.MAX_METADATA_VALUE_LEN)
            with self.assertRaises(RuntimeError):
                store.ensure_pending('device-b', 'client-b', source_key='source-a')
            with self.assertRaises(ValueError):
                store.ensure_pending('d' * 129, 'client-c', source_key='source-b')

    async def test_device_owner_mapping_is_persisted_and_updateable(self):
        pending = self.store.ensure_pending('device-owner', 'client-owner', source_key='local')
        paired = self.store.pair_code(pending['code'], 'default', owner_id='owner-a')
        self.assertEqual(paired['owner_id'], 'owner-a')
        auth = self.store.authenticate_device(
            'device-owner',
            'client-owner',
            self.store.get_device('device-owner', 'client-owner')['credential'],
        )
        self.assertEqual(auth['device']['owner_id'], 'owner-a')
        updated = self.store.update_device('device-owner', 'client-owner', owner_id='owner-b')
        self.assertEqual(updated['owner_id'], 'owner-b')
        with self.assertRaises(ValueError):
            self.store.update_device('device-owner', 'client-owner', owner_id='x' * 129)

    async def test_health_reports_degraded_and_security_headers(self):
        self.server.runtime_readiness['llm_warm'] = False
        self.server.runtime_readiness['asr_ready'] = True
        response = await self.client.get('/health')
        payload = await response.json()
        self.assertEqual(payload['liveness'], 'alive')
        self.assertEqual(payload['readiness'], 'degraded')
        self.assertEqual(payload['status'], 'degraded')
        self.assertEqual(response.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(response.headers.get('X-Frame-Options'), 'DENY')
        self.assertIn("frame-ancestors 'none'", response.headers.get('Content-Security-Policy', ''))

    async def test_missing_llm_credentials_do_not_block_management_plane_construction(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ManagementStore(directory + '/state.json')
            config = AppConfig()
            config.llm.provider = 'groq'
            with (
                patch('server.ManagementStore', return_value=store),
                patch('server.VieneuLocalTTS', return_value=CountingTTS()),
                patch('server.build_engine_from_config', side_effect=ValueError('Groq key pool is empty')),
            ):
                server = VeeTeeServer(config)
            self.assertIsInstance(server.llm_engine, UnavailableLLM)
            self.assertFalse(server.runtime_readiness['llm_warm'])
            self.assertFalse(server.runtime_readiness['llm_retryable'])
            self.assertIn('llm_configuration_unavailable', server.runtime_readiness['llm_error'])
            await server.response_audio_cache.shutdown()

    async def test_runtime_groq_key_hot_reload_persists_without_restart(self):
        class WarmLLM:
            def __init__(self):
                self.model = 'qwen/qwen3.6-27b'
                self.warmed = False
                self.closed = False

            async def warmup(self):
                self.warmed = True

            async def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as directory:
            store = ManagementStore(directory + '/state.json')
            config = AppConfig()
            config.llm.provider = 'groq'
            replacement = WarmLLM()
            old_value = os.environ.pop('GROQ_API_KEY_1', None)
            try:
                with (
                    patch('server.ManagementStore', return_value=store),
                    patch('server.VieneuLocalTTS', return_value=CountingTTS()),
                    patch(
                        'server.build_engine_from_config',
                        side_effect=[ValueError('Groq key pool is empty'), replacement],
                    ),
                ):
                    server = VeeTeeServer(config)
                    result = await server.apply_runtime_config({
                        'GROQ_API_KEY_1': 'gsk_test_runtime_key',
                    })

                self.assertIs(server.llm_engine, replacement)
                self.assertTrue(replacement.warmed)
                self.assertTrue(server.runtime_readiness['llm_warm'])
                self.assertFalse(result['values']['GROQ_API_KEY_1'].get('masked') == 'gsk_test_runtime_key')
                self.assertTrue(result['values']['GROQ_API_KEY_1']['configured'])
                self.assertEqual(store.runtime_raw()['GROQ_API_KEY_1'], 'gsk_test_runtime_key')
                self.assertEqual(os.environ.get('GROQ_API_KEY_1'), 'gsk_test_runtime_key')
                self.assertFalse(result.get('restart_required', False))
                await server.response_audio_cache.shutdown()
                await replacement.close()
            finally:
                if old_value is None:
                    os.environ.pop('GROQ_API_KEY_1', None)
                else:
                    os.environ['GROQ_API_KEY_1'] = old_value

    async def test_runtime_latency_policy_hot_applies_without_restart(self):
        class RoutingAwareLLM(CountingLLM):
            def __init__(self):
                super().__init__()
                self.routing_updates = []

            async def configure_routing(self, **kwargs):
                self.routing_updates.append(dict(kwargs))

        with tempfile.TemporaryDirectory() as directory:
            store = ManagementStore(directory + '/state.json')
            config = AppConfig()
            routing_llm = RoutingAwareLLM()
            with (
                patch('server.ManagementStore', return_value=store),
                patch('server.VieneuLocalTTS', return_value=CountingTTS()),
                patch(
                    'server.build_engine_from_config',
                    return_value=routing_llm,
                ),
            ):
                server = VeeTeeServer(config)
                result = await server.apply_runtime_config({
                    'latency.target_first_audio_ms': 600,
                    'latency.first_token_timeout_ms': 1300,
                    'latency.total_turn_timeout_ms': 12000,
                    'asr.endpointing_ms': 225,
                    'asr.min_silence_duration_ms': 320,
                    'asr.speculative_inference_enabled': True,
                    'asr.speculative_start_silence_ms': 64,
                    'asr.speculative_min_confidence': 0.96,
                    'asr.speculative_llm_enabled': True,
                    'asr.speculative_llm_min_confidence': 0.97,
                    'asr.min_speech_duration_ms': 128,
                    'asr.speech_start_frames': 1,
                    'asr.pre_speech_pad_ms': 256,
                    'asr.vad_threshold': 0.45,
                    'asr.vad_threshold_low': 0.25,
                    'asr.vad_end_threshold': 0.8,
                    'tts.send_ahead_ms': 180,
                    'tts.stream_queue_max_chunks': 16,
                    'tts.first_audio_priority_boost': 7.5,
                    'tts.scheduler_aging_per_second': 3.0,
                    'tts.admission_timeout_ms': 4500,
                    'tts.speculative_prefetch_enabled': True,
                    'memory.lookup_timeout_ms': 18,
                    'conversation.history_turns': 8,
                    'tools.max_parallel_read_only': 3,
                    'tools.max_llm_rounds_per_turn': 3,
                    'llm.routing.headroom_pct': 12.5,
                    'llm.routing.max_attempts': 4,
                    'llm.routing.admission_wait_ms': 80.0,
                    'llm.routing.discovery_wait_ms': 900.0,
                    'llm.routing.discovery_max_inflight': 2,
                    'llm.routing.inflight_penalty_s': 0.6,
                    'llm.routing.latency_ewma_alpha': 0.4,
                    'llm.routing.latency_jitter_penalty': 0.9,
                })

            self.assertEqual(config.latency.target_first_audio_ms, 600)
            self.assertEqual(config.latency.first_token_timeout_ms, 1300)
            self.assertEqual(config.latency.total_turn_timeout_ms, 12000)
            self.assertEqual(config.asr.endpointing_ms, 225)
            self.assertEqual(config.asr.min_silence_duration_ms, 320)
            self.assertTrue(config.asr.speculative_inference_enabled)
            self.assertEqual(config.asr.speculative_start_silence_ms, 64)
            self.assertEqual(config.asr.speculative_min_confidence, 0.96)
            self.assertTrue(config.asr.speculative_llm_enabled)
            self.assertEqual(config.asr.speculative_llm_min_confidence, 0.97)
            self.assertEqual(config.asr.min_speech_duration_ms, 128)
            self.assertEqual(config.asr.speech_start_frames, 1)
            self.assertEqual(config.asr.pre_speech_pad_ms, 256)
            self.assertEqual(config.asr.vad_threshold, 0.45)
            self.assertEqual(config.asr.vad_threshold_low, 0.25)
            self.assertEqual(config.asr.vad_end_threshold, 0.8)
            self.assertEqual(config.tts.send_ahead_ms, 180)
            self.assertEqual(config.tts.stream_queue_max_chunks, 16)
            self.assertEqual(server.tts_engine.stream_queue_max_chunks, 16)
            self.assertEqual(config.tts.first_audio_priority_boost, 7.5)
            self.assertEqual(server.tts_engine.first_audio_priority_boost, 7.5)
            self.assertEqual(config.tts.scheduler_aging_per_second, 3.0)
            self.assertEqual(server.tts_engine.scheduler_aging_per_second, 3.0)
            self.assertEqual(config.tts.admission_timeout_ms, 4500)
            self.assertEqual(server.tts_engine.admission_timeout_ms, 4500)
            self.assertTrue(config.tts.speculative_prefetch_enabled)
            self.assertEqual(config.memory.lookup_timeout_ms, 18)
            self.assertEqual(config.conversation.history_turns, 8)
            self.assertEqual(config.tools.max_parallel_read_only, 3)
            self.assertEqual(config.tools.max_llm_rounds_per_turn, 3)
            self.assertEqual(config.llm.routing.headroom_pct, 12.5)
            self.assertEqual(config.llm.routing.max_attempts, 4)
            self.assertEqual(config.llm.routing.admission_wait_ms, 80.0)
            self.assertEqual(config.llm.routing.discovery_wait_ms, 900.0)
            self.assertEqual(config.llm.routing.discovery_max_inflight, 2)
            self.assertEqual(config.llm.routing.inflight_penalty_s, 0.6)
            self.assertEqual(config.llm.routing.latency_ewma_alpha, 0.4)
            self.assertEqual(config.llm.routing.latency_jitter_penalty, 0.9)
            self.assertEqual(len(routing_llm.routing_updates), 1)
            self.assertEqual(
                routing_llm.routing_updates[0]['discovery_wait_ms'], 900.0
            )
            self.assertEqual(
                routing_llm.routing_updates[0]['discovery_max_inflight'], 2
            )
            self.assertFalse(result['llm_reloaded'])
            self.assertEqual(
                store.runtime_raw()['latency.first_token_timeout_ms'], 1300
            )
            self.assertEqual(
                store.runtime_public()['latency.first_token_timeout_ms'], 1300
            )
            self.assertEqual(
                result['values']['latency.first_token_timeout_ms'], 1300
            )
            self.assertEqual(
                store.runtime_raw()['tools.max_llm_rounds_per_turn'], 3
            )
            self.assertEqual(
                store.runtime_raw()['llm.routing.discovery_wait_ms'], 900.0
            )
            self.assertEqual(
                store.runtime_raw()['llm.routing.discovery_max_inflight'], 2
            )
            self.assertEqual(
                store.runtime_raw()['asr.min_silence_duration_ms'], 320
            )
            self.assertTrue(
                store.runtime_raw()['asr.speculative_llm_enabled']
            )
            self.assertEqual(
                store.runtime_raw()['asr.speculative_llm_min_confidence'], 0.97
            )
            self.assertEqual(store.runtime_raw()['asr.pre_speech_pad_ms'], 256)
            self.assertEqual(store.runtime_raw()['asr.vad_threshold'], 0.45)
            self.assertEqual(store.runtime_raw()['asr.vad_end_threshold'], 0.8)
            self.assertEqual(
                store.runtime_raw()['tts.send_ahead_ms'], 180
            )
            self.assertEqual(
                store.runtime_raw()['tts.stream_queue_max_chunks'], 16
            )
            self.assertEqual(
                store.runtime_raw()['tts.first_audio_priority_boost'], 7.5
            )
            self.assertEqual(
                store.runtime_raw()['tts.scheduler_aging_per_second'], 3.0
            )
            self.assertEqual(
                store.runtime_raw()['tts.admission_timeout_ms'], 4500
            )
            self.assertTrue(
                store.runtime_raw()['tts.speculative_prefetch_enabled']
            )
            self.assertFalse(result.get('restart_required', False))
            await server.response_audio_cache.shutdown()

    async def test_runtime_speech_segmentation_hot_applies_without_restart(self):
        class SegmentationAwareLLM:
            def __init__(self):
                self.policies = []

            def set_speech_segmentation(self, policy):
                self.policies.append(policy)

        class ActiveSession:
            def __init__(self):
                self.session_id = "active-segmentation"
                self.llm_engine = SegmentationAwareLLM()

        with tempfile.TemporaryDirectory() as directory:
            store = ManagementStore(directory + '/state.json')
            config = AppConfig()
            with (
                patch('server.ManagementStore', return_value=store),
                patch('server.VieneuLocalTTS', return_value=CountingTTS()),
                patch(
                    'server.build_engine_from_config',
                    side_effect=ValueError('Groq key pool is empty'),
                ),
            ):
                server = VeeTeeServer(config)
                base_llm = SegmentationAwareLLM()
                session = ActiveSession()
                server.llm_engine = base_llm
                server.http_server.llm_engine = base_llm
                server.active_sessions[session.session_id] = session

                result = await server.apply_runtime_config({
                    'llm.speech_segmentation.first_clause_min_chars': 18,
                    'llm.speech_segmentation.first_clause_min_words': 3,
                    'llm.speech_segmentation.clause_target_chars': 140,
                    'llm.speech_segmentation.first_soft_cut_chars': 10,
                    'llm.speech_segmentation.first_soft_cut_min_words': 3,
                })

            self.assertEqual(
                config.llm.speech_segmentation.first_clause_min_chars, 18
            )
            self.assertEqual(
                config.llm.speech_segmentation.first_clause_min_words, 3
            )
            self.assertEqual(
                config.llm.speech_segmentation.clause_target_chars, 140
            )
            self.assertEqual(
                config.llm.speech_segmentation.first_soft_cut_chars, 10
            )
            self.assertEqual(
                config.llm.speech_segmentation.first_soft_cut_min_words, 3
            )
            self.assertEqual(base_llm.policies[-1].first_clause_min_chars, 18)
            self.assertEqual(
                session.llm_engine.policies[-1].first_clause_min_words, 3
            )
            self.assertEqual(
                store.runtime_raw()[
                    'llm.speech_segmentation.first_clause_min_chars'
                ],
                18,
            )
            self.assertEqual(
                store.runtime_raw()[
                    'llm.speech_segmentation.first_soft_cut_chars'
                ],
                10,
            )
            self.assertFalse(result.get('restart_required', False))
            await server.response_audio_cache.shutdown()

    async def test_runtime_speech_segmentation_rejects_invalid_policy_before_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ManagementStore(directory + '/state.json')
            config = AppConfig()
            with (
                patch('server.ManagementStore', return_value=store),
                patch('server.VieneuLocalTTS', return_value=CountingTTS()),
                patch(
                    'server.build_engine_from_config',
                    side_effect=ValueError('Groq key pool is empty'),
                ),
            ):
                server = VeeTeeServer(config)
                with self.assertRaisesRegex(ValueError, 'clause_min_chars'):
                    await server.apply_runtime_config({
                        'llm.speech_segmentation.clause_min_chars': 220,
                        'llm.speech_segmentation.clause_target_chars': 120,
                    })

            self.assertNotIn(
                'llm.speech_segmentation.clause_min_chars',
                store.runtime_raw(),
            )
            self.assertEqual(
                config.llm.speech_segmentation.clause_min_chars,
                80,
            )
            await server.response_audio_cache.shutdown()

    async def test_runtime_latency_policy_rejects_impossible_deadline_order(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ManagementStore(directory + '/state.json')
            config = AppConfig()
            with (
                patch('server.ManagementStore', return_value=store),
                patch('server.VieneuLocalTTS', return_value=CountingTTS()),
                patch(
                    'server.build_engine_from_config',
                    side_effect=ValueError('Groq key pool is empty'),
                ),
            ):
                server = VeeTeeServer(config)
                with self.assertRaisesRegex(
                    ValueError, 'first_token_timeout_ms must be >= target_first_audio_ms'
                ):
                    await server.apply_runtime_config({
                        'latency.target_first_audio_ms': 900,
                        'latency.first_token_timeout_ms': 500,
                    })

            self.assertNotIn(
                'latency.target_first_audio_ms', store.runtime_raw()
            )
            await server.response_audio_cache.shutdown()

    async def test_runtime_groq_key_hot_reload_rolls_back_failed_probe(self):
        class BadLLM:
            async def warmup(self):
                raise RuntimeError('401 invalid_api_key')

            async def close(self):
                return None

        with tempfile.TemporaryDirectory() as directory:
            store = ManagementStore(directory + '/state.json')
            config = AppConfig()
            config.llm.provider = 'groq'
            old_value = os.environ.pop('GROQ_API_KEY_2', None)
            try:
                with (
                    patch('server.ManagementStore', return_value=store),
                    patch('server.VieneuLocalTTS', return_value=CountingTTS()),
                    patch(
                        'server.build_engine_from_config',
                        side_effect=[ValueError('Groq key pool is empty'), BadLLM()],
                    ),
                ):
                    server = VeeTeeServer(config)
                    old_engine = server.llm_engine
                    with self.assertRaisesRegex(RuntimeError, 'invalid_api_key'):
                        await server.apply_runtime_config({
                            'GROQ_API_KEY_2': 'gsk_invalid_runtime_key',
                        })

                self.assertIs(server.llm_engine, old_engine)
                self.assertNotIn('GROQ_API_KEY_2', store.runtime_raw())
                self.assertNotIn('GROQ_API_KEY_2', os.environ)
                await server.response_audio_cache.shutdown()
            finally:
                if old_value is not None:
                    os.environ['GROQ_API_KEY_2'] = old_value

    async def test_llm_retry_classifier_stops_permanent_auth_failures(self):
        self.assertFalse(VeeTeeServer._llm_error_retryable(RuntimeError('401 invalid_api_key')))
        self.assertFalse(VeeTeeServer._llm_error_retryable(RuntimeError('Groq key pool is empty')))
        self.assertTrue(VeeTeeServer._llm_error_retryable(RuntimeError('temporary network timeout')))

    async def test_private_urls_rejected_before_subprocess(self):
        with patch('core.tools.builtin.music_tool.asyncio.create_subprocess_exec', new_callable=AsyncMock) as spawn:
            for value in ('http://127.0.0.1/private', 'https://example.test', '../file', 'abcdefghijk?x=1'):
                with self.assertRaises(ValueError):
                    await ytdlp_resolve_url(value)
            spawn.assert_not_called()


class SchemaTests(unittest.TestCase):
    def test_combinators_preserve_sibling_constraints(self):
        for combinator in ('anyOf', 'oneOf', 'allOf'):
            schema = {'type': 'object', 'properties': {'volume': {
                'type': 'integer', 'maximum': 100, combinator: [{'type': 'integer'}]}},
                'required': ['volume'], 'additionalProperties': False,
                combinator: [{'type': 'object'}]}
            validate_arguments(schema, {'volume': 50})
            for args in ({'volume': 999}, {}, {'volume': 50, 'extra': 1}):
                with self.subTest(combinator=combinator, args=args):
                    with self.assertRaises(ToolValidationError):
                        validate_arguments(schema, args)
