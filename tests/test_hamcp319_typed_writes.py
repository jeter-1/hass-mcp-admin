"""HAMCP-319 Phase 2: typed power, fan and helper writes on ha-mcp 9.1.0 and 8.5.0.

Design section 6.2 (typed-writes outcome matrix), section 1.2 rules 6-8 and
section 8.1 items 3, 5 and 6. The real pinned MCP ClientSession and the
production McpReadGatewayTransport run over in-memory streams against a
synthetic peer; the real CoreRuntime runs on an ephemeral signed registry.
No endpoint, household target, credential or filesystem path is supplied.

9.1.0 peer responses are rendered from tests/fixtures/ha_mcp_9_1_0/typed/
(class S templates; see provenance.json). 8.5.0 responses reuse the existing
synthetic shapes of tests/test_hamcp175_power_fan_admission.py; they are not
source-cited.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import anyio
import jsonschema
from mcp import types
from mcp.shared.message import SessionMessage
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hass_mcp_engineering_beta'), str(ROOT / 'tests'), str(ROOT)]

from core_registry_fixtures import CoreSigner, ProjectedCoreSource, core_entry, NOW
from ha_mcp_engineering.clients import upstream_read
from ha_mcp_engineering.clients.upstream_read import McpReadCatalog
from ha_mcp_engineering.f3.executor import SharedOperationExecutor
from ha_mcp_engineering.fan.authority import FanCoreAuthority
from ha_mcp_engineering.fan.contracts import (FAN_RELEASES, FAN_SEMANTIC_CONTRACTS, FanRefusal,
                                              FanRequest)
from ha_mcp_engineering.fan.service import FAN_OPERATIONS, FanService
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.ha_core_readmission.runtime import CoreRuntime
from ha_mcp_engineering.helper_value.contracts import (HELPER_RELEASES, HELPER_SEMANTIC_CONTRACTS,
                                                       HelperRefusal, HelperRequest)
from ha_mcp_engineering.helper_value.service import (HELPER_VALUE_OPERATIONS, HelperCoreAuthority,
                                                     HelperService, status_of)
from ha_mcp_engineering.power.contracts import (POWER_RELEASES, POWER_SEMANTIC_CONTRACTS, PowerRefusal,
                                                PowerRequest, digest)
from ha_mcp_engineering.power.service import POWER_OPERATIONS, PowerCoreAuthority, PowerService
from ha_mcp_engineering.providers.upstream_fan import FanProvider, REQUIRED_TOOLS
from ha_mcp_engineering.providers.upstream_helper_value import HelperProvider
from ha_mcp_engineering.providers.upstream_power import PowerProvider
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.tools.fan import control_fan
from ha_mcp_engineering.tools.governance import get_execution_task
from ha_mcp_engineering.tools.helper import control_helper
from ha_mcp_engineering.tools.power import control_power
from ha_mcp_engineering.upstream_tool_policy import load_reviewed_upstream_release_registry

VERSIONS = ('9.1.0', '8.5.0')
# 9.1.0 is admitted only through the strict full-catalog selector (design 3.2);
# 8.5.0 keeps its legacy form. Synthetic, never resolved: the client is replaced.
URLS = {'9.1.0': 'http://synthetic.invalid/base/catalog/full', '8.5.0': ''}
FIXTURES = ROOT / 'tests/fixtures/ha_mcp_9_1_0/typed'
TEMPLATES = json.loads((FIXTURES / 'responses.json').read_text())
SECRET = 'synthetic-private-exception-text'
OPAQUE_ONLY = {'available': True, 'names': {'automation.acceptance_probe': 'Acceptance probe'},
               'binding': {'physical_consequence': 'unknown', 'exact_dependency_obligation_count': 0,
                           'coverage_complete': False, 'relevant_consumer_counts_by_class': {'opaque': 1},
                           'downstream_profiles': [{'automation_id': 'automation.acceptance_probe',
                                                    'evidence_class': 'opaque',
                                                    'physical_consequence': 'safety_critical'}]}}

FAMILIES = {
    'power': SimpleNamespace(
        provider=PowerProvider, refusal=PowerRefusal, service=PowerService, core=PowerCoreAuthority,
        operations=POWER_OPERATIONS, tool=control_power, request=PowerRequest, entity='switch.synthetic',
        action='turn_off', initial='on', binding='ordinary_power_binding', outcome='power_outcome',
        releases=POWER_RELEASES, semantic=POWER_SEMANTIC_CONTRACTS, authority='power_provider_authority_token',
        services=('turn_on', 'turn_off'), provider_name='upstream_typed_power'),
    'fan': SimpleNamespace(
        provider=FanProvider, refusal=FanRefusal, service=FanService, core=FanCoreAuthority,
        operations=FAN_OPERATIONS, tool=control_fan, request=FanRequest, entity='fan.synthetic',
        action='turn_off', initial='on', binding='ordinary_fan_binding', outcome='fan_outcome',
        releases=FAN_RELEASES, semantic=FAN_SEMANTIC_CONTRACTS, authority='fan_provider_authority_token',
        services=('turn_on', 'turn_off', 'set_percentage'), provider_name='upstream_typed_fan'),
    'helper': SimpleNamespace(
        provider=HelperProvider, refusal=HelperRefusal, service=HelperService, core=HelperCoreAuthority,
        operations=HELPER_VALUE_OPERATIONS, tool=control_helper, request=HelperRequest,
        entity='input_boolean.synthetic', action='turn_on', initial='off',
        binding='ordinary_helper_value_binding', outcome='helper_value_outcome',
        releases=HELPER_RELEASES, semantic=HELPER_SEMANTIC_CONTRACTS,
        authority='helper_value_provider_authority_token', services=('turn_on', 'turn_off'),
        provider_name='upstream_typed_helper_value'),
}
# Section 8.1 item 3: every key the typed wrappers must never forward.
FORBIDDEN_ARGUMENTS = (
    {'ws_command': 'config/entity_registry/list'}, {'result_fields': ['entity_id']},
    {'result_attribute_keys': ['brightness']}, {'wait': True}, {'return_response': True},
    {'verbose': True}, {'data': {}}, {'domain': 'lock'}, {'service': 'unlock'},
    {'synthetic_unknown_key': 1},
)


def extra_targets(kind):
    entity = FAMILIES[kind].entity
    domain = entity.split('.')[0]
    # 9.1.0 ha_call_service accepts comma-separated entity_id lists.
    return ({'entity_id': f'{entity},{domain}.other'}, {'entity_id': [entity, f'{domain}.other']},
            {'entity_id': f'{entity} {domain}.other'})


def catalog_tools(version):
    folder = ROOT / 'docs/evidence/upstream-read-compatibility'
    raw = json.loads((folder / f'ha-mcp-{version}.json').read_text())
    review = json.loads((folder / f'ha-mcp-{version}-contract-review.json').read_text())
    by_name = {tool['name']: tool for tool in raw['tools']}
    return tuple(deepcopy(by_name[name]) for name in review['runtime_catalog']['runtime_tool_order'])


def call_service_schema(version):
    return next(t for t in catalog_tools(version) if t['name'] == 'ha_call_service')['inputSchema']


def render(value, substitutions):
    """Fill fixture placeholders: whole-value tokens keep their type."""
    if isinstance(value, str):
        if value in substitutions:
            return deepcopy(substitutions[value])
        for token, replacement in substitutions.items():
            if isinstance(replacement, str):
                value = value.replace(token, replacement)
        return value
    if isinstance(value, list):
        return [render(item, substitutions) for item in value]
    if isinstance(value, dict):
        return {key: render(item, substitutions) for key, item in value.items()}
    return value


class Clock:
    """Fake wall clock; the inline observation poll sleeps on it."""
    def __init__(self):
        self.value = NOW

    def __call__(self):
        return self.value

    async def sleep(self, seconds):
        self.value += timedelta(seconds=seconds)
        await asyncio.sleep(0)


class Transport(upstream_read.McpReadGatewayTransport):
    """The production transport; only the operation budget is shortened."""
    budget = None

    async def execute_read(self, tool_name, arguments, *, timeout_seconds, **kwargs):
        return await super().execute_read(tool_name, arguments,
                                          timeout_seconds=self.budget or timeout_seconds, **kwargs)


class Peer:
    """Synthetic ha-mcp peer for one exact version; actual MCP framing."""
    def __init__(self, kind, version):
        self.kind, self.version = kind, version
        self.family = FAMILIES[kind]
        self.tools = catalog_tools(version)
        self.events, self.calls = [], []
        self.writes, self.state, self.percentage = 0, self.family.initial, 50
        self.expected = None
        self.mismatches = []
        self.call_mode = 'changed'
        self.no_effect = False
        self.hooks = {}          # tool -> async callable(count), before the call is applied
        self.after_apply = {}    # tool -> async callable(count), after application, before reply
        self.hang = set()
        self.drop = set()        # apply ha_call_service, then close without a response
        self.connections = []

    def last_updated(self):
        if self.version == '9.1.0':
            return TEMPLATES['synthetic_values']['last_updated_format'] % self.writes
        return f'synthetic-revision-{self.writes}'

    def record(self, args=None):
        attributes = {'percentage': self.percentage, 'supported_features': 49} if self.kind == 'fan' else {}
        full = {'entity_id': self.family.entity, 'state': self.state, 'attributes': attributes,
                'last_updated': self.last_updated()}
        if args is None:
            return full
        fields = args.get('fields') or list(full)
        projected = {key: full[key] for key in fields if key in full}
        if 'attribute_keys' in args and 'attributes' in projected:
            projected['attributes'] = {key: attributes[key] for key in args['attribute_keys'] if key in attributes}
        return projected

    def template(self, name, args, **extra):
        substitutions = {'${timezone}': TEMPLATES['synthetic_values']['timezone'], **extra}
        for key in ('domain', 'service', 'entity_id'):
            if key in args:
                substitutions['${' + key + '}'] = args[key]
        if 'data' in args:
            substitutions['${data}'] = args['data']
        return render(TEMPLATES['templates'][name], substitutions)

    def respond(self, name, args):
        if name == 'ha_list_services':
            domain = args['domain']
            if self.version == '9.1.0':
                services = {f'{domain}.{action}': {'name': action.replace('_', ' ').title(), 'description': '',
                                                    'domain': domain, 'service': action}
                            for action in self.family.services}
                return self.template('list_services_summary', args, **{'${services}': services,
                                                                        '${count}': len(services)})
            return {'success': True, 'services': {f'{domain}.{action}': {} for action in self.family.services}}
        if name == 'ha_get_state':
            if self.version == '9.1.0':
                return self.template('get_state', args, **{'${record}': self.record(args)})
            return {'data': self.record(args)}
        if name == 'ha_call_service':
            compact = {key: value for key, value in self.record().items() if key != 'last_updated'}
            if self.call_mode == 'changed':
                if self.version == '9.1.0':
                    return self.template('call_service_success_changed_states', args, **{'${record}': compact})
                return {'success': True}
            # Non-admin and timeout envelopes are 9.1.0 shapes; on 8.5.0 they are
            # conservative coverage only.
            return self.template({'empty_list': 'call_service_nonadmin_empty_list',
                                  'envelope': 'call_service_nonadmin_envelope',
                                  'partial': 'call_service_timeout_partial'}[self.call_mode], args)
        raise AssertionError('unexpected synthetic dispatch: ' + name)

    def apply(self, args):
        self.writes += 1
        if self.no_effect:
            return
        self.state = 'off' if args['service'] == 'turn_off' else 'on'
        if 'percentage' in args['data']:
            self.percentage = args['data']['percentage']

    async def serve(self, incoming, outgoing, connection):
        async with incoming, outgoing:
            async for message in incoming:
                request = message.message.root
                self.events.append(request.method)
                if not isinstance(request, types.JSONRPCRequest):
                    continue
                if request.method == 'initialize':
                    result = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}},
                              'serverInfo': {'name': 'ha-mcp', 'version': self.version}}
                elif request.method == 'tools/list':
                    result = {'tools': list(self.tools)}
                elif request.method == 'tools/call':
                    name, args = request.params['name'], request.params['arguments']
                    self.calls.append((name, deepcopy(args)))
                    count = sum(1 for called, _ in self.calls if called == name)
                    if name in self.hooks:
                        await self.hooks[name](count)
                    if name in self.hang:
                        await anyio.sleep_forever()
                    if name == 'ha_call_service':
                        if args != self.expected:
                            self.mismatches.append(args)
                            return
                        self.apply(args)
                        if name in self.after_apply:
                            await self.after_apply[name](self.writes)
                        if name in self.drop:
                            return
                    value = self.respond(name, args)
                    result = {'content': [], 'structuredContent': value, 'isError': False}
                else:
                    raise AssertionError('unexpected MCP method')
                await outgoing.send(SessionMessage(types.JSONRPCMessage(
                    types.JSONRPCResponse(jsonrpc='2.0', id=request.id, result=result))))

    @asynccontextmanager
    async def connect(self, unused, **kwargs):
        assert unused == URLS[self.version]  # synthetic only; the external transport is replaced
        connection = {'id': 'synthetic-session-' + uuid.uuid4().hex}
        self.connections.append(connection)
        send_to_client, read = anyio.create_memory_object_stream(10)
        write, read_from_client = anyio.create_memory_object_stream(10)
        async with anyio.create_task_group() as group:
            group.start_soon(self.serve, read_from_client, send_to_client, connection)
            try:
                yield read, write, lambda: connection['id']
            finally:
                self.connections.remove(connection)
                group.cancel_scope.cancel()

    def writes_sent(self):
        return [args for name, args in self.calls if name == 'ha_call_service']


class Harness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = Clock()
        self.telemetry, token = begin_request('synthetic-HAMCP-319-typed')
        self.addCleanup(end_request, token)
        signer = CoreSigner()  # ephemeral test key; no production signing material
        entries = [core_entry(v, typed_operations=True) for v in ('2026.10.0', '2026.10.1')]
        for entry in entries:
            entry['image_index_digest'] = 'sha256:' + sha256(('synthetic-' + entry['version']).encode()).hexdigest()
        raw = signer.journal_raw(entries=entries)
        async def fetch(*args):
            return raw
        self.registry = CoreReleaseRegistry(enabled=True, public_key=signer.public_key_base64,
                                            cache_path=self.root / 'registry.json', fetcher=fetch,
                                            now=lambda: NOW)
        self.core = CoreRuntime()
        self.source = ProjectedCoreSource(self.registry, '2026.10.1', typed_operations=True)
        self.core.configure(object(), source=self.source, release_registry=self.registry)
        await self.core.reconcile_once('startup')
        self.authority = 'synthetic-authority'
        self.patcher = None

    def operation(self, kind, version, *, action=None):
        family = FAMILIES[kind]
        self.clock.value = NOW  # each subtest starts inside its authorization window
        peer = Peer(kind, version)
        transport = Transport(URLS[version], timeout_seconds=3, client_version='synthetic')
        provider = family.provider(transport, lambda *args: self.authority)
        kwargs = dict(now=self.clock, sleep=self.clock.sleep)
        if kind == 'helper':
            async def reader(entity_id):
                return deepcopy(OPAQUE_ONLY)
            kwargs['dependency_reader'] = reader
        service = family.service(self.root / uuid.uuid4().hex, provider, family.core(self.core), **kwargs)
        self.addAsyncCleanup(service.close)
        request = self.request(kind, action=action)
        peer.expected = request.arguments()
        return peer, service, request

    def request(self, kind, *, action=None, operation_id=None):
        family = FAMILIES[kind]
        request = family.request(entity_id=family.entity, action=action or family.action,
                                 operation_id=operation_id or f'{int(NOW.timestamp())}-{uuid.uuid4().hex}')
        setattr(self.telemetry, family.binding, digest(request.model_dump()))
        return request

    async def public_call(self, peer, service, request):
        family = FAMILIES[peer.kind]
        with patch.object(upstream_read, 'streamablehttp_client', peer.connect), \
             patch.object(family.operations, 'service', service):
            return json.loads(await family.tool(**request.model_dump(exclude_none=True)))

    async def read_task(self, peer, service, task_id):
        with patch.object(upstream_read, 'streamablehttp_client', peer.connect), \
             patch.object(FAMILIES[peer.kind].operations, 'service', service):
            return json.loads(await get_execution_task(task_id))

    @staticmethod
    def outcome(service, task_id):
        """The executor's normalized outcome for the durable record."""
        return SharedOperationExecutor._result(service.executions.get(task_id)).outcome

    def balanced(self):
        self.assertEqual(self.telemetry.upstream_active_requests, 0)

    async def wait_for(self, predicate, pending=None):
        async with asyncio.timeout(10):
            while not predicate():
                if pending is not None and pending.done():
                    break
                await asyncio.sleep(0.01)

    async def settle(self, service):
        await asyncio.gather(*list(service.active.values()), return_exceptions=True)

    # Shared assertions ---------------------------------------------------------

    def assert_verified(self, result, peer, service, request, version):
        family = FAMILIES[peer.kind]
        data = result.get('data', {})
        self.assertTrue(result['success'], result)
        self.assertEqual(data.get('state'), 'succeeded_verified', result)
        if peer.kind == 'helper':
            self.assertEqual(data['status'], 'succeeded_verified')
        self.assertEqual(data['provider'], family.provider_name)
        self.assertEqual(data['provider_contract'], family.semantic[version])
        self.assertEqual(data['core_binding']['version'], '2026.10.1')
        self.assertEqual(data['provider_attempt_count'], 1)
        self.assertTrue(data['dispatch_intent_recorded'])
        self.assertEqual(result['metadata']['task_id'], request.task_id)
        self.assertEqual(peer.writes, 1)
        self.assertEqual(peer.mismatches, [])
        self.assertEqual(peer.writes_sent(), [request.arguments()])
        self.assertEqual(service.locks.records(), ())

    def assert_uncertain(self, peer, service, request, *, response_received=False):
        """Post-intent uncertainty per the section 6.2 matrix.

        Send or reply loss (no provider response) is ``dispatch_indeterminate``
        for power and fan; once a response was recorded, an interrupted
        observation keeps the existing uncertain ``observing`` outcome (matrix
        row "Observation timeout"). Helper reports ``changed_unverified``.
        """
        receipt = service.receipt(request.task_id)
        self.assertIsNotNone(receipt)
        self.assertTrue(receipt['dispatch_intent_recorded'], receipt)
        self.assertIs(receipt['provider_response_received'], response_received, receipt)
        self.assertFalse(receipt['terminal'], receipt)
        self.assertIsNone(receipt['terminal_outcome'])
        self.assertIs(receipt['provider_dispatch_occurred'], True, receipt)
        self.assertTrue(receipt['redispatch_prohibited'], receipt)
        self.assertLessEqual(peer.writes, 1)
        self.assertLessEqual(len(peer.writes_sent()), 1)
        outcome = self.outcome(service, request.task_id)
        self.assertEqual(outcome, 'observing' if response_received else 'dispatch_indeterminate')
        if peer.kind == 'helper':
            self.assertEqual(status_of(receipt), 'changed_unverified', receipt)
        return receipt


