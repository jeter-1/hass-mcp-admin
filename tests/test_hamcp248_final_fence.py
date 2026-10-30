"""HAMCP-248: a full capture's final fence survives the idle websocket.

Live (beta.45): every full capture's final fence failed ``malformed_response``
with zero timeouts and 123 requests, while a 13-record resume succeeded. The
add-on's websocket is the Supervisor proxy's (``heartbeat=30``), which pings a
client idle for 30 s; the capture websocket idles through collection, and the
queued PING was refused as a malformed frame. Disposable loopback peers and
synthetic data only.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiohttp import web, ClientConnectionResetError, WSCloseCode, WSMsgType, WSMessage, WebSocketError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hass_mcp_engineering_beta'), str(ROOT / 'tests')]

from test_automation_baseline_capture import SECRET, fixture  # noqa: E402
from test_hamcp228_baseline_store import OWNER, Harness, capture_id  # noqa: E402
from ha_mcp_engineering.audit_baseline import capture_contracts as c  # noqa: E402
from ha_mcp_engineering.audit_baseline.capture_provider import BaselineCaptureProvider  # noqa: E402
from ha_mcp_engineering.audit_baseline.capture_service import BaselineCaptureService  # noqa: E402
from ha_mcp_engineering.audit_baseline.comparison import _shared_unknown_reasons  # noqa: E402
from ha_mcp_engineering.audit_baseline.models import FINGERPRINT_MODEL  # noqa: E402
from ha_mcp_engineering.audit_baseline.store import BaselineStore  # noqa: E402
from ha_mcp_engineering.clients.audit_baseline import BaselinePeer, BaselineReadClient  # noqa: E402

TOKEN = 'SYNTHETIC_HAMCP248_TOKEN'
VERSION = '2026.9.4'
KEEPALIVE = b'synthetic-keepalive'
COUNT = 116
# HAMCP-248 (review 1, R1-M1): each non-TEXT outcome of a capture websocket
# read, with its fixed reason; ``malformed_response`` is malformed data only.
FRAME_REASONS = {'close': 'connection_closed', 'transport': 'transport_error', 'binary': 'unexpected_frame',
                 'protocol': 'protocol_error', 'keepalive_flood': 'control_frame_limit',
                 'oversized': 'response_limit', 'silence': 'timeout', 'invalid_json': 'malformed_response'}
# The authentication attempt, five reads per fence and one per configuration.
FULL_REQUESTS = 1 + 2 * len(c.FENCE_READS) + COUNT


class FakeHomeAssistant:
    """A loopback Home Assistant behind a keepalive-sending websocket.

    The websocket answers the fixed capture commands; ``keepalive`` makes it
    PING the idle client when the first configuration read arrives, as the
    Supervisor proxy does 30 s into a full capture. It never pongs for the
    client (``autoping=False``), so every frame the client sends is recorded.
    ``respond`` may replace the reply to one command.
    ``handshake`` PINGs are sent before ``auth_required``, before any
    credential has been sent or accepted.
    """

    def __init__(self, data):
        self.data, self.keepalive, self.respond, self.handshake = data, None, None, ()
        self.events, self.socket = [], None
        self.kinds = {command['type']: kind for kind, command in c.COMMANDS.items()}

    async def handler(self, request):
        if request.path == '/api/websocket':
            ws = self.socket = web.WebSocketResponse(autoping=False)
            await ws.prepare(request)
            for payload in self.handshake:
                self.events.append(('ping', payload))
                await ws.ping(payload)
            await ws.send_json({'type': 'auth_required', 'ha_version': VERSION})
            message = await ws.receive()
            while message.type == WSMsgType.PONG:
                self.events.append(('pong', message.data))
                message = await ws.receive()
            if message.type != WSMsgType.TEXT:
                self.events.append(('frame', message.type))
                return ws
            if json.loads(message.data) != {'type': 'auth', 'access_token': TOKEN}:
                raise AssertionError('unexpected authentication')
            self.events.append(('auth', None))
            await ws.send_json({'type': 'auth_ok', 'ha_version': VERSION})
            async for message in ws:
                if message.type == WSMsgType.PONG:
                    self.events.append(('pong', message.data))
                    continue
                if message.type != WSMsgType.TEXT:
                    self.events.append(('frame', message.type))
                    break
                command = json.loads(message.data)
                kind = self.kinds[command['type']]
                self.events.append(('command', kind))
                if self.respond is not None and await self.respond(request, ws, command):
                    continue
                await ws.send_json({'id': command['id'], 'type': 'result', 'success': True,
                                    'result': self.data[kind]})
            return ws
        if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
            raise AssertionError('unexpected credential')
        if request.path == '/api/states':
            self.events.append(('rest', 'states'))
            return web.json_response(self.data['states'])
        if self.keepalive is not None:
            self.events.append(('ping', self.keepalive))
            await self.socket.ping(self.keepalive)
            self.keepalive = None
        config = self.data['configs'].get(request.path.rsplit('/', 1)[1])
        return web.json_response(config) if config is not None else web.Response(status=404)


class LoopbackHarness(Harness):
    async def asyncSetUp(self):
        self.home = FakeHomeAssistant(fixture(COUNT))
        app = web.Application()
        app.router.add_get('/{path:.*}', self.home.handler)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.client = BaselineReadClient(SimpleNamespace(
            api_url=f'http://127.0.0.1:{port}/api', ha_token=TOKEN,
            websocket_url=f'ws://127.0.0.1:{port}/api/websocket'))

    async def asyncTearDown(self):
        await self.runner.cleanup()

    def loopback_service(self):
        return BaselineCaptureService(
            BaselineCaptureProvider(self.client, self.core, known_secrets=(SECRET,)),
            BaselineStore(self.governance), wall=lambda: self.now)

    async def stored_capture(self, service):
        page = await service.capture(limit=100)
        report, _ = service.store.get(capture_id(page), OWNER, service.now_ms())
        return page, report


class FullCaptureFinalFenceTests(LoopbackHarness):
    async def test_a_keepalive_during_collection_no_longer_fails_the_final_fence(self):
        # The live failure: the PING waits on the idle websocket until the
        # final fence's first read. It is answered, and the fence completes.
        self.home.keepalive = KEEPALIVE
        service = self.loopback_service()
        page, report = await self.stored_capture(service)
        diagnostics, baseline = report['diagnostics'], report['baseline']
        self.assertEqual((diagnostics['final_fence'], diagnostics['final_fence_reason']), ('complete', None))
        self.assertEqual(baseline['installation']['status'], 'established')
        self.assertEqual(baseline['installation']['installation_id'], diagnostics['first_fence_installation_id'])
        self.assertEqual(page['baseline_header']['installation']['status'], 'established')
        self.assertEqual([source['kind'] for source in diagnostics['sources'] if source['fence'] == 'after'],
                         list(c.FENCE_READS))
        self.assertEqual(diagnostics['configuration_incomplete_count'], 0)
        self.assertEqual(diagnostics['requests'], FULL_REQUESTS)
        self.assertEqual(diagnostics['credential_screen']['withheld_record_count'], 0)
        # The PING arrived during collection and was answered, with its own
        # payload, by the final fence's first read; nothing else was sent.
        events = self.home.events
        ping, pong = events.index(('ping', KEEPALIVE)), events.index(('pong', KEEPALIVE))
        commands = [index for index, event in enumerate(events) if event[0] == 'command']
        self.assertEqual([events[index][1] for index in commands],
                         ['principal', 'entries', 'devices', 'registry'] * 2)
        self.assertLess(commands[3], ping)
        self.assertLess(ping, pong)
        self.assertEqual(events[pong - 1], ('command', 'principal'))
        self.assertEqual(pong, commands[4] + 1)
        self.assertEqual([event for event in events if event[0] in ('pong', 'frame')], [('pong', KEEPALIVE)])
        self.assertNotIn(TOKEN, json.dumps(report))

    async def test_the_resume_path_is_unchanged_over_the_real_transport(self):
        # A full capture with one configuration read timing out, then its
        # resume: only that record is read, and both fences complete.
        self.home.keepalive = KEEPALIVE
        original = self.home.data['configs']['7']
        service = self.loopback_service()
        real = BaselinePeer.configuration

        async def configuration(peer, identifier):
            if identifier == '7' and self.home.data['configs']['7'] is None:
                raise c.CaptureError('timeout')
            return await real(peer, identifier)
        with patch.object(BaselinePeer, 'configuration', configuration):
            self.home.data['configs']['7'] = None
            first, _ = await self.stored_capture(service)
            self.home.data['configs']['7'] = original
            self.home.events.clear()
            self.now += 60
            resumed = await service.capture(limit=100, resume_capture_id=capture_id(first))
        report, _ = service.store.get(capture_id(resumed), OWNER, service.now_ms())
        diagnostics = report['diagnostics']
        self.assertEqual(first['diagnostics']['final_fence'], 'complete')
        self.assertEqual((diagnostics['final_fence'], diagnostics['final_fence_reason']), ('complete', None))
        self.assertEqual(report['baseline']['resume']['resumed_count'], 1)
        self.assertEqual(report['baseline']['installation']['status'], 'established')
        self.assertEqual(diagnostics['requests'], 1 + 2 * len(c.FENCE_READS) + 1)
        self.assertEqual(self.home.events.count(('command', 'principal')), 2)


class FrameClassificationTests(LoopbackHarness):
    """Each frame the capture websocket can deliver has its own fixed outcome."""

    def authorize(self):
        return None

    async def principal(self):
        self.error = None
        async with self.client.capture(VERSION, self.authorize) as peer:
            try:
                return await peer.read('principal'), peer
            except c.CaptureError as exc:
                self.error = exc
                return exc.reason, peer

    def assert_content_free(self, error):
        # The fixed reason only: no close text, frame payload or exception text.
        self.assertEqual(error.details, {'reason': error.reason})
        for text in (SECRET, 'synthetic'):
            self.assertNotIn(text, str(error))
            self.assertNotIn(text, repr(error.details))

    def reply(self, *frames):
        async def respond(request, ws, command):
            for frame in frames:
                if frame == 'result':
                    await ws.send_json({'id': command['id'], 'type': 'result', 'success': True,
                                        'result': {'is_admin': True}})
                else:
                    await frame(request, ws)
            return True
        self.home.respond = respond

    def test_the_keepalive_and_capture_bounds_are_the_documented_ones(self):
        self.assertEqual(c.CONTROL_FRAMES, 4)
        self.assertEqual((c.COLLECTION_SECONDS, c.READ_SECONDS), (45, 5))

    async def test_keepalive_is_answered_and_the_read_succeeds(self):
        async def ping(request, ws):
            await ws.ping(KEEPALIVE)

        async def pong(request, ws):
            await ws.pong(b'unsolicited')
        for label, frames, answered in (
                ('ping', (ping,), [KEEPALIVE]),
                ('unsolicited_pong', (pong,), []),
                ('bounded_keepalive', (ping,) * c.CONTROL_FRAMES, [KEEPALIVE] * c.CONTROL_FRAMES)):
            with self.subTest(label):
                self.home.events.clear()
                self.reply(*frames, 'result')
                result, peer = await self.principal()
                self.assertEqual(result, {'is_admin': True})
                self.assertEqual(peer.requests, 2)
                await asyncio.sleep(0.05)
                self.assertEqual([event[1] for event in self.home.events if event[0] == 'pong'], answered)
                self.assertNotIn('frame', [event[0] for event in self.home.events[:-1]])

    async def test_keepalive_bytes_are_charged_to_the_capture_budget(self):
        async def ping(request, ws):
            await ws.ping(KEEPALIVE)
        self.reply('result')
        _, quiet = await self.principal()
        self.reply(ping, 'result')
        _, pinged = await self.principal()
        self.assertEqual(pinged.consumed - quiet.consumed, len(KEEPALIVE))
        self.reply(ping, 'result')
        with patch.object(c, 'TOTAL_BYTES', quiet.consumed - 1):
            reason, peer = await self.principal()
        self.assertEqual(reason, 'response_limit')

    async def test_each_failure_class_has_its_own_reason(self):
        async def ping(request, ws):
            await ws.ping(KEEPALIVE)

        async def close(request, ws):
            # The close code and text are not interpreted (review 1, R1-M1).
            await ws.close(code=WSCloseCode.MESSAGE_TOO_BIG, message=SECRET.encode())

        async def drop(request, ws):
            # An aborted TCP connection reaches the client as CLOSED; its
            # cause is not inferred.
            request.transport.abort()

        async def binary(request, ws):
            await ws.send_bytes(json.dumps({'type': 'result', 'data': SECRET}).encode())

        async def oversized(request, ws):
            await ws.send_str(json.dumps({'type': 'result', 'pad': SECRET * 64}))

        async def invalid_text(request, ws):
            # A TEXT frame that is not UTF-8: a websocket protocol violation.
            await ws._writer.send_frame(b'\xff\xfe' + SECRET.encode(), WSMsgType.TEXT)

        async def silence(request, ws):
            return None

        async def garbage(request, ws):
            await ws.send_str('{"type":"' + SECRET)
        cases = (
            ('server_close', (close,), {}, FRAME_REASONS['close']),
            ('transport_drop', (drop,), {}, FRAME_REASONS['close']),
            ('frame_size_bound', (oversized,), {'FRAME_BYTES': 1024}, FRAME_REASONS['oversized']),
            ('read_timeout', (silence,), {'READ_SECONDS': 0.2}, FRAME_REASONS['silence']),
            ('binary_frame', (binary,), {}, FRAME_REASONS['binary']),
            ('invalid_text_frame', (invalid_text,), {}, FRAME_REASONS['protocol']),
            ('invalid_json', (garbage,), {}, FRAME_REASONS['invalid_json']),
            ('keepalive_flood', (ping,) * (c.CONTROL_FRAMES + 1) + ('result',), {},
             FRAME_REASONS['keepalive_flood']))
        found = {}
        for label, frames, bounds, reason in cases:
            with self.subTest(label):
                self.home.events.clear()
                self.reply(*frames)
                with patch.multiple(c, **{'CONTROL_FRAMES': c.CONTROL_FRAMES, **bounds}):
                    reason_found, peer = await self.principal()
                found[label] = reason_found
                self.assertEqual(reason_found, reason)
                self.assert_content_free(self.error)
                self.assertEqual(peer.requests, 2)
                self.assertTrue(peer.websocket.closed)
                if label == 'keepalive_flood':
                    # Refused before the excess PING is answered.
                    await asyncio.sleep(0.05)
                    self.assertEqual(self.home.events.count(('pong', KEEPALIVE)), c.CONTROL_FRAMES)
        # Only a malformed body is malformed_response; the transport and
        # frame outcomes are each distinct.
        self.assertEqual([label for label, reason in found.items() if reason == 'malformed_response'],
                         ['invalid_json'])
        self.assertEqual(len(set(found.values())), len(found) - 1)

    async def test_a_cancelled_read_is_cancelled_not_classified(self):
        async def silence(request, ws):
            return None
        self.reply(silence)
        async with self.client.capture(VERSION, self.authorize) as peer:
            read = asyncio.create_task(peer.read('principal'))
            while ('command', 'principal') not in self.home.events:
                await asyncio.sleep(0.01)
            read.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await read

    async def test_local_frames_without_a_wire_form_keep_their_reason(self):
        # CLOSING, CLOSED and a non-protocol ERROR are produced by aiohttp itself.
        class Socket:
            def __init__(self, *frames, pong=None):
                self.frames, self.failure = list(frames), pong

            async def receive(self):
                return self.frames.pop(0)

            async def pong(self, data):
                raise self.failure
        ping = WSMessage(WSMsgType.PING, KEEPALIVE, None)
        for label, socket, reason in (
                ('close_with_too_big_code', Socket(WSMessage(WSMsgType.CLOSE, WSCloseCode.MESSAGE_TOO_BIG,
                                                             SECRET)), 'connection_closed'),
                ('closing', Socket(WSMessage(WSMsgType.CLOSING, None, None)), 'connection_closed'),
                ('closed', Socket(WSMessage(WSMsgType.CLOSED, None, None)), 'connection_closed'),
                ('error_runtime', Socket(WSMessage(WSMsgType.ERROR, RuntimeError(SECRET), None)),
                 'transport_error'),
                ('error_reset', Socket(WSMessage(WSMsgType.ERROR, ClientConnectionResetError(SECRET), None)),
                 'transport_error'),
                ('pong_reset', Socket(ping, pong=ClientConnectionResetError(SECRET)), 'transport_error'),
                ('pong_os_reset', Socket(ping, pong=ConnectionResetError(SECRET)), 'transport_error'),
                ('error_too_big', Socket(WSMessage(WSMsgType.ERROR, WebSocketError(
                    WSCloseCode.MESSAGE_TOO_BIG, SECRET), None)), 'response_limit'),
                ('error_protocol', Socket(WSMessage(WSMsgType.ERROR, WebSocketError(
                    WSCloseCode.PROTOCOL_ERROR, SECRET), None)), 'protocol_error'),
                ('continuation', Socket(WSMessage(WSMsgType.CONTINUATION, SECRET.encode(), None)),
                 'unexpected_frame')):
            with self.subTest(label):
                peer = BaselinePeer(self.client, VERSION, self.authorize)
                peer.websocket = socket
                with self.assertRaises(c.CaptureError) as found:
                    await peer.receive(c.FRAME_BYTES)
                self.assertEqual(found.exception.reason, reason)
                self.assert_content_free(found.exception)

    async def test_a_pong_that_times_out_or_is_cancelled_is_not_a_transport_error(self):
        # The transport-error wrapper does not swallow a timeout or a cancellation.
        class Socket:
            def __init__(self, failure):
                self.failure = failure

            async def receive(self):
                return WSMessage(WSMsgType.PING, KEEPALIVE, None)

            async def pong(self, data):
                raise self.failure
        for failure in (TimeoutError(), asyncio.CancelledError()):
            with self.subTest(type(failure).__name__):
                peer = BaselinePeer(self.client, VERSION, self.authorize)
                peer.websocket = Socket(failure)
                with self.assertRaises(type(failure)):
                    await peer.receive(c.FRAME_BYTES)

    async def test_retryability_and_public_code_follow_the_former_reason(self):
        for reason, former in (('connection_closed', 'source_unavailable'),
                               ('transport_error', 'source_unavailable'),
                               ('unexpected_frame', 'malformed_response'),
                               ('protocol_error', 'malformed_response'),
                               ('control_frame_limit', 'malformed_response')):
            with self.subTest(reason):
                error, previous = c.CaptureError(reason), c.CaptureError(former)
                self.assertEqual(error.reason, reason)
                self.assertEqual((error.code, error.retryable), (previous.code, previous.retryable))


class HandshakeKeepaliveTests(LoopbackHarness):
    """Bounded control handling also applies to the handshake reads, before authentication."""

    async def test_a_pre_authentication_ping_is_answered_before_the_credential_is_sent(self):
        self.home.data = fixture(3)
        self.home.handshake = (KEEPALIVE,)
        page, report = await self.stored_capture(self.loopback_service())
        self.assertEqual(report['diagnostics']['final_fence'], 'complete')
        self.assertEqual(self.home.events[:3], [('ping', KEEPALIVE), ('pong', KEEPALIVE), ('auth', None)])
        self.assertEqual(self.home.events.count(('pong', KEEPALIVE)), 1)

    async def test_a_pre_authentication_keepalive_flood_is_refused_before_the_credential(self):
        self.home.data = fixture(3)
        self.home.handshake = (KEEPALIVE,) * (c.CONTROL_FRAMES + 1)
        with self.assertRaises(c.CaptureError) as found:
            await self.loopback_service().capture(limit=100)
        self.assertEqual(found.exception.reason, 'control_frame_limit')
        await asyncio.sleep(0.05)
        self.assertNotIn(('auth', None), self.home.events)
        self.assertEqual(self.home.events.count(('pong', KEEPALIVE)), c.CONTROL_FRAMES)


class FinalFenceFrameReasonTests(LoopbackHarness):
    """A real final fence's frame outcome is the reason read back from the store."""

    def inject(self, action):
        principals = {'count': 0}

        async def respond(request, ws, command):
            if self.home.kinds[command['type']] != 'principal':
                return False
            principals['count'] += 1
            if principals['count'] < 2:
                return False
            await action(request, ws, command)
            return True
        self.home.respond = respond

    async def test_each_final_fence_outcome_is_stored_with_its_own_reason(self):
        async def result(ws, command, **extra):
            await ws.send_json({'id': command['id'], 'type': 'result', 'success': True,
                                'result': {'is_admin': True}, **extra})

        async def close(request, ws, command):
            await ws.close(code=WSCloseCode.MESSAGE_TOO_BIG, message=SECRET.encode())

        async def binary(request, ws, command):
            await ws.send_bytes(SECRET.encode())

        async def protocol(request, ws, command):
            await ws._writer.send_frame(b'\xff\xfe' + SECRET.encode(), WSMsgType.TEXT)

        async def keepalive_flood(request, ws, command):
            for _ in range(c.CONTROL_FRAMES + 1):
                await ws.ping(KEEPALIVE)
            await result(ws, command)

        async def oversized(request, ws, command):
            await result(ws, command, pad=SECRET * 4096)

        async def silence(request, ws, command):
            return None

        async def invalid_json(request, ws, command):
            await ws.send_str('{"type":"' + SECRET)
        self.home.data = fixture(3)
        actions = {'close': close, 'binary': binary, 'protocol': protocol, 'keepalive_flood': keepalive_flood,
                   'oversized': oversized, 'silence': silence, 'invalid_json': invalid_json}
        stored = {}
        for label, action in actions.items():
            with self.subTest(label):
                self.inject(action)
                bounds = {'FRAME_BYTES': 65_536} if label == 'oversized' else {}
                if label == 'silence':
                    bounds['READ_SECONDS'] = 0.5
                with patch.multiple(c, **{'COLLECTION_SECONDS': c.COLLECTION_SECONDS, **bounds}):
                    page, report = await self.stored_capture(self.loopback_service())
                diagnostics = report['diagnostics']
                stored[label] = diagnostics['final_fence_reason']
                self.assertEqual((diagnostics['final_fence'], diagnostics['final_fence_reason']),
                                 ('unavailable', FRAME_REASONS[label]))
                self.assertEqual(page['diagnostics']['final_fence_reason'], FRAME_REASONS[label])
                self.assertEqual(report['baseline']['installation']['status'], 'unestablished')
                self.assertNotIn(SECRET, json.dumps(report))
                self.assertNotIn(TOKEN, json.dumps(report))
                self.now += 700
        self.assertEqual(len(set(stored.values())), len(actions))

    async def test_a_cancelled_final_fence_is_cancelled_and_nothing_is_stored(self):
        waiting = asyncio.Event()

        async def silence(request, ws, command):
            waiting.set()
        self.home.data = fixture(3)
        self.inject(silence)
        service = self.loopback_service()
        capture = asyncio.create_task(service.capture(limit=100))
        await waiting.wait()
        capture.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await capture
        self.assertEqual(self.entries(service), [])


