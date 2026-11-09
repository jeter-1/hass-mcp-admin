"""HAMCP-319 Phase 2: governed dashboard surfaces at 9.1.0 parity with 8.5.0.

Design sections 6.3 (F4 dashboard model), 7 (governed dashboard surfaces),
7.1 (internal dashboard-guide read) and 8.1 item 4 ("No config found" fails
closed). The real pinned MCP ClientSession runs over in-memory streams against
a synthetic peer, behind the real dashboard provider, gateway, governance
service and F3 runtime. The 9.1 peer answers with structured-content shapes
transcribed from the pinned 9.1.0 source (see
tests/fixtures/ha_mcp_9_1_0/dashboard/provenance.json); the 8.5 peer answers
with the shapes the retained 8.5 tests already use. No endpoint is contacted:
the 9.1 transport is configured with a synthetic strict_full value only so
that endpoint admission takes its 9.1 path, and the SDK HTTP transport is
replaced.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

import anyio
from mcp import types
from mcp.shared.message import SessionMessage

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hass_mcp_engineering_beta'), str(ROOT / 'tests')]

from ha_mcp_engineering.audit import AuditLogger
from ha_mcp_engineering.clients import mcp as mcp_client
from ha_mcp_engineering.clients.mcp import (
    BEST_PRACTICES_GUIDE_ARGUMENTS, MAX_UPSTREAM_CONTENT_CHARS,
    DashboardTransportError, McpDashboardTransport,
)
from ha_mcp_engineering.dashboard_analysis import contracts as analysis_contracts
from ha_mcp_engineering.errors import DashboardProviderError, GovernanceError
from ha_mcp_engineering.f3_dashboard.gateway import DashboardExecutionGateway
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
from ha_mcp_engineering.governance.service import ChangeGovernanceService
from ha_mcp_engineering.governance.storage import ChangePlanRepository
from ha_mcp_engineering.providers import upstream_dashboard
from ha_mcp_engineering.providers.upstream_dashboard import UpstreamDashboardProvider
from ha_mcp_engineering.request_context import begin_request, end_request
from test_dashboard_update_mvp import (
    _UnusedConfigurationGateway, _unrelated_lifecycle_provider_identity,
)

GETTER, GUIDE, SETTER = (
    'ha_config_get_dashboard', 'ha_get_skill_guide', 'ha_config_set_dashboard')
URL_PATH = 'operations'
SKILL = 'home-assistant-best-practices'
GUIDE_FILE = 'references/dashboard-guide.md'
FIXTURES = ROOT / 'tests/fixtures/ha_mcp_9_1_0/dashboard'
ENVELOPES = json.loads((FIXTURES / 'envelopes.json').read_text(encoding='utf-8'))
GUIDE_BYTES = (FIXTURES / 'dashboard-guide.md').read_bytes()
GUIDE_SHA256 = 'b4d42e9383fe929ecc87fb52150ceb3222173b528b1cf9f8663218c65544d293'
GUIDE_GIT_BLOB = 'bafb545627ca4aac44eb439400f30d77f24eacf8'
# Synthetic strict_full value (design section 3.3); nothing is contacted.
STRICT_URL = 'http://synthetic.invalid/base/catalog/full'
URLS = {'9.1.0': STRICT_URL, '8.5.0': ''}
KEY_PATTERN = re.compile(r'I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-[0-9a-f]{8}')
KEY_91 = 'I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-9a1b2c3d'
KEY_85 = 'I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-0123abcd'
VERSIONS = ('9.1.0', '8.5.0')


def catalog(version):
    folder = ROOT / 'docs/evidence/upstream-read-compatibility'
    raw = json.loads((folder / f'ha-mcp-{version}.json').read_text())
    review = json.loads((folder / f'ha-mcp-{version}-contract-review.json').read_text())
    by_name = {tool['name']: tool for tool in raw['tools']}
    return [deepcopy(by_name[name])
            for name in review['runtime_catalog']['runtime_tool_order']]


def compute_config_hash_91(config):
    # Mirrors pinned 9.1.0 utils/config_hash.py compute_config_hash; kept
    # independent of Engineering's own hash helpers.
    text = json.dumps(config, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def fill(template, **values):
    """Replace "<name>" placeholders in a deep copy of an envelope template."""
    if isinstance(template, dict):
        return {key: fill(value, **values) for key, value in template.items()}
    if isinstance(template, list):
        return [fill(value, **values) for value in template]
    if isinstance(template, str) and template.startswith('<') and template.endswith('>'):
        name = template[1:-1]
        if name in values:
            return deepcopy(values[name])
    return deepcopy(template)


def ack_line(key):
    return ENVELOPES['strict_bps_ack_line'].replace('<key>', key)


def text_result(value, *, is_error=False):
    return {'content': [{'type': 'text', 'text': json.dumps(value)}], 'isError': is_error}


def error_envelope(code, message, **context):
    # Shaped like ha-mcp create_error_response (9.1.0 errors.py:261-327).
    return {'success': False, 'error': {'code': code, 'message': message}, **context}


def listed(url_path=URL_PATH, mode='storage'):
    return {'id': url_path, 'url_path': url_path, 'mode': mode,
            'title': url_path.title(), 'require_admin': False, 'show_in_sidebar': True}


class DashboardPeer:
    """Version-aware synthetic ha-mcp peer behind the real ClientSession."""

    def __init__(self, version):
        self.version = version
        self.tools = catalog(version)
        self.calls = []
        self.configuration = {'title': 'Before', 'views': [{'title': 'Home', 'cards': []}]}
        self.rows = [listed()]
        self.list_payload = None   # callable(args) -> payload, overrides rows
        self.get_payload = None    # callable(args) -> (payload, is_error)
        self.setter_payload = None  # callable(args) -> (payload, is_error)
        self.native_write = False
        self.key = KEY_91 if version == '9.1.0' else KEY_85

    def names(self):
        return [name for name, _ in self.calls]

    def args_for(self, name):
        return [args for called, args in self.calls if called == name]

    # -- replies -----------------------------------------------------------
    def _list(self, args):
        if self.list_payload is not None:
            return self.list_payload(args), False
        rows = deepcopy(self.rows)
        if self.version == '9.1.0':
            return fill(ENVELOPES['list'], rows=rows, count=len(rows)), False
        return {'success': True, 'action': 'list', 'count': len(rows), 'dashboards': rows}, False

    def _get(self, args):
        if self.get_payload is not None:
            return self.get_payload(args)
        config = deepcopy(self.configuration)
        if self.version == '9.1.0':
            return fill(ENVELOPES['get_stored_config'], config=config,
                        config_hash=compute_config_hash_91(config),
                        config_size_bytes=len(json.dumps(config))) | {
                            'url_path': args['url_path']}, False
        return {'success': True, 'action': 'get', 'url_path': args['url_path'],
                'config': config, 'config_hash': compute_config_hash_91(config)}, False

    def _guide(self, args):
        if self.version == '9.1.0':
            # Exact 9.1 input schema: only ``file`` (additionalProperties false).
            if set(args) - {'file'} or args.get('file', 'SKILL.md') != GUIDE_FILE:
                return None
            structured = fill(ENVELOPES['skill_guide_reference_file'])
            structured['content'] = (
                ack_line(self.key) + '\n\n' + GUIDE_BYTES.decode('utf-8'))
            return {'content': [{'type': 'text', 'text': json.dumps(structured, indent=2)}],
                    'structuredContent': structured, 'isError': False}
        if args != {'skill': SKILL, 'file': GUIDE_FILE}:
            return None
        guide = '# Dashboard guide\n' + self.key
        return {'content': [{'type': 'text', 'text': guide}],
                'structuredContent': {'success': True, 'content': guide}, 'isError': False}

    def _set(self, args):
        if self.setter_payload is not None:
            return self.setter_payload(args)
        if args.get('BestPracticeKey') != self.key:
            return error_envelope('VALIDATION_FAILED', 'Synthetic strict BPS refusal',
                                  action='set'), True
        self.configuration = deepcopy(args['config'])
        if self.version == '9.1.0':
            template = 'set_update_native' if self.native_write else 'set_update'
            return fill(ENVELOPES[template],
                        config_hash=compute_config_hash_91(self.configuration)) | {
                            'url_path': args['url_path'], 'dashboard_id': args['url_path']}, False
        return {'success': True, 'action': 'update', 'url_path': args['url_path'],
                'dashboard_created': False, 'config_updated': True,
                'metadata_updated': False}, False

    async def serve(self, incoming, outgoing):
        async with incoming, outgoing:
            async for message in incoming:
                request = message.message.root
                if not isinstance(request, types.JSONRPCRequest):
                    continue
                if request.method == 'initialize':
                    result = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}},
                              'serverInfo': {'name': 'ha-mcp', 'version': self.version}}
                elif request.method == 'tools/list':
                    result = {'tools': self.tools}
                elif request.method == 'tools/call':
                    name, args = request.params['name'], request.params.get('arguments') or {}
                    self.calls.append((name, deepcopy(args)))
                    if name == GETTER and args.get('list_only') is True:
                        payload, is_error = self._list(args)
                        result = text_result(payload, is_error=is_error)
                    elif name == GETTER:
                        payload, is_error = self._get(args)
                        result = text_result(payload, is_error=is_error)
                    elif name == GUIDE:
                        result = self._guide(args)
                        if result is None:
                            result = {'content': [{'type': 'text', 'text':
                                      'Synthetic input validation error'}], 'isError': True}
                    elif name == SETTER:
                        payload, is_error = self._set(args)
                        result = text_result(payload, is_error=is_error)
                    else:
                        raise AssertionError('Unexpected synthetic dispatch')
                else:
                    raise AssertionError('Unexpected MCP method')
                await outgoing.send(SessionMessage(types.JSONRPCMessage(
                    types.JSONRPCResponse(jsonrpc='2.0', id=request.id, result=result))))

    @asynccontextmanager
    async def connect(self, url, **kwargs):
        assert url == URLS[self.version]
        send_to_client, read = anyio.create_memory_object_stream(10)
        write, read_from_client = anyio.create_memory_object_stream(10)
        async with anyio.create_task_group() as group:
            group.start_soon(self.serve, read_from_client, send_to_client)
            try:
                yield read, write, lambda: 'synthetic-session'
            finally:
                group.cancel_scope.cancel()


TITLE_PATCH = [{'operation_id': 'rename', 'operation': 'replace',
                'path': '/title', 'value': 'After'}]
VIEWS_SET = [{'operation_id': 'set-views', 'operation': 'replace', 'path': '/views',
              'value': [{'title': 'Replaced', 'cards': []}]}]


class Harness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.telemetry, token = begin_request('synthetic-HAMCP-319-dashboard')
        self.addCleanup(end_request, token)

    def provider(self, peer):
        provider = UpstreamDashboardProvider()
        provider._transport = McpDashboardTransport(
            URLS[peer.version], timeout_seconds=3, client_version='synthetic')
        self.enterContext(patch.object(mcp_client, 'streamablehttp_client', peer.connect))
        return provider

    async def stack(self, peer, name='plans'):
        provider = self.provider(peer)
        gateway = DashboardExecutionGateway(provider, response_limit=60_000)
        storage = self.root / name
        service = ChangeGovernanceService(
            ChangePlanRepository(storage), _UnusedConfigurationGateway(),
            AuditLogger(str(self.root / f'{name}-audit.jsonl'), 'synthetic-access-secret'),
            dashboard_gateway=gateway,
            provider_identity_reader=_unrelated_lifecycle_provider_identity)
        runtime = F3RuntimeIntegration(
            service=service, storage_root=str(storage),
            configuration_gateway=_UnusedConfigurationGateway(), backup_gateway=None,
            lifecycle_gateway=None, dashboard_gateway=gateway,
            provider_identity_reader=_unrelated_lifecycle_provider_identity, retention_days=90)
        service.f3_runtime = runtime
        await runtime.recover_once('startup')
        return provider, service, runtime

    async def create(self, service, operations, url_path=URL_PATH):
        return await service.create_dashboard_update_plan(
            title='Synthetic dashboard update', description='Bounded existing-dashboard update.',
            url_path=url_path, patch_operations=deepcopy(operations), expiration_minutes=30)

    async def approve(self, service, created):
        pending = service.approve(created['plan_id'], created['plan_hash'])
        _review, csrf = await service.issue_external_csrf(created['plan_id'], pending['challenge_id'])
        await service.decide_external_approval(
            plan_id=created['plan_id'], challenge_id=pending['challenge_id'],
            expected_plan_hash=created['plan_hash'], approval_kind=pending['approval_kind'],
            approval_action=pending['approval_action'], csrf_nonce=csrf, decision='approve',
            approver_principal='home_assistant_admin_ingress:synthetic-reviewer')

    async def governed_update(self, peer, operations, name):
        _provider, service, runtime = await self.stack(peer, name)
        created = await self.create(service, operations)
        await self.approve(service, created)
        result = await service.apply(created['plan_id'], created['plan_hash'])
        return service, runtime, created, result

    def balanced(self):
        self.assertEqual(self.telemetry.upstream_active_requests, 0)

    async def assert_refused_everywhere(self, peer, name, url_path=URL_PATH):
        """Set (replace /views) and patch (replace /title) are both refused
        before any guide read or setter call. There is no governed create;
        absence never unlocks one (see the create-refusal tests)."""
        _provider, service, _runtime = await self.stack(peer, name)
        for label, operations in (('set', VIEWS_SET), ('patch', TITLE_PATCH)):
            with self.subTest(operation=label):
                with self.assertRaises((GovernanceError, DashboardProviderError)):
                    await self.create(service, operations, url_path=url_path)
        self.assertNotIn(SETTER, peer.names())
        self.assertNotIn(GUIDE, peer.names())
        self.assertEqual(service.list_plans(limit=50)['plans'], [])
        self.balanced()


# ---------------------------------------------------------------------------
# Section 7.1: the internal dashboard-guide read.
# ---------------------------------------------------------------------------
class InternalGuideRead(Harness):
    def test_pinned_guide_fixture_identity(self):
        self.assertEqual(hashlib.sha256(GUIDE_BYTES).hexdigest(), GUIDE_SHA256)
        blob = hashlib.sha1(b'blob %d\0' % len(GUIDE_BYTES) + GUIDE_BYTES).hexdigest()
        self.assertEqual(blob, GUIDE_GIT_BLOB)
        self.assertNotIn(b'\r', GUIDE_BYTES)

    async def test_91_dashboard_guide_internal_read(self):
        peer = DashboardPeer('9.1.0')
        provider = self.provider(peer)
        exchange = await provider._transport.execute_best_practices_read(
            provider._validate_write_handshake)
        self.assertEqual(peer.calls, [(GUIDE, {'file': GUIDE_FILE})])
        encoded = json.dumps(exchange.call_result, default=str)
        self.assertLessEqual(len(encoded), MAX_UPSTREAM_CONTENT_CHARS)
        structured = exchange.call_result['structuredContent']
        self.assertIs(structured['success'], True)
        self.assertEqual(structured['file'], GUIDE_FILE)
        self.assertEqual(structured['uri'], f'skill://{SKILL}/{GUIDE_FILE}')
        # Declared acknowledgement normalization: exactly one leading
        # acknowledgement line, then one blank line, then the file.
        first, separator, body = structured['content'].partition('\n\n')
        self.assertEqual(separator, '\n\n')
        keys = KEY_PATTERN.findall(first)
        self.assertEqual(keys, [peer.key])
        self.assertEqual(first, ack_line(keys[0]))
        self.assertEqual(body.encode('utf-8'), GUIDE_BYTES)
        self.assertEqual(hashlib.sha256(body.encode('utf-8')).hexdigest(), GUIDE_SHA256)
        # The text channel carries the same object.
        self.assertEqual(json.loads(exchange.call_result['content'][0]['text']), structured)
        # Strict BPS: the provider extracts the published receipt.
        self.assertEqual(await provider.best_practices_acknowledgement_key(), peer.key)
        self.assertEqual(peer.args_for(GUIDE), [{'file': GUIDE_FILE}] * 2)
        # ... and a governed dashboard write proceeds with that receipt.
        write_peer = DashboardPeer('9.1.0')
        _s, _r, _c, result = await self.governed_update(write_peer, TITLE_PATCH, 'guide-91')
        self.assertEqual(result['task_state'], 'succeeded_verified', result)
        self.assertEqual(write_peer.args_for(SETTER)[0]['BestPracticeKey'], write_peer.key)
        self.assertEqual(write_peer.args_for(GUIDE), [{'file': GUIDE_FILE}])
        self.balanced()

    async def test_91_dashboard_guide_rejects_other_arguments(self):
        cases = (
            {},
            {'file': GUIDE_FILE},
            {'skill': SKILL},
            {'file': 'SKILL.md'},
            {'skill': SKILL, 'file': 'SKILL.md'},
            {'skill': SKILL, 'file': 'references/dashboard-cards.md'},
            {'skill': SKILL, 'file': '../dashboard-guide.md'},
            {'skill': 'other', 'file': GUIDE_FILE},
            {'skill': SKILL, 'file': GUIDE_FILE, 'extra': True},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                peer = DashboardPeer('9.1.0')
                provider = self.provider(peer)
                with self.assertRaises(DashboardTransportError) as caught:
                    await provider._transport._run(
                        tool_name=GUIDE, arguments=deepcopy(arguments),
                        capability_validator=provider._validate_write_handshake)
                self.assertEqual(caught.exception.category, 'prohibited_argument')
                self.assertEqual(peer.calls, [])
        self.balanced()

    async def test_85_dashboard_guide_read_unchanged(self):
        peer = DashboardPeer('8.5.0')
        provider = self.provider(peer)
        self.assertEqual(await provider.best_practices_acknowledgement_key(), peer.key)
        self.assertEqual(peer.calls, [(GUIDE, dict(BEST_PRACTICES_GUIDE_ARGUMENTS))])
        self.assertEqual(peer.calls, [(GUIDE, {'skill': SKILL, 'file': GUIDE_FILE})])
        # No 9.1 rewrite row is consulted for an 8.5 session.
        self.assertNotIn(('8.5.0', GUIDE), mcp_client._BEST_PRACTICES_ARGUMENT_REWRITES)
        self.balanced()

    async def test_91_skill_guide_internal_rewrite_positive(self):
        # The compiled (9.1.0, ha_get_skill_guide) row turns the fixed internal
        # arguments into the exact file-only form the 9.1 schema admits; the
        # peer enforces that schema, so without the row the read fails.
        peer = DashboardPeer('9.1.0')
        provider = self.provider(peer)
        self.assertEqual(await provider.best_practices_acknowledgement_key(), peer.key)
        self.assertEqual(peer.calls, [(GUIDE, {'file': GUIDE_FILE})])
        self.assertEqual(
            mcp_client._BEST_PRACTICES_ARGUMENT_REWRITES[('9.1.0', GUIDE)],
            (('file', GUIDE_FILE),))
        health = provider.health_snapshot()
        self.assertIsNone(health['last_failure_category'])
        self.assertEqual(health['contract_status'], 'valid')
        self.balanced()

    async def test_91_guide_without_receipt_fails_closed(self):
        peer = DashboardPeer('9.1.0')
        peer.key = 'no-receipt-published'
        provider = self.provider(peer)
        with self.assertRaises(DashboardProviderError):
            await provider.best_practices_acknowledgement_key()
        self.assertEqual(peer.names(), [GUIDE])


# ---------------------------------------------------------------------------
# Section 7: governed dashboard surfaces, positive on both releases.
# ---------------------------------------------------------------------------
class GovernedPositive(Harness):
    async def _positive(self, version):
        for label, operations, expected in (
                ('patch', TITLE_PATCH, lambda c: c['title'] == 'After'),
                ('set', VIEWS_SET, lambda c: c['views'] == [{'title': 'Replaced', 'cards': []}])):
            with self.subTest(version=version, operation=label):
                peer = DashboardPeer(version)
                provider = self.provider(peer)
                listing = await provider.list_dashboards(limit=200, response_limit=60_000)
                self.assertEqual(listing.completeness, 'complete')
                self.assertEqual([row['url_path'] for row in listing.data['dashboards']], [URL_PATH])
                got = await provider.get_dashboard_config(
                    url_path=URL_PATH, force_reload=True, response_limit=60_000)
                self.assertEqual(got.completeness, 'complete')
                self.assertEqual(got.data['configuration'], peer.configuration)
                self.assertEqual(got.data['config_hash'], compute_config_hash_91(peer.configuration))
                peer.calls.clear()
                _s, runtime, _c, result = await self.governed_update(
                    peer, operations, f'ok-{version}-{label}')
                self.assertEqual(result['task_state'], 'succeeded_verified', result)
                self.assertEqual(peer.names().count(SETTER), 1)
                self.assertTrue(expected(peer.configuration), peer.configuration)
                sent = peer.args_for(SETTER)[0]
                self.assertEqual(set(sent), {'url_path', 'config', 'config_hash', 'MandatoryBPS',
                                             'return_screenshot', 'BestPracticeKey'})
                self.assertEqual(sent['BestPracticeKey'], peer.key)
                self.assertLessEqual(set(peer.names()), {GETTER, GUIDE, SETTER})
                self.balanced()

    async def test_91_dashboard_positive(self):
        await self._positive('9.1.0')
        # The native-path 9.1 success (write_committed true plus attached
        # verification fields) is also accepted, still with readback.
        peer = DashboardPeer('9.1.0')
        peer.native_write = True
        _s, _r, _c, result = await self.governed_update(peer, TITLE_PATCH, 'ok-91-native')
        self.assertEqual(result['task_state'], 'succeeded_verified', result)
        self.assertEqual(peer.names().count(SETTER), 1)

    async def test_85_dashboard_positive(self):
        await self._positive('8.5.0')

    async def test_85_dashboard_positive_on_strict_form(self):
        # The design keeps 8.5 useful on both accepted URL forms.
        peer = DashboardPeer('8.5.0')
        with patch.dict(URLS, {'8.5.0': STRICT_URL}):
            _s, _r, _c, result = await self.governed_update(peer, TITLE_PATCH, 'ok-85-strict')
        self.assertEqual(result['task_state'], 'succeeded_verified', result)
        self.assertEqual(peer.args_for(GUIDE), [{'skill': SKILL, 'file': GUIDE_FILE}])

    def test_91_analysis_accepts_stored_config(self):
        config = {'title': 'Before', 'views': []}
        payload = fill(ENVELOPES['get_stored_config'], config=config,
                       config_hash=compute_config_hash_91(config),
                       config_size_bytes=len(json.dumps(config)))
        decoded = upstream_dashboard._analysis_configuration(
            text_result(payload), URL_PATH, upstream_version='9.1.0')
        self.assertEqual(decoded['configuration'], config)
        self.assertEqual(decoded['config_hash'], compute_config_hash_91(config))


# ---------------------------------------------------------------------------
# Section 8.1 item 4: "No config found" fails closed, on 9.1 and 8.5.
# ---------------------------------------------------------------------------
def _configuration_shaped(value):
    return value is None or value == {} or (isinstance(value, dict) and (
        'views' in value or 'title' in value and 'name' not in value))


def no_config_91(args):
    return fill(ENVELOPES['get_no_stored_config']) | {'url_path': args['url_path']}, False


def no_config_85(args):
    # 8.5.0 surfaces Home Assistant's "No config found." as a tool error.
    return error_envelope('SERVICE_CALL_FAILED', 'No config found.',
                          action='get', url_path=args['url_path']), True


NO_CONFIG = {'9.1.0': no_config_91, '8.5.0': no_config_85}


class NoConfigFailsClosed(Harness):
    async def _refused_get(self, peer, *, expected_code=None):
        provider = self.last_provider = self.provider(peer)
        upstream_hash = upstream_dashboard._upstream_config_hash
        engineering_hash = upstream_dashboard._engineering_config_hash
        with patch.object(upstream_dashboard, '_upstream_config_hash',
                          wraps=upstream_hash) as up, \
                patch.object(upstream_dashboard, '_engineering_config_hash',
                             wraps=engineering_hash) as eng:
            with self.assertRaises(DashboardProviderError) as caught:
                await provider.get_dashboard_config(
                    url_path=URL_PATH, force_reload=True, response_limit=60_000)
            # No hash is derived from a non-dict or unavailable config. The
            # Engineering hash helper also fingerprints catalog descriptors
            # during admission, so only configuration-shaped inputs count.
            self.assertEqual(up.call_count, 0)
            self.assertEqual([call for call in eng.call_args_list
                              if _configuration_shaped(call.args[0] if call.args else None)], [])
            self.assertGreater(eng.call_count, 0)  # the wrap was live
        details = caught.exception.details
        self.assertEqual(details['completeness'], 'unavailable')
        self.assertNotIn('config_hash', details)
        self.assertNotIn('configuration', details)
        if expected_code is not None:
            self.assertEqual(details['failure_category'], expected_code)
        # Never decided as absence.
        self.assertNotEqual(details['failure_category'], 'dashboard_not_found')
        return details

    async def test_91_no_stored_config_fails_closed(self):
        peer = DashboardPeer('9.1.0')
        peer.get_payload = no_config_91
        details = await self._refused_get(peer, expected_code='upstream_error')
        self.assertEqual(details['saved_configuration'], 'unavailable')
        self.assertEqual(details['provider_failure_kind'], 'saved_configuration_unavailable')
        self.assertEqual(details['dashboard_identity'], 'exists')
        # A saved-config gap is a domain answer, not an unreachable provider.
        health = self.last_provider.health_snapshot()
        self.assertIs(health['reachable'], True)
        self.assertEqual(health['last_failure_category'], 'upstream_error')
        await self.assert_refused_everywhere(peer, 'nc-91')

    async def test_85_no_config_found_fails_closed(self):
        peer = DashboardPeer('8.5.0')
        peer.get_payload = no_config_85
        details = await self._refused_get(peer, expected_code='upstream_error')
        self.assertEqual(details.get('upstream_error_code'), 'SERVICE_CALL_FAILED')
        await self.assert_refused_everywhere(peer, 'nc-85')

    async def test_91_recovery_mode_and_missing_yaml_fail_closed(self):
        # 9.1.0 reports both through the same stored_config:false envelope
        # (dashboard_read_errors.py NO_STORED_CONFIG_MESSAGES); a YAML-mode
        # listed row does not change that.
        for mode in ('storage', 'yaml'):
            with self.subTest(mode=mode):
                peer = DashboardPeer('9.1.0')
                peer.rows = [listed(mode=mode)]
                peer.get_payload = no_config_91
                await self._refused_get(peer, expected_code='upstream_error')
                await self.assert_refused_everywhere(peer, f'rec-91-{mode}')

    async def test_85_recovery_mode_and_missing_yaml_fail_closed(self):
        for message in ('No config found.', 'Configuration not found', 'Recovery mode'):
            with self.subTest(message=message):
                peer = DashboardPeer('8.5.0')
                peer.rows = [listed(mode='yaml')]
                peer.get_payload = lambda args, m=message: (error_envelope(
                    'SERVICE_CALL_FAILED', m, action='get', url_path=args['url_path']), True)
                await self._refused_get(peer, expected_code='upstream_error')
                await self.assert_refused_everywhere(peer, f'rec-85-{len(message)}')

    async def test_empty_dict_config_without_stored_config_is_never_readable(self):
        cases = {
            '9.1.0': [
                # stored_config false with a synthesized empty dict.
                lambda a: (fill(ENVELOPES['get_no_stored_config'], ) | {
                    'url_path': a['url_path'], 'config': {}, 'config_hash':
                    compute_config_hash_91({})}, False),
                # Empty dict with no stored_config flag on 9.1.
                lambda a: ({'success': True, 'action': 'get', 'url_path': a['url_path'],
                            'config': {}, 'config_hash': compute_config_hash_91({})}, False),
                # Non-boolean stored_config.
                lambda a: (fill(ENVELOPES['get_stored_config'], config={},
                                config_hash=compute_config_hash_91({}),
                                config_size_bytes=2) | {'url_path': a['url_path'],
                                                        'stored_config': 'yes'}, False),
            ],
            '8.5.0': [
                lambda a: ({'success': True, 'action': 'get', 'url_path': a['url_path'],
                            'config': {}, 'config_hash': compute_config_hash_91({}),
                            'stored_config': False}, False),
                # config None (never coerced to {}).
                lambda a: ({'success': True, 'action': 'get', 'url_path': a['url_path'],
                            'config': None, 'config_hash': None}, False),
            ],
        }
        for version, payloads in cases.items():
            for index, payload in enumerate(payloads):
                with self.subTest(version=version, case=index):
                    peer = DashboardPeer(version)
                    peer.get_payload = payload
                    await self._refused_get(peer)
                    await self.assert_refused_everywhere(peer, f'empty-{version}-{index}')

    async def test_91_config_none_with_stored_config_true_fails_closed(self):
        peer = DashboardPeer('9.1.0')
        peer.get_payload = lambda a: (fill(ENVELOPES['get_stored_config'], config=None,
                                           config_hash=None, config_size_bytes=0) | {
                                               'url_path': a['url_path']}, False)
        await self._refused_get(peer, expected_code='invalid_response')
        await self.assert_refused_everywhere(peer, 'none-91')

    async def test_default_absent_from_list_is_refused(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                peer = DashboardPeer(version)
                peer.rows = [listed()]  # no "default" row
                await self.assert_refused_everywhere(peer, f'default-{version}',
                                                     url_path='default')

    async def test_collapsed_empty_list_never_decides_absence(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                peer = DashboardPeer(version)
                peer.rows = []
                provider = self.provider(peer)
                listing = await provider.list_dashboards(limit=200, response_limit=60_000)
                if version == '9.1.0':
                    # 9.1.0 collapses a failed registry read to [] (`or []`).
                    self.assertEqual(listing.completeness, 'partial')
                    self.assertTrue(any('absence is unavailable' in warning
                                        for warning in listing.warnings))
                else:
                    # Retained 8.5 inventory semantics are unchanged.
                    self.assertEqual(listing.completeness, 'complete')
                await self.assert_refused_everywhere(peer, f'empty-list-{version}')

    async def test_none_fetch_collapsed_to_empty_list_is_refused(self):
        # A None fetch reaches the wire as the same `[]` (9.1.0 :1808); a
        # null or non-list `dashboards` field is malformed.
        for version in VERSIONS:
            for value in ([], None, {}, 'unavailable'):
                with self.subTest(version=version, dashboards=value):
                    peer = DashboardPeer(version)
                    peer.list_payload = lambda a, v=value: {
                        'success': True, 'action': 'list', 'dashboards': deepcopy(v),
                        'count': 0}
                    await self.assert_refused_everywhere(
                        peer, f'none-{version}-{type(value).__name__}')

    async def test_malformed_or_truncated_list_is_refused(self):
        for version in VERSIONS:
            for label, rows in (
                    ('malformed-row', [listed(), {'title': 'No path'}]),
                    ('bad-path', [listed(), {'url_path': '../x', 'mode': 'storage'}]),
                    ('non-dict-row', [listed(), 'operations']),
                    ('truncated', [listed(f'dash-{i:03d}') for i in range(201)] + [listed()])):
                with self.subTest(version=version, case=label):
                    peer = DashboardPeer(version)
                    peer.rows = rows
                    provider = self.provider(peer)
                    listing = await provider.list_dashboards(limit=200, response_limit=60_000)
                    self.assertEqual(listing.completeness, 'partial')
                    await self.assert_refused_everywhere(peer, f'mal-{version}-{label}')

    async def test_alias_mismatch_is_refused(self):
        cases = (
            ('resolved_from', lambda a: (fill(ENVELOPES['get_stored_config'],
                config={'title': 'X', 'views': []},
                config_hash=compute_config_hash_91({'title': 'X', 'views': []}),
                config_size_bytes=27) | {'url_path': 'dashboard-operations',
                                         'resolved_from': a['url_path']}, False)),
            ('other-path', lambda a: (fill(ENVELOPES['get_stored_config'],
                config={'title': 'X', 'views': []},
                config_hash=compute_config_hash_91({'title': 'X', 'views': []}),
                config_size_bytes=27) | {'url_path': 'dashboard-operations'}, False)),
            ('no-config-alias', lambda a: (fill(ENVELOPES['get_no_stored_config']) | {
                'url_path': 'dashboard-operations', 'resolved_from': a['url_path']}, False)),
        )
        for version in VERSIONS:
            for label, payload in cases:
                with self.subTest(version=version, case=label):
                    peer = DashboardPeer(version)
                    peer.rows = [listed(), listed('dashboard-operations')]
                    peer.get_payload = payload
                    await self.assert_refused_everywhere(peer, f'alias-{version}-{label}')

    async def test_alias_no_config_does_not_claim_identity(self):
        peer = DashboardPeer('9.1.0')
        peer.get_payload = lambda a: (fill(ENVELOPES['get_no_stored_config']) | {
            'url_path': 'dashboard-operations', 'resolved_from': a['url_path']}, False)
        details = await self._refused_get(peer, expected_code='upstream_error')
        self.assertNotIn('dashboard_identity', details)

    async def test_stale_membership_is_refused(self):
        for version in VERSIONS:
            for label, rows in (
                    ('missing', [listed('other')]),
                    ('duplicated', [listed(), listed()])):
                with self.subTest(version=version, case=label):
                    peer = DashboardPeer(version)
                    peer.rows = rows
                    await self.assert_refused_everywhere(peer, f'stale-{version}-{label}')

    async def test_create_is_never_reached(self):
        # There is no governed dashboard create: a target absent from a list
        # (even one proven complete) is refused, and a setter answer claiming
        # creation is not accepted as an update.
        for version in VERSIONS:
            with self.subTest(version=version, case='absent-target'):
                peer = DashboardPeer(version)
                peer.rows = [listed('other')]
                peer.get_payload = NO_CONFIG[version]
                await self.assert_refused_everywhere(peer, f'create-{version}')
            with self.subTest(version=version, case='create-answer'):
                peer = DashboardPeer(version)
                peer.setter_payload = lambda a: ({
                    'success': True, 'action': 'create', 'url_path': a['url_path'],
                    'dashboard_id': a['url_path'], 'dashboard_created': True,
                    'config_updated': True, 'metadata_updated': False,
                    'message': f"Dashboard {a['url_path']} created successfully"}, False)
                _s, _r, _c, result = await self.governed_update(
                    peer, TITLE_PATCH, f'create-answer-{version}')
                self.assertNotEqual(result['task_state'], 'succeeded_verified', result)
                self.assertEqual(peer.names().count(SETTER), 1)
                self.balanced()

    async def test_91_write_outcome_unknown_is_uncertain_and_not_retried(self):
        # write_committed None is "outcome uncertain": never a success by
        # itself and never redispatched. Only the exact readback decides.
        for applied in (False, True):
            with self.subTest(save_applied=applied):
                peer = DashboardPeer('9.1.0')

                def outcome_unknown(args, peer=peer, applied=applied):
                    if applied:
                        peer.configuration = deepcopy(args['config'])
                    return fill(ENVELOPES['set_write_outcome_unknown']) | {
                        'url_path': args['url_path']}, True
                peer.setter_payload = outcome_unknown
                _s, _r, _c, result = await self.governed_update(
                    peer, TITLE_PATCH, f'unknown-91-{applied}')
                self.assertEqual(peer.names().count(SETTER), 1)
                self.assertIs(result.get('redispatch_performed', False), False)
                if applied:
                    # Readback proves the exact approved configuration.
                    self.assertEqual(result['task_state'], 'succeeded_verified', result)
                    self.assertEqual(peer.configuration['title'], 'After')
                else:
                    self.assertNotIn(result['task_state'], ('succeeded_verified', 'succeeded'),
                                     result)
                    self.assertEqual(peer.configuration['title'], 'Before')
                self.balanced()

    def test_91_analysis_rejects_no_stored_config(self):
        for payload in (
                fill(ENVELOPES['get_no_stored_config']),
                fill(ENVELOPES['get_no_stored_config']) | {
                    'config': {'views': []}, 'config_hash': compute_config_hash_91({'views': []})},
                fill(ENVELOPES['get_stored_config'], config={'views': []},
                     config_hash=compute_config_hash_91({'views': []}),
                     config_size_bytes=13) | {'stored_config': None},
                # Codex F3 on PR #406: on 9.1.0 an absent flag is refused too.
                {key: value for key, value in fill(
                    ENVELOPES['get_stored_config'], config={'views': []},
                    config_hash=compute_config_hash_91({'views': []}),
                    config_size_bytes=13).items() if key != 'stored_config'}):
            with self.subTest(payload=payload.get('stored_config', 'absent')):
                with self.assertRaises(analysis_contracts.AnalysisError) as caught:
                    upstream_dashboard._analysis_configuration(
                        text_result(payload), URL_PATH, upstream_version='9.1.0')
                self.assertEqual(caught.exception.reason, 'source_rejected')


class AnalysisOperatorRemediation(unittest.IsolatedAsyncioTestCase):
    """Section 3.3: the dashboard-analysis result carries the fixed operator
    text for a refused redirect and a catalog-mode refusal. A refused redirect
    reports its own closed reason, upstream_redirect_refused (architect ruling
    2026-11-07, item 4); a catalog-mode refusal keeps authority_unavailable. No
    other category gains remediation text. The end-to-end latch path is
    asserted in test_hamcp319_no_redirect_matrix
    (NoRedirectAnalysisProviderReason)."""

    async def _refused(self, category):
        from ha_mcp_engineering.clients.dashboard_analysis import CollectionBudget
        from ha_mcp_engineering.clients.mcp import DashboardTransportError

        class _Transport:
            calls = 0

            async def execute_analysis_read(self, *_args, **_kwargs):
                type(self).calls += 1
                raise DashboardTransportError(category, retryable=False)

        provider = UpstreamDashboardProvider()
        provider._transport = _Transport()
        with patch.object(UpstreamDashboardProvider, 'analysis_authority',
                          lambda _self: ('synthetic-authority',)):
            with self.assertRaises(analysis_contracts.AnalysisError) as caught:
                await provider.get_analysis_configuration(
                    url_path=URL_PATH, authorize=lambda: None, budget=CollectionBudget())
        self.assertEqual(_Transport.calls, 1)
        return caught.exception

    async def test_redirect_refusal_carries_fixed_operator_text(self):
        from ha_mcp_engineering.clients.ha_mcp_endpoint import (
            OPERATOR_MESSAGES, UPSTREAM_REDIRECT_OPERATOR_MESSAGE, UPSTREAM_REDIRECT_REFUSED)
        from ha_mcp_engineering.errors import map_exception

        from ha_mcp_engineering.errors import ErrorCode

        for category in sorted(OPERATOR_MESSAGES):
            with self.subTest(category=category):
                error = await self._refused(category)
                reason = (UPSTREAM_REDIRECT_REFUSED if category == UPSTREAM_REDIRECT_REFUSED
                          else 'authority_unavailable')
                self.assertEqual(error.reason, reason)
                self.assertFalse(error.retryable)
                self.assertEqual(error.code, ErrorCode.PROVIDER_UNAVAILABLE)
                self.assertEqual(error.details, {
                    'reason': reason,
                    'operator_remediation': OPERATOR_MESSAGES[category]})
                _code, _message, _retryable, details = map_exception(error)
                self.assertEqual(details['reason'], reason)
                self.assertEqual(details['operator_remediation'], OPERATOR_MESSAGES[category])
        error = await self._refused(UPSTREAM_REDIRECT_REFUSED)
        self.assertEqual(error.details['operator_remediation'], UPSTREAM_REDIRECT_OPERATOR_MESSAGE)

    async def test_other_categories_carry_no_remediation(self):
        for category, reason in (('timeout', 'timeout'), ('upstream_error', 'source_rejected'),
                                 ('protocol_error', 'authority_unavailable'),
                                 ('upstream_endpoint_rejected', 'authority_unavailable')):
            with self.subTest(category=category):
                error = await self._refused(category)
                self.assertEqual(error.reason, reason)
                self.assertEqual(error.details, {'reason': reason})

    def test_remediation_is_closed(self):
        # Only a fixed endpoint constant may ride along; free text is dropped.
        error = analysis_contracts.AnalysisError(
            'authority_unavailable', operator_remediation='https://synthetic.invalid/x')
        self.assertEqual(error.details, {'reason': 'authority_unavailable'})



class AnalysisStoredConfigReleaseGate(unittest.IsolatedAsyncioTestCase):
    """HAMCP-319 (Codex F3 on PR #406): dashboard analysis decodes with the
    admitted release. On 9.1.0, whose get mode always reports stored_config,
    anything but literal ``stored_config: true`` is refused before any
    configuration or hash work, matching the governed getter. 8.5.0 omits the
    flag and stays analyzable. The real provider admission runs over a
    synthetic handshake; no endpoint is contacted."""

    REFUSED_91 = (('missing', None), ('false', False), ('null', None),
                  ('string_true', 'true'), ('one', 1), ('zero', 0),
                  ('object', {}), ('list', [True]))

    async def analyse(self, version, flag, value=None):
        from types import SimpleNamespace
        from ha_mcp_engineering.clients.dashboard_analysis import CollectionBudget
        from ha_mcp_engineering.clients.mcp import McpDashboardHandshake, McpDashboardRead

        provider = UpstreamDashboardProvider()
        peer = DashboardPeer(version)
        handshake = McpDashboardHandshake(
            '2025-03-26', 'ha-mcp', version, tuple(peer.tools), 0)
        provider._validate_handshake(handshake)
        payload, _ = peer._get({'url_path': URL_PATH})
        if flag == 'missing':
            payload.pop('stored_config', None)
        elif flag != 'unchanged':
            payload['stored_config'] = value
        exchanges = []

        async def exchange(arguments, admit, **_kwargs):
            exchanges.append(arguments)
            await admit(handshake)
            return McpDashboardRead(handshake, text_result(payload), 0)

        provider._transport = SimpleNamespace(execute_analysis_read=exchange)
        hashing = []
        upstream_hash = upstream_dashboard._upstream_config_hash
        engineering_hash = upstream_dashboard._engineering_config_hash

        def spy(name, function):
            def wrapped(*args, **kwargs):
                hashing.append(name)
                return function(*args, **kwargs)
            return wrapped

        with patch.object(upstream_dashboard, '_upstream_config_hash',
                          spy('upstream', upstream_hash)), \
                patch.object(upstream_dashboard, '_engineering_config_hash',
                             spy('engineering', engineering_hash)):
            try:
                data, metadata = await asyncio.wait_for(
                    provider.get_analysis_configuration(
                        url_path=URL_PATH, authorize=lambda: None,
                        budget=CollectionBudget()), 5)
            except analysis_contracts.AnalysisError as error:
                outcome = ('refused', error.reason, None, None)
            else:
                outcome = ('accepted', None, data, metadata)
        self.assertEqual(len(exchanges), 1)
        return outcome, hashing, peer

    async def test_91_without_literal_true_refused_before_hash_work(self):
        for label, value in self.REFUSED_91:
            with self.subTest(stored_config=label):
                flag = 'missing' if label == 'missing' else 'set'
                (state, reason, data, metadata), hashing, _peer = await self.analyse(
                    '9.1.0', flag, value)
                self.assertEqual((state, reason), ('refused', 'source_rejected'))
                self.assertIsNone(data)
                self.assertIsNone(metadata)
                self.assertEqual(hashing, [])

    async def test_91_literal_true_accepted(self):
        (state, reason, data, metadata), hashing, peer = await self.analyse(
            '9.1.0', 'unchanged')
        self.assertEqual((state, reason), ('accepted', None))
        self.assertEqual(data['configuration'], peer.configuration)
        self.assertEqual(data['config_hash'], compute_config_hash_91(peer.configuration))
        self.assertEqual(metadata['upstream_version'], '9.1.0')
        self.assertEqual(hashing, ['upstream', 'engineering'])

    async def test_85_absent_flag_accepted(self):
        (state, reason, data, metadata), hashing, peer = await self.analyse(
            '8.5.0', 'missing')
        self.assertEqual((state, reason), ('accepted', None))
        self.assertEqual(data['configuration'], peer.configuration)
        self.assertEqual(metadata['upstream_version'], '8.5.0')
        self.assertEqual(hashing, ['upstream', 'engineering'])

    async def test_85_present_flag_keeps_presence_rule(self):
        (state, _r, _d, _m), _h, _p = await self.analyse('8.5.0', 'set', True)
        self.assertEqual(state, 'accepted')
        for label, value in (('false', False), ('null', None), ('string_true', 'true')):
            with self.subTest(stored_config=label):
                (state, reason, data, _m), hashing, _p = await self.analyse(
                    '8.5.0', 'set', value)
                self.assertEqual((state, reason), ('refused', 'source_rejected'))
                self.assertIsNone(data)
                self.assertEqual(hashing, [])


if __name__ == '__main__':
    unittest.main()