class TypedWritePositives(Harness):
    """Plan -> apply -> verified readback, exact arguments, on each exact release."""

    async def positive(self, kind, version):
        family = FAMILIES[kind]
        peer, service, request = self.operation(kind, version)
        result = await self.public_call(peer, service, request)
        self.assert_verified(result, peer, service, request, version)
        args = peer.writes_sent()[0]
        # Rule 6: the exact constructed key/value set, valid against the
        # observed exact-release ha_call_service schema.
        domain = family.entity.split('.')[0]
        self.assertEqual(args, {'domain': domain, 'service': request.action, 'entity_id': family.entity,
                                'data': {}, 'wait': False, 'return_response': False, 'verbose': False})
        jsonschema.validate(args, call_service_schema(version))
        called = [name for name, _ in peer.calls]
        self.assertTrue(set(called) <= set(REQUIRED_TOOLS), called)
        self.assertEqual(called.count('ha_call_service'), 1)
        # Same ID only reconciles; no second write.
        replay = await self.public_call(peer, service, request)
        self.assertEqual(replay['data']['state'], 'succeeded_verified')
        read = await self.read_task(peer, service, request.task_id)
        self.assertEqual(read['data']['state'], 'succeeded_verified', read)
        self.assertEqual(peer.writes, 1)
        # The release row binds the exact reviewed registry entry and commit.
        release = load_reviewed_upstream_release_registry().by_version[version]
        self.assertEqual(family.releases[version][:2], (release.entry_id, release.source_commit))
        self.balanced()

    async def test_91_power_positive(self):
        await self.positive('power', '9.1.0')

    async def test_91_fan_positive(self):
        await self.positive('fan', '9.1.0')

    async def test_91_helper_positive(self):
        await self.positive('helper', '9.1.0')

    async def test_85_power_positive(self):
        await self.positive('power', '8.5.0')

    async def test_85_fan_positive(self):
        await self.positive('fan', '8.5.0')

    async def test_85_helper_positive(self):
        await self.positive('helper', '8.5.0')

    async def test_fan_percentage_exact_arguments_on_each_release(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                peer, service, _ = self.operation('fan', version)
                request = FanRequest(entity_id='fan.synthetic', action='set_percentage', percentage=30,
                                     operation_id=f'{int(NOW.timestamp())}-{uuid.uuid4().hex}')
                self.telemetry.ordinary_fan_binding = digest(request.model_dump())
                peer.expected = request.arguments()
                result = await self.public_call(peer, service, request)
                self.assertEqual(result.get('data', {}).get('state'), 'succeeded_verified', result)
                self.assertEqual(peer.writes_sent(), [{'domain': 'fan', 'service': 'set_percentage',
                    'entity_id': 'fan.synthetic', 'data': {'percentage': 30}, 'wait': False,
                    'return_response': False, 'verbose': False}])
                jsonschema.validate(peer.writes_sent()[0], call_service_schema(version))
                self.assertEqual((peer.state, peer.percentage), ('on', 30))

    def test_release_rows_and_gateway_authority_are_exact(self):
        registry = load_reviewed_upstream_release_registry()
        gateway = SimpleNamespace(_lock=threading.RLock(), _release_registry=registry,
                                  _readmission_selector=None)
        for kind, family in FAMILIES.items():
            for version in VERSIONS:
                with self.subTest(kind=kind, version=version):
                    release = registry.by_version[version]
                    self.assertEqual(family.releases[version][:2], (release.entry_id, release.source_commit))
                    self.assertEqual(family.semantic[version], f'ha-mcp-{version}-single-' +
                                     {'power': 'power-v2', 'fan': 'fan-v2', 'helper': 'helper-value-v1'}[kind])
                    token = getattr(UpstreamReadGateway, family.authority)(gateway, version)
                    self.assertIsInstance(token, str)
            # An adjacent unreviewed version has no row and fails closed.
            for version in ('9.1.1', '9.0.0'):
                with self.subTest(kind=kind, version=version):
                    self.assertNotIn(version, family.releases)
                    with self.assertRaises(family.refusal):
                        getattr(UpstreamReadGateway, family.authority)(gateway, version)
        self.assertEqual(FAN_RELEASES['9.1.0'][2], 'core-2026.9.2-ha-mcp-9.1.0-single-fan-v1')
        self.assertEqual(POWER_RELEASES['9.1.0'][2], 'core-2026.9.2-ha-mcp-9.1.0-single-power-v1')
        self.assertEqual(HELPER_RELEASES['9.1.0'][0], 'ha-mcp-v9.1.0-4259f247')

    def test_provider_admits_each_exact_catalog_and_refuses_cross_release_mapping(self):
        for kind, family in FAMILIES.items():
            for version in VERSIONS:
                c = McpReadCatalog('2025-03-26', 'ha-mcp', version, catalog_tools(version), 0)
                with self.subTest(kind=kind, version=version):
                    selected, contract, authority = family.provider(None, lambda *a: 'a').validate_catalog(c)
                    self.assertEqual((selected, authority), (version, 'a'))
                    self.assertEqual(contract, family.releases[version][2])
                    signed = family.provider(None, lambda *a: 'a').validate_catalog(c, signed_core=True)
                    self.assertEqual(signed[1], family.semantic[version])
                # A session bound to one release never maps through the other's row.
                other = '8.5.0' if version == '9.1.0' else '9.1.0'
                with self.subTest(kind=kind, version=version, expected=other):
                    with self.assertRaises(family.refusal):
                        family.provider(None, lambda *a: 'a').validate_catalog(
                            c, expected_contract=family.semantic[other])


class ForwardingRefusals(Harness):
    """Section 8.1 item 3: refused before dispatch, zero peer calls."""

    def test_request_models_reject_every_forbidden_key_and_extra_target(self):
        for kind, family in FAMILIES.items():
            base = {'entity_id': family.entity, 'action': family.action,
                    'operation_id': f'{int(NOW.timestamp())}-{uuid.uuid4().hex}'}
            for change in FORBIDDEN_ARGUMENTS + extra_targets(kind):
                with self.subTest(kind=kind, change=change):
                    with self.assertRaises(ValidationError):
                        family.request.model_validate({**base, **change})

    async def test_provider_revalidation_refuses_constructed_requests_with_zero_peer_calls(self):
        for kind, family in FAMILIES.items():
            for version in VERSIONS:
                peer, service, request = self.operation(kind, version)
                for change in FORBIDDEN_ARGUMENTS + extra_targets(kind):
                    smuggled = SimpleNamespace(task_id=request.task_id, operation_id=request.operation_id,
                                               model_dump=lambda change=change: {**request.model_dump(), **change})
                    with self.subTest(kind=kind, version=version, change=change), \
                         patch.object(upstream_read, 'streamablehttp_client', peer.connect):
                        with self.assertRaises(ValidationError):
                            await service.provider.dispatch(smuggled, lambda: None)
                        with self.assertRaises((ValidationError, family.refusal)):
                            await service.control(smuggled)
                self.assertEqual(peer.events, [])
                self.assertEqual(peer.calls, [])
                self.assertEqual(peer.writes, 0)
                self.assertIsNone(service.load(request.task_id))

    async def test_public_tool_rejects_forbidden_keys_before_any_peer_contact(self):
        for kind, family in FAMILIES.items():
            for version in VERSIONS:
                peer, service, request = self.operation(kind, version)
                for change in FORBIDDEN_ARGUMENTS + extra_targets(kind):
                    with self.subTest(kind=kind, version=version, change=change), \
                         patch.object(upstream_read, 'streamablehttp_client', peer.connect), \
                         patch.object(family.operations, 'service', service):
                        try:
                            raw = await family.tool(**{**request.model_dump(exclude_none=True), **change})
                        except (TypeError, ValidationError):
                            continue  # rejected by the Python signature itself
                        self.assertFalse(json.loads(raw)['success'], raw)
                self.assertEqual(peer.events, [])
                self.assertEqual(peer.writes, 0)


class RecordingTransport:
    """Counts every provider contact; models one synthetic target per family."""
    def __init__(self, kind, version):
        self.kind, self.family = kind, FAMILIES[kind]
        self.catalog = McpReadCatalog('2025-03-26', 'ha-mcp', version, catalog_tools(version), 0)
        self.state = {'entity_id': self.family.entity, 'state': self.family.initial,
                      'attributes': {'percentage': 50, 'supported_features': 49} if kind == 'fan' else {},
                      'last_updated': 'synthetic-revision-1'}
        self.calls, self.writes, self.authority = [], 0, 'synthetic-current'

    async def execute_read(self, tool, args, *, catalog_validator, before_dispatch, **kwargs):
        catalog_validator(self.catalog)
        await before_dispatch()
        self.calls.append((tool, deepcopy(args)))
        if tool == 'ha_get_state':
            fields = args['fields']
            result = {'data': {key: deepcopy(self.state[key]) for key in fields}}
        elif tool == 'ha_list_services':
            result = {'success': True, 'services': {args['domain'] + '.' + a: {} for a in self.family.services}}
        elif tool == 'ha_call_service':
            self.writes += 1
            self.state.update(state='off' if args['service'] == 'turn_off' else 'on',
                              last_updated=f'synthetic-revision-{self.writes + 1}')
            result = {'success': True}
        else:
            raise AssertionError('unrelated provider reachability: ' + tool)
        return SimpleNamespace(call_result={'structuredContent': result, 'isError': False})


class SignedStubCore:
    """Synthetic signed applicability for the authenticated-gateway test only."""
    def __init__(self):
        from ha_mcp_engineering.ha_core_readmission.typed_operations import operation_profile
        self.profiles = {operation_profile(f).capability_id for f in ('power', 'fan', 'helper_value')}
        self.version, self.generation, self.leases, self.commits = '2026.10.1', 1, set(), set()

    async def reconcile_once(self, trigger):
        return None

    def route_status(self, requirements):
        return {'available': bool(self.profiles.intersection(requirements))}

    def acquire(self, requirements, **kwargs):
        if kwargs.get('expected_core_version') not in {None, self.version}:
            return None
        token = SimpleNamespace(nonce=uuid.uuid4().hex, generation=self.generation,
                                core_version=self.version, capability_ids=tuple(requirements))
        self.leases.add(id(token)); token.key = id(token)
        return token

    def consume(self, token):
        if token.key not in self.leases:
            return None
        self.leases.remove(token.key); self.commits.add(token.key)
        return token

    def revalidate(self, token, commit):
        return token is commit and token.key in self.commits

    def release(self, token):
        self.leases.discard(token.key)

    def finish(self, token):
        self.commits.discard(token.key)


class GatewayForwardingRefusals(unittest.TestCase):
    """Strict routing validation: HTTP 400 before the tool runs, zero contact."""

    def run_gateway(self, kind, version):
        from tests.test_mcp_inbound_security import settings_for, SECRET as GATEWAY_SECRET
        from tests.same_thread_asgi_client import SameThreadAsgiTestClient
        from ha_mcp_engineering.mcp_server import create_mcp_server
        from ha_mcp_engineering.routing import AuthenticatedMcpGateway
        from ha_mcp_engineering.audit import AuditLogger
        family = FAMILIES[kind]
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            clock, core = Clock(), SignedStubCore()
            transport = RecordingTransport(kind, version)
            audit = AuditLogger(settings.audit_path, 'synthetic-audit-secret')
            kwargs = dict(now=clock, sleep=clock.sleep, audit=audit)
            if kind == 'helper':
                async def reader(entity_id):
                    return deepcopy(OPAQUE_ONLY)
                kwargs['dependency_reader'] = reader
            service = family.service(directory, family.provider(transport, lambda *a: transport.authority),
                                     family.core(core), **kwargs)
            server = create_mcp_server(settings); server.tool()(family.tool)
            inner = server.streamable_http_app()
            gateway = AuthenticatedMcpGateway(inner, settings, audit)
            name = family.tool.__name__
            def body(**change):
                arguments = dict(entity_id=family.entity, action=family.action,
                                 operation_id=f'{int(clock().timestamp())}-{uuid.uuid4().hex}')
                arguments.update(change)
                return {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                        'params': {'name': name, 'arguments': arguments}}
            headers = {'accept': 'application/json, text/event-stream'}
            path = '/' + GATEWAY_SECRET + '/mcp'
            with patch.object(family.operations, 'service', service), SameThreadAsgiTestClient(
                    gateway, lifespan_app=inner, base_url='http://127.0.0.1:8100') as client:
                for change in FORBIDDEN_ARGUMENTS + extra_targets(kind):
                    with self.subTest(kind=kind, version=version, change=change):
                        self.assertEqual(client.post(path, json=body(**change), headers=headers).status_code,
                                         400, change)
                self.assertEqual(transport.calls, [])
                self.assertEqual(transport.writes, 0)
                response = client.post(path, json=body(), headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertIn('succeeded_verified', response.text)
            self.assertEqual(transport.writes, 1)
            self.assertEqual([a for t, a in transport.calls if t == 'ha_call_service'],
                             [family.request(entity_id=family.entity, action=family.action,
                                             operation_id=f'{int(NOW.timestamp())}-{"0" * 32}').arguments()])

    def test_power_gateway_refuses_forwarding_keys(self):
        for version in VERSIONS:
            self.run_gateway('power', version)

    def test_fan_gateway_refuses_forwarding_keys(self):
        for version in VERSIONS:
            self.run_gateway('fan', version)

    def test_helper_gateway_refuses_forwarding_keys(self):
        for version in VERSIONS:
            self.run_gateway('helper', version)


class CancellationMatrix(Harness):
    """Section 8.1 item 5 at five injection points with real task cancellation."""

    async def cancel_execution(self, peer, service, request, reached):
        """Run the public call until ``reached``, then cancel the execution task."""
        with patch.object(upstream_read, 'streamablehttp_client', peer.connect):
            pending = asyncio.create_task(self.public_call(peer, service, request))
            await self.wait_for(lambda: reached() and request.task_id in service.active, pending)
            self.assertFalse(pending.done())
            service.active[request.task_id].cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            await self.settle(service)
        self.assertNotIn(request.task_id, service.active)
        self.balanced()

    async def recover(self, peer, service, request, *, applied):
        """Status and recovery reconcile from durable state; never redispatch."""
        peer.hooks.clear(); peer.after_apply.clear(); peer.hang.clear(); peer.drop.clear()
        writes = peer.writes
        # While the cancelled owner's claim is unexpired, status reports only.
        early = await self.read_task(peer, service, request.task_id)
        data = early['data']
        self.assertEqual(data['task_id'], request.task_id, early)
        self.assertTrue(data['dispatch_intent_recorded'], early)
        self.assertTrue(data['redispatch_prohibited'], early)
        self.assertIs(data['provider_dispatch_occurred'], True, early)
        not_before = data.get('observation_status', {}).get('next_reobservation_not_before')
        if not data['terminal'] and not_before:
            # Read-only recovery after the claim and lease expire.
            self.clock.value = datetime.fromisoformat(not_before) + timedelta(seconds=1)
        read = await self.read_task(peer, service, request.task_id)
        replay = await self.public_call(peer, service, request)
        self.assertEqual(replay['metadata']['task_id'], request.task_id, replay)
        if applied:
            self.assertEqual(read['data']['state'], 'succeeded_verified', read)
        else:
            self.assertNotEqual(read['data']['state'], 'succeeded_verified', read)
            if not read['data']['terminal'] and peer.kind == 'helper':
                self.assertEqual(read['data']['status'], 'changed_unverified', read)
            # Past the evidence deadline the final read still cannot claim
            # success, a confirmed non-send, or a pre-dispatch failure.
            self.clock.value = datetime.fromisoformat(read['data']['observation_status']['evidence_deadline']) \
                + timedelta(seconds=1)
            read = await self.read_task(peer, service, request.task_id)
            data = read['data']
            self.assertNotEqual(data['state'], 'succeeded_verified', read)
            self.assertNotIn(data['terminal_outcome'], ('succeeded_verified', 'dispatch_failed_confirmed',
                                                        'failed_pre_dispatch'), read)
            self.assertIs(data['provider_dispatch_occurred'], True, read)
            # The existing uncertain terminal: held for review, never retried.
            self.assertEqual((data['state'], data['terminal_outcome']),
                             ('manual_review_required', 'manual_review_required'), read)
            if peer.kind == 'helper':
                self.assertEqual(data['status'], 'changed_unverified', read)
        self.assertEqual(peer.writes, writes)
        self.assertLessEqual(len(peer.writes_sent()), 1)
        self.balanced()
        return read

    async def test_point_1_before_intent_cancellation_leaves_no_task(self):
        for kind in FAMILIES:
            for version in VERSIONS:
                with self.subTest(kind=kind, version=version):
                    self.telemetry.audit_context.clear()
                    peer, service, request = self.operation(kind, version)
                    peer.hang.add('ha_list_services')
                    with patch.object(upstream_read, 'streamablehttp_client', peer.connect):
                        pending = asyncio.create_task(self.public_call(peer, service, request))
                        await self.wait_for(lambda: 'tools/call' in peer.events, pending)
                        self.assertFalse(pending.done())
                        pending.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await pending
                    self.assertIsNone(service.load(request.task_id))
                    self.assertIsNone(service.executions.get(request.task_id))
                    self.assertEqual(peer.writes_sent(), [])
                    self.assertEqual(peer.writes, 0)
                    self.assertEqual(service.locks.records(), ())
                    self.assertEqual(self.telemetry.audit_context[FAMILIES[kind].outcome],
                                     'preparation_failed_no_task')
                    self.balanced()

    async def test_point_2a_proven_pre_send_refusal_stays_call_not_sent(self):
        for kind in FAMILIES:
            for version in VERSIONS:
                with self.subTest(kind=kind, version=version):
                    self.authority = 'synthetic-authority'
                    peer, service, request = self.operation(kind, version)
                    commit = service.executions.commit_dispatch_intent
                    def retire_after_intent(*args, **kwargs):
                        value = commit(*args, **kwargs)
                        self.authority = 'synthetic-retired'  # provider recheck after the callback
                        return value
                    with patch.object(service.executions, 'commit_dispatch_intent', retire_after_intent):
                        result = await self.public_call(peer, service, request)
                    self.assertEqual(peer.writes_sent(), [])
                    self.assertEqual(peer.writes, 0)
                    receipt = service.receipt(request.task_id)
                    self.assertTrue(receipt['dispatch_intent_recorded'], receipt)
                    self.assertTrue(receipt['terminal'], receipt)
                    self.assertEqual(receipt['terminal_outcome'], 'dispatch_failed_confirmed', receipt)
                    self.assertIs(receipt['provider_dispatch_occurred'], False, receipt)
                    self.assertNotEqual(self.outcome(service, request.task_id), 'dispatch_indeterminate')
                    if kind == 'helper':
                        self.assertEqual(status_of(receipt), 'provider_unavailable')
                    self.assertEqual(result['metadata']['task_id'], request.task_id)
                    self.authority = 'synthetic-authority'
                    read = await self.read_task(peer, service, request.task_id)
                    self.assertEqual(read['data']['terminal_outcome'], 'dispatch_failed_confirmed', read)
                    await self.public_call(peer, service, request)
                    self.assertEqual(peer.writes, 0)  # a confirmed not-sent is never retried
                    self.balanced()

    async def test_point_2b_cancellation_after_intent_before_send_is_indeterminate(self):
        for kind in FAMILIES:
            for version in VERSIONS:
                with self.subTest(kind=kind, version=version):
                    peer, service, request = self.operation(kind, version)
                    commit = service.executions.commit_dispatch_intent
                    def cancel_after_intent(*args, **kwargs):
                        value = commit(*args, **kwargs)
                        asyncio.current_task().cancel()  # the real execution task
                        return value
                    with patch.object(service.executions, 'commit_dispatch_intent', cancel_after_intent), \
                         patch.object(upstream_read, 'streamablehttp_client', peer.connect):
                        with self.assertRaises(asyncio.CancelledError):
                            await self.public_call(peer, service, request)
                        await self.settle(service)
                    self.assertEqual(peer.writes_sent(), [])
                    self.assert_uncertain(peer, service, request)
                    await self.recover(peer, service, request, applied=False)
                    self.balanced()

    async def test_point_3_cancellation_during_send_is_indeterminate(self):
        for kind in FAMILIES:
            for version in VERSIONS:
                with self.subTest(kind=kind, version=version):
                    peer, service, request = self.operation(kind, version)
                    release = asyncio.Event()
                    async def hold(count):
                        await release.wait()
                    peer.hooks['ha_call_service'] = hold
                    await self.cancel_execution(peer, service, request, lambda: peer.writes_sent() != [])
                    self.assertEqual(len(peer.writes_sent()), 1)
                    self.assertEqual(peer.writes, 0)
                    self.assert_uncertain(peer, service, request)
                    await self.recover(peer, service, request, applied=False)

    async def test_point_4_application_before_reply_is_indeterminate_then_verifies(self):
        for kind in FAMILIES:
            for version in VERSIONS:
                with self.subTest(kind=kind, version=version, injection='cancel'):
                    peer, service, request = self.operation(kind, version)
                    async def hang(count):
                        await anyio.sleep_forever()
                    peer.after_apply['ha_call_service'] = hang
                    await self.cancel_execution(peer, service, request, lambda: peer.writes == 1)
                    self.assert_uncertain(peer, service, request)
                    await self.recover(peer, service, request, applied=True)
                    self.assertEqual(peer.writes, 1)
                with self.subTest(kind=kind, version=version, injection='timeout'):
                    peer, service, request = self.operation(kind, version)
                    peer.drop.add('ha_call_service')
                    service.provider.transport.budget = 1
                    result = await self.public_call(peer, service, request)
                    self.assertEqual(peer.writes, 1)
                    self.assertTrue(result['success'], result)
                    self.assertTrue(result['data']['dispatch_intent_recorded'])
                    self.assertFalse(result['data']['provider_response_received'])
                    self.assertNotEqual(result['data'].get('provider_dispatch_occurred'), False)
                    service.provider.transport.budget = None
                    await self.recover(peer, service, request, applied=True)

    async def test_point_5_cancellation_during_observation_stays_uncertain_then_verifies(self):
        for kind in FAMILIES:
            for version in VERSIONS:
                with self.subTest(kind=kind, version=version):
                    peer, service, request = self.operation(kind, version)
                    async def hang_after_write(count):
                        if peer.writes:
                            await anyio.sleep_forever()
                    peer.hooks['ha_get_state'] = hang_after_write
                    reads_after = lambda: peer.writes == 1 and peer.calls[-1][0] == 'ha_get_state'
                    await self.cancel_execution(peer, service, request, reads_after)
                    self.assertEqual(peer.writes, 1)
                    self.assert_uncertain(peer, service, request, response_received=True)
                    await self.recover(peer, service, request, applied=True)
                    self.assertEqual(peer.writes, 1)

    async def test_partial_timeout_envelope_goes_to_observation(self):
        for kind in FAMILIES:
            for version in VERSIONS:
                with self.subTest(kind=kind, version=version, effect=True):
                    peer, service, request = self.operation(kind, version)
                    peer.call_mode = 'partial'
                    result = await self.public_call(peer, service, request)
                    self.assert_verified(result, peer, service, request, version)
                with self.subTest(kind=kind, version=version, effect=False):
                    peer, service, request = self.operation(kind, version)
                    peer.call_mode, peer.no_effect = 'partial', True
                    await self.public_call(peer, service, request)
                    self.assertEqual(peer.writes, 1)
                    receipt = service.receipt(request.task_id)
                    self.assertFalse(receipt['terminal'], receipt)
                    self.assertTrue(receipt['provider_response_received'])
                    if kind == 'helper':
                        self.assertEqual(status_of(receipt), 'changed_unverified')


class NonAdminEmptyResult(Harness):
    """Section 8.1 item 6: empty changed_states never means not sent or failure."""

    async def test_empty_result_shapes_go_to_observation(self):
        for kind in FAMILIES:
            for version in VERSIONS:
                for mode in ('empty_list', 'envelope'):
                    with self.subTest(kind=kind, version=version, mode=mode, readback='matching'):
                        peer, service, request = self.operation(kind, version)
                        peer.call_mode = mode
                        result = await self.public_call(peer, service, request)
                        self.assert_verified(result, peer, service, request, version)
                        self.assertTrue(result['data']['provider_response_received'])
                    with self.subTest(kind=kind, version=version, mode=mode, readback='non_matching'):
                        peer, service, request = self.operation(kind, version)
                        peer.call_mode, peer.no_effect = mode, True
                        result = await self.public_call(peer, service, request)
                        self.assertTrue(result['success'], result)
                        data = result['data']
                        self.assertEqual(peer.writes, 1)
                        self.assertTrue(data['dispatch_intent_recorded'])
                        self.assertTrue(data['provider_response_received'])
                        self.assertFalse(data['terminal'], data)
                        self.assertNotEqual(data['state'], 'succeeded_verified')
                        self.assertNotEqual(data.get('provider_dispatch_occurred'), False, data)
                        self.assertNotIn(data.get('terminal_outcome'), ('dispatch_failed_confirmed',
                                                                         'failed_pre_dispatch'))
                        if kind == 'helper':
                            self.assertEqual(data['status'], 'changed_unverified', data)
                        read = await self.read_task(peer, service, request.task_id)
                        self.assertNotEqual(read['data']['state'], 'succeeded_verified', read)
                        self.assertEqual(peer.writes, 1)
                        self.balanced()


if __name__ == '__main__':
    unittest.main()