class FinalFenceReasonTests(Harness):
    """Every final-fence failure records its own reason; none is relabelled."""

    def failing(self, kind, reason):
        service, client = self.service(fixture(3))
        reads = {'count': 0}

        async def hook(read):
            if read == kind:
                reads['count'] += 1
                if reads['count'] == 2:
                    raise c.CaptureError(reason)
        client.hook = hook
        return service

    async def test_identity_and_inventory_read_failures_keep_their_reason(self):
        for kind, final, established in (('principal', 'unavailable', 'unestablished'),
                                         ('states', 'identity_only', 'established'),
                                         ('registry', 'identity_only', 'established')):
            # A fresh store per fence read: ten captures each, within its capacity.
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            self.governance = Path(directory.name)
            for reason in ('timeout', 'response_limit', 'source_unavailable', 'malformed_response',
                           'structural_limit', 'connection_closed', 'transport_error', 'unexpected_frame',
                           'protocol_error', 'control_frame_limit'):
                with self.subTest(kind=kind, reason=reason):
                    page = await self.capture(self.failing(kind, reason))
                    diagnostics = page['diagnostics']
                    self.assertEqual((diagnostics['final_fence'], diagnostics['final_fence_reason']),
                                     (final, reason))
                    self.assertEqual(page['baseline_header']['installation']['status'], established)
                    self.now += 700

    async def test_identity_and_authority_refusals_are_still_refused_not_recorded(self):
        for reason in ('access_denied', 'authority_unavailable', 'authority_drift', 'identity_unverified',
                       'identity_drift'):
            with self.subTest(reason):
                service = self.failing('principal', reason)
                error = await self.reason(self.capture(service))
                self.assertEqual(error.reason, reason)
                self.assertEqual(self.entries(service), [])


