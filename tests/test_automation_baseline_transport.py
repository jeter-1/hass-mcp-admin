"""Disposable loopback transport tests; no external service or production data."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from aiohttp import web

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'hass_mcp_engineering_beta'))
from ha_mcp_engineering.clients.audit_baseline import BaselineReadClient
from ha_mcp_engineering.audit_baseline import capture_contracts as c
from ha_mcp_engineering.request_context import begin_request,end_request

class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests=[];self.commands=[];self.auth_version='2026.9.4';self.frame=None
        self.body=b'{"id":"1","action":[]}';self.status=200;self.headers={};self.drop=False;self.hold=False
        self.authority=True
        async def handler(request):
            self.requests.append((request.method,request.path))
            if self.drop:
                request.transport.close();return web.Response()
            if request.path=='/api/websocket':
                ws=web.WebSocketResponse();await ws.prepare(request)
                await ws.send_json({'type':'auth_required','ha_version':self.auth_version})
                auth=await ws.receive_json()
                if auth!={'type':'auth','access_token':'SYNTHETIC_BASELINE_TOKEN'}:
                    raise AssertionError('unexpected authentication')
                await ws.send_json({'type':'auth_ok','ha_version':self.auth_version})
                async for message in ws:
                    if message.type!=web.WSMsgType.TEXT: break
                    command=json.loads(message.data);self.commands.append(command)
                    await ws.send_str(self.frame or json.dumps({'id':command['id'],'type':'result','success':True,'result':{'is_admin':True}}))
                return ws
            self.assertEqual(request.headers.get('Authorization'),'Bearer SYNTHETIC_BASELINE_TOKEN')
            if self.hold: await asyncio.sleep(.3)
            return web.Response(body=self.body,status=self.status,headers=self.headers)
        app=web.Application();app.router.add_get('/{path:.*}',handler)
        self.runner=web.AppRunner(app,access_log=None);await self.runner.setup()
        site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        self.client=BaselineReadClient(SimpleNamespace(api_url=f'http://127.0.0.1:{port}/api',websocket_url=f'ws://127.0.0.1:{port}/api/websocket',ha_token='SYNTHETIC_BASELINE_TOKEN'))
        self.telemetry,self.token=begin_request()
    async def asyncTearDown(self):
        end_request(self.token);await self.runner.cleanup()
    def authorize(self):
        if not self.authority: raise c.CaptureError('authority_unavailable')

    async def test_fixed_commands_rest_exact_bytes_and_one_connection(self):
        async with self.client.capture('2026.9.4',self.authorize) as peer:
            for kind in c.COMMANDS: await peer.read(kind)
            config=await peer.configuration('1')
            self.body=b'[]';self.assertEqual(await peer.read('states'),[])
            self.assertEqual(config,{'id':'1','action':[]})
            self.assertEqual(peer.requests,7)
        self.assertEqual(self.commands,[{'id':i,**command} for i,command in enumerate(c.COMMANDS.values(),1)])
        self.assertEqual(self.requests,[('GET','/api/websocket'),('GET','/api/config/automation/config/1'),('GET','/api/states')])
        self.assertEqual(self.telemetry.retry_count,0)
        self.assertEqual(self.telemetry.ha_active_requests,0)

    async def test_redirect_disconnect_and_timeout_never_retry(self):
        for kind, path in (('states', '/api/states'), ('configuration', '/api/config/automation/config/1')):
            for case, reason in (('redirect', 'source_unavailable'),
                                 ('disconnect', 'source_unavailable'), ('timeout', 'timeout')):
                with self.subTest(kind=kind, case=case):
                    self.status=200;self.headers={};self.drop=self.hold=False
                    async with self.client.capture('2026.9.4',self.authorize) as peer:
                        before=len(self.requests)
                        if case=='redirect': self.status=302;self.headers={'Location':'/forbidden'}
                        if case=='disconnect': self.drop=True
                        if case=='timeout': self.hold=True
                        with patch.object(c,'READ_SECONDS',.04):
                            with self.assertRaises(c.CaptureError) as found:
                                if kind == 'states':
                                    await peer.read('states')
                                else:
                                    await peer.configuration('1')
                        self.assertEqual(found.exception.reason, reason)
                        self.assertIs(found.exception.retryable, True)
                        self.assertEqual(self.requests[before:], [('GET', path)])
                        self.assertEqual(peer.requests, 2)
                        self.assertEqual(self.commands, [])
                        self.assertEqual(self.telemetry.retry_count, 0)
                        self.assertEqual(self.telemetry.ha_active_requests, 0)
                    self.assertTrue(peer.session.closed)
                    self.assertTrue(peer.websocket.closed)
                    self.drop=self.hold=False
        self.assertNotIn(('GET','/forbidden'),self.requests)

    async def test_rest_status_attribution_access_refusal_and_public_privacy(self):
        from ha_mcp_engineering.tool_framework import run_structured
        for kind, path, unavailable, retryable in (
            ('states', '/api/states', 'source_unavailable', True),
            ('configuration', '/api/config/automation/config/1', 'configuration_unavailable', False),
        ):
            for status in (404, 429, 500, 503, 401, 403):
                with self.subTest(kind=kind, status=status):
                    self.status = status
                    self.body = b'SYNTHETIC_PRIVATE_RESPONSE'
                    self.headers = {'X-Synthetic-Private': 'SYNTHETIC_PRIVATE_HEADER'}
                    before = len(self.requests)
                    async with self.client.capture('2026.9.4', self.authorize) as peer:
                        async def action():
                            if kind == 'states':
                                return await peer.read('states')
                            return await peer.configuration('1')
                        raw = await run_structured('synthetic_baseline_read', '', action)
                        response = json.loads(raw)
                        denied = status in (401, 403)
                        self.assertFalse(response['success'])
                        self.assertEqual(response['error'], 'CaptureError')
                        self.assertEqual(response['error_code'], 'analysis_unavailable')
                        self.assertEqual(response['message'], 'Analysis evidence is unavailable.')
                        self.assertEqual(response['details'], {
                            'reason': 'access_denied' if denied else unavailable,
                        })
                        self.assertIs(response['retryable'], False if denied else retryable)
                        self.assertEqual(response['timing']['retry_count'], 0)
                        self.assertEqual(response['timing']['upstream_request_count'], 0)
                        self.assertEqual(peer.requests, 2)
                        self.assertEqual(self.requests[before:], [
                            ('GET', '/api/websocket'), ('GET', path),
                        ])
                        self.assertEqual(self.commands, [])
                        self.assertEqual(self.telemetry.ha_active_requests, 0)
                        for private in ('SYNTHETIC_PRIVATE_RESPONSE', 'SYNTHETIC_PRIVATE_HEADER',
                                        'SYNTHETIC_BASELINE_TOKEN', path):
                            self.assertNotIn(private, raw)
                    self.assertTrue(peer.session.closed)
                    self.assertTrue(peer.websocket.closed)

    async def test_unsupported_selector_and_unsafe_path_zero_dispatch(self):
        async with self.client.capture('2026.9.4',self.authorize) as peer:
            before=len(self.requests)
            for value in ('..','.','x/y','x?z',True):
                with self.assertRaises(c.CaptureError): await peer.configuration(value)
            with self.assertRaises(c.CaptureError): await peer.read('service_call')
            self.assertEqual(len(self.requests),before)
            self.assertEqual(self.commands,[])

    async def test_malformed_result_envelopes_and_secret_errors(self):
        for frame in ('{"id":1,"id":1}', '{"type":"result","id":1,"success":true,"result":NaN}',
                      '{"type":"event","id":1}', '{"type":"result","id":1,"success":false,"error":{"message":"SYNTHETIC_PRIVATE"}}'):
            with self.subTest(frame=frame):
                self.frame=frame
                async with self.client.capture('2026.9.4',self.authorize) as peer:
                    with self.assertRaises(c.CaptureError) as found: await peer.read('principal')
                    self.assertNotIn('SYNTHETIC_PRIVATE',str(found.exception))

    async def test_wrong_auth_version_and_revoked_authority_prevent_reads(self):
        self.auth_version='2026.9.99'
        with self.assertRaises(c.CaptureError):
            async with self.client.capture('2026.9.4',self.authorize): pass
        self.assertEqual(self.commands,[])
        self.auth_version='2026.9.4'
        async with self.client.capture('2026.9.4',self.authorize) as peer:
            self.authority=False
            before=len(self.requests)
            with self.assertRaises(c.CaptureError): await peer.configuration('1')
            self.assertEqual(len(self.requests),before)

    async def test_rest_limit_compression_and_nonfinite_are_not_retryable(self):
        for kind in ('states', 'configuration'):
            for body, headers, reason in (
                (b'x'*300, {}, 'response_limit'),
                (b'{"id":"1","a":NaN}', {}, 'malformed_response'),
                (b'{}', {'Content-Encoding': 'gzip'}, 'malformed_response'),
            ):
                with self.subTest(kind=kind, reason=reason, headers=headers):
                    self.body,self.headers=body,headers
                    async with self.client.capture('2026.9.4',self.authorize) as peer:
                        with patch.object(c,'CONFIG_BYTES',256), patch.object(c,'FRAME_BYTES',256):
                            with self.assertRaises(c.CaptureError) as found:
                                if kind == 'states':
                                    await peer.read('states')
                                else:
                                    await peer.configuration('1')
                        self.assertEqual(found.exception.reason, reason)
                        self.assertIs(found.exception.retryable, False)
                        self.assertEqual(peer.requests,2)
                        self.assertEqual(self.telemetry.retry_count, 0)

    async def test_exhausted_budget_prevents_later_application_dispatch(self):
        async with self.client.capture('2026.9.4',self.authorize) as peer:
            with patch.object(c,'TOTAL_BYTES',peer.consumed+5):
                with self.assertRaises(c.CaptureError):await peer.configuration('1')
                count=len(self.requests)
                for _ in range(3):
                    with self.assertRaises(c.CaptureError):await peer.configuration('1')
                self.assertEqual(len(self.requests),count)

    async def test_unexpected_control_frame_fails_closed(self):
        # A completed authentication read is required before any application read;
        # the ordinary version mismatch path above proves zero command dispatch.
        async with self.client.capture('2026.9.4',self.authorize) as peer:
            self.frame='{"type":"ping"}'
            with self.assertRaises(c.CaptureError):await peer.read('principal')
            self.assertEqual(len(self.commands),1)
