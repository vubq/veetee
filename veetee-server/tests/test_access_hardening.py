import asyncio
import json
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