class CompareReasonTests(Harness):
    """A missing digest is the reason; it is not also a fingerprint-model mismatch."""

    @staticmethod
    def record(digest, model):
        return SimpleNamespace(identity_verified=True, configuration_status='readable' if digest else 'unreadable',
                               coverage=SimpleNamespace(comparison_complete=bool(digest)),
                               fingerprint_model=model, configuration_digest=digest)

    def reasons(self, earlier, later):
        return _shared_unknown_reasons(earlier, later, installation_mismatch=False,
                                       fingerprint_contract_compatible=True, inventory_scope_comparable=True)

    def test_a_missing_digest_names_only_the_true_reasons(self):
        readable = self.record('sha256:' + 'a' * 64, FINGERPRINT_MODEL)
        missing = self.record(None, None)
        self.assertEqual(self.reasons(missing, readable), [
            'earlier_configuration_coverage_incomplete', 'earlier_configuration_digest_missing',
            'earlier_configuration_not_readable'])
        self.assertEqual(self.reasons(readable, missing), [
            'later_configuration_coverage_incomplete', 'later_configuration_digest_missing',
            'later_configuration_not_readable'])
        # A model without its digest is not a model claim either.
        self.assertNotIn('record_fingerprint_model_mismatch',
                         self.reasons(self.record(None, 'other-model-v1'), readable))
        # Two digests of different models remain a mismatch.
        self.assertEqual(self.reasons(readable, self.record('sha256:' + 'a' * 64, 'other-model-v1')),
                         ['record_fingerprint_model_mismatch'])

    async def test_stored_compare_row_with_a_missing_earlier_digest(self):
        service, client = self.service(fixture(3))
        client.data['configs']['1'] = c.CaptureError('configuration_unavailable')
        first = await self.capture(service)
        client.data['configs']['1'] = fixture(3)['configs']['1']
        self.now += 4
        second = await self.capture(service)
        result = await service.compare(earlier_capture_id=capture_id(first), later_capture_id=capture_id(second))
        rows = {row['configuration_id']: row for row in result['records']}
        self.assertEqual(rows['1']['classification'], 'UNKNOWN')
        self.assertEqual(rows['1']['reasons'], [
            'earlier_configuration_coverage_incomplete', 'earlier_configuration_digest_missing',
            'earlier_configuration_not_readable'])
        self.assertEqual(rows['0']['classification'], 'UNCHANGED')

    async def test_recorded_compare_row_with_a_missing_capture_digest(self):
        service, client = self.service(fixture(3))
        client.data['configs']['1'] = c.CaptureError('configuration_unavailable')
        page = await self.capture(service)
        report, _ = service.store.get(capture_id(page), OWNER, service.now_ms())
        rows = [{'configuration_id': row['configuration_id'], 'fingerprint': row['configuration']['digest']
                 or 'sha256:' + 'b' * 64} for row in report['baseline']['records']]
        result = await service.compare(earlier_capture_id=capture_id(page), recorded_fingerprints=rows,
                                       fingerprint_model=FINGERPRINT_MODEL)
        by_id = {row['configuration_id']: row for row in result['records']}
        self.assertEqual(by_id['1']['reasons'], [
            'capture_configuration_coverage_incomplete', 'capture_configuration_digest_missing',
            'capture_configuration_not_readable'])
        # A caller's different model is still that row's reason.
        other = await service.compare(earlier_capture_id=capture_id(page), recorded_fingerprints=rows,
                                      fingerprint_model='other-model-v1')
        self.assertIn('fingerprint_model_mismatch', {row['configuration_id']: row for row in
                                                     other['records']}['1']['reasons'])


if __name__ == '__main__':
    unittest.main()
