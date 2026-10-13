"""HAMCP-175: dependency-scoped power/fan admission and truthful outcomes.

Converted from the PR #249 characterization. The 77 descriptors come from
committed exact-8.5.0 evidence in runtime order; the three additional ones carry
the reported live names with deliberately synthetic contracts. No endpoint,
household target or filesystem path is supplied: the real pinned MCP
ClientSession runs over in-memory streams against a synthetic peer.
"""
import asyncio
from datetime import timedelta
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import anyio
from mcp import types
from mcp.shared.message import SessionMessage

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hass_mcp_engineering_beta'), str(ROOT / 'tests')]

from core_registry_fixtures import CoreSigner, ProjectedCoreSource, core_entry, NOW
from ha_mcp_engineering.clients import upstream_read
from ha_mcp_engineering.clients.mcp import DashboardTransportError
from ha_mcp_engineering.errors import ErrorCode, map_exception
from ha_mcp_engineering.fan.authority import FanCoreAuthority
from ha_mcp_engineering.fan.contracts import FanRefusal, FanRequest
from ha_mcp_engineering.fan.service import FanService, FAN_OPERATIONS
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.ha_core_readmission.runtime import CoreRuntime
from ha_mcp_engineering.power.contracts import PowerRequest, PowerRefusal, digest
from ha_mcp_engineering.power.service import PowerService, PowerCoreAuthority, POWER_OPERATIONS
from ha_mcp_engineering.providers import upstream_fan
from ha_mcp_engineering.providers.upstream_fan import FanProvider, REQUIRED_TOOLS
from ha_mcp_engineering.providers.upstream_power import PowerProvider
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.tools.fan import control_fan
from ha_mcp_engineering.tools.governance import get_execution_task
from ha_mcp_engineering.tools.power import control_power
from ha_mcp_engineering.upstream_tool_policy import (
    load_reviewed_upstream_release_registry, validate_reviewed_release_catalog,
    validate_reviewed_release_dependencies,
)

EXTRAS = ('ha_config_get_yaml', 'ha_list_files', 'ha_read_file')
SECRET = 'synthetic-private-exception-text'
KINDS = {
    'power': (PowerProvider, PowerRefusal, PowerService, PowerCoreAuthority, POWER_OPERATIONS,
              control_power, 'switch.synthetic', 'ordinary_power_binding', 'power_outcome'),
    'fan': (FanProvider, FanRefusal, FanService, FanCoreAuthority, FAN_OPERATIONS,
            control_fan, 'fan.synthetic', 'ordinary_fan_binding', 'fan_outcome'),
}


def catalog(extra_names=(), *, version='8.5.0', protocol='2025-03-26'):
    folder = ROOT / 'docs/evidence/upstream-read-compatibility'
    raw = json.loads((folder / 'ha-mcp-8.5.0.json').read_text())
    review = json.loads((folder / 'ha-mcp-8.5.0-contract-review.json').read_text())
    by_name = {tool['name']: tool for tool in raw['tools']}
    tools = [by_name[name] for name in review['runtime_catalog']['runtime_tool_order']]
    tools.extend({'name': name, 'description': 'Synthetic unreviewed tool; never called.',
                  'inputSchema': {'type': 'object', 'properties': {}}}
                 for name in extra_names)
    return upstream_read.McpReadCatalog(protocol, 'ha-mcp', version, tuple(deepcopy(tools)), 0)


def drifted(tools, name, component):
    tools = deepcopy(list(tools))
    tool = next(item for item in tools if item['name'] == name)
    if component == 'inputSchema':
        tool['inputSchema'] = {'type': 'object', 'properties': {}}
    elif component == 'description':
        tool['description'] = (tool.get('description') or '') + ' Synthetic drift.'
    elif component == 'annotations':
        tool['annotations'] = {**(tool.get('annotations') or {}), 'destructiveHint': True,
                               'title': 'Synthetic drift'}
    elif component == 'outputSchema':
        tool['outputSchema'] = {'type': 'object', 'properties': {'synthetic': {'type': 'string'}}}
    elif component == 'title':
        tool['title'] = 'Synthetic drift'
    else:
        raise AssertionError(component)
    return tools


COMPONENTS = ('inputSchema', 'description', 'annotations', 'outputSchema', 'title')


class Transport(upstream_read.McpReadGatewayTransport):
    """The production transport; only the operation budget is shortened."""
    budget = None

    async def execute_read(self, tool_name, arguments, *, timeout_seconds, **kwargs):
        return await super().execute_read(tool_name, arguments,
                                          timeout_seconds=self.budget or timeout_seconds, **kwargs)


class MemoryPeer:
    """Synthetic MCP peer; actual ClientSession framing and task-group teardown."""
    def __init__(self, tools, entity_id, *, version='8.5.0', protocol='2025-03-26'):
        self.tools, self.entity_id = tools, entity_id
        self.version, self.protocol = version, protocol
        self.events, self.calls = [], []
        self.writes, self.state, self.percentage = 0, 'on', 50
        self.expected = None
        self.hooks = {}  # tool name -> async callable(count) run before replying
        self.hang = set()
        self.drop = set()  # apply the call, then close without a response
        self.close_on_list = False
        self.rotate_on_list = False
        self.connections = []

    def rotate(self):
        for connection in self.connections:
            connection['id'] = 'synthetic-rotated-' + uuid.uuid4().hex

    async def serve(self, incoming, outgoing, connection):
        async with incoming, outgoing:
            async for message in incoming:
                request = message.message.root
                self.events.append(request.method)
                if not isinstance(request, types.JSONRPCRequest):
                    continue
                if request.method == 'initialize':
                    result = {'protocolVersion': self.protocol, 'capabilities': {'tools': {}},
                              'serverInfo': {'name': 'ha-mcp', 'version': self.version}}
                elif request.method == 'tools/list':
                    if self.close_on_list:
                        return
                    result = {'tools': self.tools}
                    if self.rotate_on_list:
                        connection['id'] = 'synthetic-rotated-' + uuid.uuid4().hex
                elif request.method == 'tools/call':
                    name, args = request.params['name'], request.params['arguments']
                    self.calls.append((name, deepcopy(args)))
                    count = sum(1 for called, _ in self.calls if called == name)
                    if name in self.hooks:
                        await self.hooks[name](count)
                    if name in self.hang:
                        await anyio.sleep_forever()
                    if name == 'ha_list_services':
                        value = {'success': True, 'services': {args['domain'] + '.' + action: {}
                                 for action in ('turn_off', 'turn_on', 'set_percentage')}}
                    elif name == 'ha_get_state':
                        value = {'data': {'entity_id': self.entity_id, 'state': self.state,
                                         'attributes': {'percentage': self.percentage,
                                                        'supported_features': 49},
                                         'last_updated': f'synthetic-revision-{self.writes}'}}
                    elif name == 'ha_call_service':
                        assert args == self.expected, (args, self.expected)
                        self.writes += 1
                        self.state = 'off' if args['service'] == 'turn_off' else 'on'
                        if 'percentage' in args['data']:
                            self.percentage = args['data']['percentage']
                        if name in self.drop:
                            return
                        value = {'success': True}
                    else:
                        raise AssertionError('Unexpected synthetic dispatch')
                    result = {'content': [], 'structuredContent': value, 'isError': False}
                else:
                    raise AssertionError('Unexpected MCP method')
                await outgoing.send(SessionMessage(types.JSONRPCMessage(
                    types.JSONRPCResponse(jsonrpc='2.0', id=request.id, result=result))))

    @asynccontextmanager
    async def connect(self, unused, **kwargs):
        assert unused == ''  # no endpoint supplied; external transport is replaced
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


class Harness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = NOW
        self.telemetry, token = begin_request('synthetic-HAMCP-175')
        self.addCleanup(end_request, token)
        signer = CoreSigner()  # ephemeral test key; no production signing material
        entries = [core_entry(v, typed_operations=True) for v in ('2026.10.0', '2026.10.1')]
        for entry in entries:
            entry['image_index_digest'] = 'sha256:' + sha256(('synthetic-' + entry['version']).encode()).hexdigest()
        raw = signer.journal_raw(entries=entries)
        async def fetch(*args):
            return raw
        self.registry = CoreReleaseRegistry(enabled=True, public_key=signer.public_key_base64,
            cache_path=self.root / 'registry.json', fetcher=fetch, now=lambda: NOW)
        self.core = CoreRuntime()
        self.source = ProjectedCoreSource(self.registry, '2026.10.1', typed_operations=True)
        self.core.configure(object(), source=self.source, release_registry=self.registry)
        await self.core.reconcile_once('synthetic-investigation')
        self.authority = 'synthetic-authority'

    def operation(self, kind='power', extra_names=(), *, action='turn_off', tools=None):
        provider_type, _, service_type, core_type, _, _, entity, binding, _ = KINDS[kind]
        peer = MemoryPeer(tools if tools is not None else catalog(extra_names).tools, entity)
        transport = Transport('', timeout_seconds=3, client_version='synthetic')
        provider = provider_type(transport, lambda *args: self.authority)
        service = service_type(self.root / uuid.uuid4().hex, provider, core_type(self.core),
                               now=lambda: self.clock)
        self.addAsyncCleanup(service.close)
        request = self.request(kind, action=action)
        peer.expected = request.arguments()
        return peer, service, request

    def request(self, kind, *, action='turn_off', operation_id=None):
        request_type = PowerRequest if kind == 'power' else FanRequest
        request = request_type(entity_id=KINDS[kind][6], action=action,
                               operation_id=operation_id or f'{int(NOW.timestamp())}-{uuid.uuid4().hex}')
        setattr(self.telemetry, KINDS[kind][7], digest(request.model_dump()))
        return request

    async def public_call(self, peer, service, request):
        kind = 'power' if isinstance(service, PowerService) else 'fan'
        runtime, tool = KINDS[kind][4], KINDS[kind][5]
        with patch.object(upstream_read, 'streamablehttp_client', peer.connect), \
             patch.object(runtime, 'service', service):
            return json.loads(await tool(**request.model_dump()))

    async def read_task(self, service, task_id):
        runtime = POWER_OPERATIONS if isinstance(service, PowerService) else FAN_OPERATIONS
        with patch.object(runtime, 'service', service):
            return json.loads(await get_execution_task(task_id))

    def balanced(self):
        self.assertEqual(self.telemetry.upstream_active_requests, 0)

    def assert_pre_save_refusal(self, result, peer, service, request, category):
        kind = 'power' if isinstance(service, PowerService) else 'fan'
        self.assertFalse(result['success'], result)
        self.assertEqual(result['error_code'], 'provider_unavailable', result)
        self.assertFalse(result['retryable'])
        self.assertIn(f'{kind}_{category}', json.dumps(result))
        self.assertNotIn('task_id', result['metadata'])
        self.assertEqual(result['metadata']['operation_id'], request.operation_id)
        self.assertEqual(result['metadata']['stage'], 'preparation_failed')
        self.assertEqual(result['metadata']['dispatch'], 'no_dispatch')
        self.assertEqual(result['metadata']['reconciliation'], 'no_task')
        self.assertTrue(result['timing']['upstream_attempted'])
        self.assertGreaterEqual(result['timing']['upstream_request_count'], 1)
        self.assertNotIn('ha_call_service', [name for name, _ in peer.calls])
        self.assertEqual(peer.writes, 0)
        self.assertIsNone(service.load(request.task_id))
        self.assertEqual(service.health()['receipt_count'], 0)
        self.assertEqual(service.locks.records(), ())
        self.assertEqual(self.telemetry.audit_context[KINDS[kind][8]], 'preparation_failed_no_task')
        self.assertNotIn('task_id', self.telemetry.audit_context)
        self.balanced()


class DependencyScopedAdmission(Harness):
    async def test_stock_77_succeeds_on_both_core_versions_and_same_id_never_redispatches(self):
        for kind in KINDS:
            for version in ('2026.10.0', '2026.10.1'):
                with self.subTest(kind=kind, core=version):
                    self.source.version = version
                    await self.core.reconcile_once('synthetic-core-change')
                    peer, service, request = self.operation(kind)
                    result = await self.public_call(peer, service, request)
                    self.assertEqual(result.get('data', {}).get('state'), 'succeeded_verified', result)
                    self.assertEqual(result['data']['core_binding']['version'], version)
                    self.assertEqual(result['metadata']['task_id'], request.task_id)
                    self.assertEqual(peer.writes, 1)
                    replay = await self.public_call(peer, service, request)
                    self.assertEqual(replay['data'], result['data'])
                    read = await self.read_task(service, request.task_id)
                    self.assertEqual(read['data']['state'], 'succeeded_verified')
                    self.assertEqual(peer.writes, 1)
                    self.assertEqual(service.locks.records(), ())

    async def test_80_tools_power_and_fan_each_succeed_once_and_extras_are_never_called(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.telemetry.upstream_request_count = 0
                peer, service, request = self.operation(kind, EXTRAS)
                self.assertEqual(len(peer.tools), 80)
                result = await self.public_call(peer, service, request)
                self.assertEqual(result.get('data', {}).get('state'), 'succeeded_verified', result)
                self.assertEqual(peer.writes, 1)
                called = [name for name, _ in peer.calls]
                self.assertTrue(set(called) <= set(REQUIRED_TOOLS), called)
                self.assertEqual(called.count('ha_call_service'), 1)
                self.assertEqual([args for name, args in peer.calls if name == 'ha_call_service'],
                                 [request.arguments()])
                # One count per provider exchange (an MCP session), never per action.
                self.assertTrue(result['timing']['upstream_attempted'])
                self.assertEqual(result['timing']['upstream_request_count'], peer.events.count('initialize'))
                self.assertEqual(result['timing']['upstream_request_count'], len(peer.calls))
                self.assertEqual(self.telemetry.upstream_max_concurrent_requests, 2)
                self.balanced()
                replay = await self.public_call(peer, service, request)
                self.assertEqual(replay['data'], result['data'])
                read = await self.read_task(service, request.task_id)
                self.assertEqual(read['data']['state'], 'succeeded_verified')
                self.assertEqual(peer.writes, 1)
                self.assertEqual(service.locks.records(), ())

    async def test_fan_percentage_with_80_tools_dispatches_exact_arguments_once(self):
        peer, service, request = self.operation('fan', EXTRAS)
        request = FanRequest(entity_id='fan.synthetic', action='set_percentage', percentage=30,
                             operation_id=f'{int(NOW.timestamp())}-{uuid.uuid4().hex}')
        self.telemetry.ordinary_fan_binding = digest(request.model_dump())
        peer.expected = request.arguments()
        result = await self.public_call(peer, service, request)
        self.assertEqual(result.get('data', {}).get('state'), 'succeeded_verified', result)
        self.assertEqual((peer.writes, peer.state, peer.percentage), (1, 'on', 30))

    async def test_extras_are_undispatchable_through_either_provider(self):
        for kind in KINDS:
            provider_type, refusal = KINDS[kind][:2]
            peer = MemoryPeer(catalog(EXTRAS).tools, KINDS[kind][6])
            provider = provider_type(Transport('', timeout_seconds=3, client_version='synthetic'),
                                     lambda *args: self.authority)
            before = self.telemetry.upstream_request_count
            for name in EXTRAS:
                with self.subTest(kind=kind, tool=name), \
                     patch.object(upstream_read, 'streamablehttp_client', peer.connect):
                    with self.assertRaisesRegex(refusal, 'provider_tool_unreviewed'):
                        await provider._call(name, {}, lambda: None)
            self.assertEqual(peer.events, [])
            self.assertEqual(self.telemetry.upstream_request_count, before)

    async def test_read_subset_still_exposes_25_and_does_not_admit_extras_or_writes(self):
        from mcp.server.fastmcp import FastMCP
        from ha_mcp_engineering import capabilities
        from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
        from test_ha_mcp_production_readmission import _settings
        from test_upstream_8_5_compatibility import GatewayTransport
        for name in ('_DYNAMIC_UPSTREAM_CAPABILITIES', '_UPSTREAM_READ_GATEWAY_SUMMARY'):
            self.enterContext(patch.object(capabilities, name, deepcopy(getattr(capabilities, name))))
        gateway = UpstreamReadGateway()
        gateway.configure(replace(_settings('synthetic-no-key'), ha_mcp_release_registry_enabled=False),
                          transport=GatewayTransport(catalog(EXTRAS).tools))
        await gateway.initialize(FastMCP('synthetic-investigation'))
        tools = gateway._registered_tool_registry.snapshot()
        self.assertEqual(len(tools), 25)
        self.assertFalse(set(EXTRAS).intersection(tools))
        self.assertNotIn('ha_call_service', tools)
        self.assertIsInstance(gateway.power_provider_authority_token('8.5.0'), str)

    async def test_whole_catalog_validator_is_unchanged_and_no_longer_gates_power_or_fan(self):
        release = load_reviewed_upstream_release_registry().by_version['8.5.0']
        for extras in ((), EXTRAS, (EXTRAS[0],)):
            c = catalog(extras)
            result = validate_reviewed_release_catalog(release,
                observed_server_name=c.server_name, observed_upstream_version=c.server_version,
                observed_protocol_version=c.protocol_version, tools=c.tools)
            self.assertEqual(result.valid, not extras)
            self.assertEqual(result.additional_tool_count, len(extras))
            for provider_type, _ in (KINDS['power'][:2], KINDS['fan'][:2]):
                version, contract, authority = provider_type(None, lambda *a: 'a').validate_catalog(c)
                self.assertEqual((version, authority), ('8.5.0', 'a'))
                self.assertIn(contract, provider_type.releases['8.5.0'])


class AdmissionRefusals(Harness):
    def assert_refuses(self, provider_type, refusal, c, category, **kwargs):
        with self.assertRaises(refusal) as caught:
            provider_type(None, lambda *a: 'a').validate_catalog(c, **kwargs)
        self.assertEqual(str(caught.exception).split('_', 1)[1], category)
        return caught.exception

    def test_each_required_component_drift_refuses_with_or_without_extras(self):
        for kind in KINDS:
            provider_type, refusal = KINDS[kind][:2]
            for extras in ((), EXTRAS):
                base = catalog(extras)
                for name in REQUIRED_TOOLS:
                    for component in COMPONENTS:
                        with self.subTest(kind=kind, extras=len(extras), tool=name, component=component):
                            c = replace(base, tools=tuple(drifted(base.tools, name, component)))
                            error = self.assert_refuses(provider_type, refusal, c, 'provider_contract_mismatch')
                            self.assertEqual(error.code, ErrorCode.PROVIDER_UNAVAILABLE)

    def test_unrequired_reviewed_tool_drift_grants_nothing_and_takes_nothing(self):
        base = catalog()
        other = next(t['name'] for t in base.tools if t['name'] not in REQUIRED_TOOLS)
        c = replace(base, tools=tuple(drifted(base.tools, other, 'inputSchema')))
        for kind in KINDS:
            KINDS[kind][0](None, lambda *a: 'a').validate_catalog(c)

    def test_missing_or_duplicated_required_tool_refuses(self):
        for kind in KINDS:
            provider_type, refusal = KINDS[kind][:2]
            for name in REQUIRED_TOOLS:
                tools = list(catalog(EXTRAS).tools)
                index = next(i for i, tool in enumerate(tools) if tool['name'] == name)
                for shape, changed in (('missing', tools[:index] + tools[index + 1:]),
                                       ('duplicate', tools + [deepcopy(tools[index])])):
                    with self.subTest(kind=kind, tool=name, shape=shape):
                        c = replace(catalog(), tools=tuple(changed))
                        self.assert_refuses(provider_type, refusal, c, 'provider_contract_mismatch')

    def test_malformed_extra_descriptor_refuses(self):
        for bad in ({'name': 'not a valid name', 'inputSchema': {}}, 'not-a-descriptor'):
            c = replace(catalog(), tools=catalog().tools + (bad,))
            for kind in KINDS:
                with self.subTest(kind=kind, bad=str(bad)[:20]):
                    self.assert_refuses(*KINDS[kind][:2], c, 'provider_contract_mismatch')

    def test_version_protocol_hold_and_revocation_refuse(self):
        registry = load_reviewed_upstream_release_registry()
        release = registry.by_version['8.5.0']
        held = replace(registry, releases=tuple(
            replace(item, provider_dispositions=(('read_gateway', 'held'),)) if item is release else item
            for item in registry.releases))
        revoked = replace(registry, releases=tuple(
            replace(item, revoked=True) if item is release else item for item in registry.releases))
        for kind in KINDS:
            provider_type, refusal = KINDS[kind][:2]
            with self.subTest(kind=kind, change='unreviewed version'):
                self.assert_refuses(provider_type, refusal, catalog(version='8.9.9'), 'provider_identity_unavailable')
            with self.subTest(kind=kind, change='other reviewed version'):
                self.assert_refuses(provider_type, refusal, catalog(version='8.4.3'), 'provider_contract_mismatch')
            with self.subTest(kind=kind, change='protocol'):
                self.assert_refuses(provider_type, refusal, catalog(protocol='2024-11-05'), 'provider_identity_unavailable')
            with self.subTest(kind=kind, change='incomplete catalog'):
                self.assert_refuses(provider_type, refusal, replace(catalog(), catalog_complete=False),
                                    'provider_identity_unavailable')
            with self.subTest(kind=kind, change='expected contract'):
                self.assert_refuses(provider_type, refusal, catalog(), 'provider_identity_unavailable',
                                    expected_contract='synthetic-other-contract')
            for change, value in (('hold', held), ('revocation', revoked)):
                with self.subTest(kind=kind, change=change), \
                     patch.object(upstream_fan, 'load_reviewed_upstream_release_registry', lambda: value):
                    self.assert_refuses(provider_type, refusal, catalog(EXTRAS), 'provider_identity_unavailable')

    def test_dependency_helper_preserves_release_identity_and_contract_components(self):
        release = load_reviewed_upstream_release_registry().by_version['8.5.0']
        c = catalog(EXTRAS)
        def check(rel=release, *, name='ha-mcp', version='8.5.0', protocol='2025-03-26',
                  tools=c.tools, required=REQUIRED_TOOLS):
            return validate_reviewed_release_dependencies(rel, observed_server_name=name,
                observed_upstream_version=version, observed_protocol_version=protocol,
                tools=tools, required_tools=required)
        self.assertTrue(check())
        self.assertFalse(check(replace(release, revoked=True)))
        self.assertFalse(check(name='other'))
        self.assertFalse(check(version='8.4.3'))
        self.assertFalse(check(protocol='2024-11-05'))
        self.assertFalse(check(required=()))
        self.assertFalse(check(required=('ha_get_state', 'ha_get_state')))
        self.assertFalse(check(required=('ha_read_file',)))  # no reviewed contract
        self.assertFalse(check(replace(release, runtime_contract_fingerprint_model='synthetic-model')))
        for component in COMPONENTS:
            self.assertFalse(check(tools=tuple(drifted(c.tools, 'ha_call_service', component))), component)

    async def test_public_drift_missing_and_duplicate_refuse_before_any_task(self):
        for kind in KINDS:
            base = catalog(EXTRAS).tools
            index = next(i for i, tool in enumerate(base) if tool['name'] == 'ha_call_service')
            shapes = {'missing': base[:index] + base[index + 1:],
                      'duplicate': base + (deepcopy(base[index]),)}
            shapes.update({component: tuple(drifted(base, 'ha_call_service', component))
                           for component in COMPONENTS})
            for shape, tools in shapes.items():
                with self.subTest(kind=kind, shape=shape):
                    peer, service, request = self.operation(kind, tools=tools)
                    result = await self.public_call(peer, service, request)
                    self.assert_pre_save_refusal(result, peer, service, request, 'provider_contract_mismatch')
                    self.assertEqual(peer.calls, [])
                    self.assertEqual(peer.events, ['initialize', 'notifications/initialized', 'tools/list'])

    async def test_public_version_change_refuses_before_any_task(self):
        for kind in KINDS:
            peer, service, request = self.operation(kind, EXTRAS)
            peer.version = '8.9.9'
            result = await self.public_call(peer, service, request)
            self.assert_pre_save_refusal(result, peer, service, request, 'provider_identity_unavailable')

    async def test_retirement_between_discovery_and_dispatch_never_writes(self):
        for kind in KINDS:
            for retired in ('session', 'provider', 'connector', 'core'):
                with self.subTest(kind=kind, retired=retired):
                    self.authority = 'synthetic-authority'
                    self.source.version = '2026.10.1'
                    await self.core.reconcile_once('synthetic-core-restore')
                    peer, service, request = self.operation(kind, EXTRAS)
                    async def retire(count):
                        # The third state read is the pre-dispatch revalidation
                        # inside the ha_call_service exchange.
                        if count != 3:
                            return
                        if retired == 'session':
                            peer.rotate()
                        elif retired == 'provider':
                            self.authority = 'synthetic-retired'
                        elif retired == 'connector':
                            setattr(self.telemetry, KINDS[kind][7], None)
                        else:
                            self.source.version = '2026.10.0'
                            await self.core.reconcile_once('synthetic-core-change')
                    peer.hooks['ha_get_state'] = retire
                    result = await self.public_call(peer, service, request)
                    self.assertNotEqual(result.get('data', {}).get('state'), 'succeeded_verified', result)
                    self.assertNotIn('ha_call_service', [name for name, _ in peer.calls])
                    self.assertEqual(peer.writes, 0)
                    self.assertNotIn(SECRET, json.dumps(result))
                    await asyncio.gather(*list(service.active.values()), return_exceptions=True)
                    if retired == 'session':
                        # Rotation is detected after durable intent: no tools/call
                        # was sent, but that cannot be proven, so it stays
                        # uncertain and retains its locks for read-only recovery.
                        self.assertTrue(result['data']['dispatch_intent_recorded'], result)
                        self.assertFalse(result['data']['provider_response_received'])
                        self.assertFalse(result['data']['terminal'])
                        self.assertEqual(result['metadata']['task_id'], request.task_id)
                        self.assertNotEqual(service.locks.records(), ())
                    else:
                        self.assertEqual(service.locks.records(), ())
                    self.balanced()

    async def test_session_change_after_discovery_keeps_transport_semantics(self):
        peer = MemoryPeer(catalog(EXTRAS).tools, 'switch.synthetic')
        peer.rotate_on_list = True
        provider = PowerProvider(Transport('', timeout_seconds=3, client_version='synthetic'),
                                 lambda *a: self.authority)
        with patch.object(upstream_read, 'streamablehttp_client', peer.connect):
            with self.assertRaises(DashboardTransportError) as caught:
                await provider.state('switch.synthetic', lambda: None)
        self.assertEqual(caught.exception.category, 'protocol_error')
        self.assertEqual(peer.calls, [])
        self.balanced()


class TruthfulRefusal(Harness):
    def provider(self, kind, peer):
        return KINDS[kind][0](Transport('', timeout_seconds=3, client_version='synthetic'),
                              lambda *a: self.authority)

    async def test_grouped_validator_refusal_keeps_its_bounded_category(self):
        for kind in KINDS:
            peer = MemoryPeer(drifted(catalog(EXTRAS).tools, 'ha_get_state', 'inputSchema'), KINDS[kind][6])
            with self.subTest(kind=kind), patch.object(upstream_read, 'streamablehttp_client', peer.connect):
                with self.assertRaises(KINDS[kind][1]) as caught:
                    await self.provider(kind, peer).state(KINDS[kind][6], lambda: None)
                self.assertEqual(str(caught.exception), f'{kind}_provider_contract_mismatch')
                self.assertIsNone(caught.exception.__cause__)
                self.assertTrue(caught.exception.__suppress_context__)
                self.assertEqual(peer.calls, [])

    async def test_grouped_before_dispatch_refusal_and_foreign_error_stay_bounded(self):
        for kind in KINDS:
            refusal = KINDS[kind][1]
            for raised, expected in ((refusal(f'{kind}_connector_authorization_required'),
                                      f'{kind}_connector_authorization_required'),
                                     (ValueError(SECRET), f'{kind}_provider_precondition_failed')):
                peer = MemoryPeer(catalog(EXTRAS).tools, KINDS[kind][6])
                def authorize():
                    raise raised
                with self.subTest(kind=kind, raised=type(raised).__name__), \
                     patch.object(upstream_read, 'streamablehttp_client', peer.connect):
                    with self.assertRaises(refusal) as caught:
                        await self.provider(kind, peer).state(KINDS[kind][6], authorize)
                    self.assertEqual(str(caught.exception), expected)
                    self.assertNotIn(SECRET, repr(caught.exception.args))
                    self.assertIsNone(caught.exception.__cause__)
                    self.assertEqual(peer.calls, [])
                    self.balanced()

    async def test_direct_unwrapped_failures_map_the_same_way(self):
        for kind in KINDS:
            refusal = KINDS[kind][1]
            for failure, expected in (
                (upstream_read.CatalogValidationFailure(refusal(f'{kind}_provider_contract_mismatch')),
                 f'{kind}_provider_contract_mismatch'),
                (upstream_read.CatalogValidationFailure(ValueError(SECRET)), f'{kind}_provider_admission_failed'),
                (upstream_read.BeforeDispatchFailure(ValueError(SECRET)), f'{kind}_provider_precondition_failed'),
            ):
                class Direct:
                    async def execute_read(self, *args, **kwargs):
                        raise failure
                with self.subTest(kind=kind, expected=expected):
                    with self.assertRaises(refusal) as caught:
                        await KINDS[kind][0](Direct(), lambda *a: 'a').state(KINDS[kind][6], lambda: None)
                    self.assertEqual(str(caught.exception), expected)
                    self.assertNotIn(SECRET, repr(caught.exception.args))

    def test_operation_error_mapper_unwraps_only_a_sole_typed_leaf(self):
        validator = upstream_read.CatalogValidationFailure(PowerRefusal('power_provider_contract_mismatch'))
        before = upstream_read.BeforeDispatchFailure(ValueError(SECRET))
        mapper = upstream_read.McpReadGatewayTransport._map_operation_error
        self.assertIs(mapper(ExceptionGroup('g', [validator])), validator)
        self.assertIs(mapper(BaseExceptionGroup('g', [ExceptionGroup('n', [before])])), before)
        for ambiguous in (ExceptionGroup('g', [validator, OSError(SECRET)]),
                          ExceptionGroup('g', [validator, before]),
                          ExceptionGroup('g', [OSError(SECRET)])):
            mapped = mapper(ambiguous)
            self.assertIsInstance(mapped, DashboardTransportError)
            self.assertNotIn(SECRET, str(mapped))
        cancelled = asyncio.CancelledError()
        self.assertIs(mapper(cancelled), cancelled)

    def test_admission_refusals_map_to_non_retryable_provider_unavailable(self):
        for kind in KINDS:
            refusal = KINDS[kind][1]
            for category in ('provider_identity_unavailable', 'provider_contract_mismatch',
                             'provider_admission_failed'):
                code, message, retryable, _ = map_exception(refusal(f'{kind}_{category}'))
                self.assertEqual((code, retryable), (ErrorCode.PROVIDER_UNAVAILABLE, False))
                self.assertEqual(message, f'{kind}_{category}')  # code-owned category only
            for category in ('provider_precondition_failed', 'operation_id_expired', 'dispatch_refused'):
                code, _, _, _ = map_exception(refusal(f'{kind}_{category}'))
                self.assertEqual(code, ErrorCode.INVALID_REQUEST)

    async def test_real_transport_failure_keeps_transport_semantics(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                peer, service, request = self.operation(kind, EXTRAS)
                peer.close_on_list = True
                result = await self.public_call(peer, service, request)
                self.assertFalse(result['success'])
                self.assertEqual(result['error'], 'DashboardTransportError', result)
                self.assertNotEqual(result['error_code'], 'provider_unavailable')
                self.assertEqual(result['metadata']['reconciliation'], 'no_task')
                self.assertNotIn('task_id', result['metadata'])
                self.assertTrue(result['timing']['upstream_attempted'])
                self.assertEqual(peer.writes, 0)
                self.assertIsNone(service.load(request.task_id))
                self.balanced()

    async def test_cancellation_propagates_without_a_task_or_refusal(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                peer, service, request = self.operation(kind, EXTRAS)
                peer.hang.add('ha_list_services')
                before = self.telemetry.upstream_request_count
                pending = asyncio.create_task(self.public_call(peer, service, request))
                async with asyncio.timeout(10):
                    while 'tools/call' not in peer.events and not pending.done():
                        await asyncio.sleep(0.01)
                self.assertFalse(pending.done())
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
                self.assertEqual(self.telemetry.upstream_request_count, before + 1)
                self.balanced()
                self.assertIsNone(service.load(request.task_id))
                self.assertEqual(peer.writes, 0)

    async def test_before_dispatch_persistence_failure_never_writes(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                peer, service, request = self.operation(kind, EXTRAS)
                def fail(*args, **kwargs):
                    raise OSError(SECRET)
                with patch.object(service.executions, 'commit_dispatch_intent', fail):
                    result = await self.public_call(peer, service, request)
                text = json.dumps(result)
                self.assertNotIn(SECRET, text)
                self.assertNotEqual(result.get('data', {}).get('state'), 'succeeded_verified', result)
                self.assertNotIn('ha_call_service', [name for name, _ in peer.calls])
                self.assertEqual(peer.writes, 0)
                # A declaration exists, so the pointer is truthful and stays.
                self.assertIsNotNone(service.load(request.task_id))
                self.assertEqual(result['metadata']['task_id'], request.task_id)
                self.assertEqual(service.locks.records(), ())
                replay = await self.public_call(peer, service, request)
                self.assertNotEqual(replay.get('data', {}).get('state'), 'succeeded_verified', replay)
                self.assertEqual(peer.writes, 0)
                self.balanced()

    async def test_uncertain_post_intent_response_loss_stays_uncertain_then_reads_back(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                peer, service, request = self.operation(kind, EXTRAS)
                peer.drop.add('ha_call_service')
                service.provider.transport.budget = 1
                result = await self.public_call(peer, service, request)
                self.assertEqual(peer.writes, 1)
                self.assertTrue(result['success'], result)
                self.assertFalse(result['data']['provider_response_received'])
                self.assertTrue(result['data']['dispatch_intent_recorded'])
                self.assertEqual(result['metadata']['task_id'], request.task_id)
                # Read-only readback proves the effect; nothing is replayed.
                peer.drop.clear()
                read = await self.read_task(service, request.task_id)
                self.assertEqual(read['data']['state'], 'succeeded_verified', read)
                await self.public_call(peer, service, request)
                self.assertEqual(peer.writes, 1)
                self.assertEqual(service.locks.records(), ())
                self.balanced()


class ExchangeTelemetry(Harness):
    async def test_refused_discovery_counts_one_finished_exchange(self):
        peer = MemoryPeer(drifted(catalog().tools, 'ha_call_service', 'description'), 'switch.synthetic')
        provider = PowerProvider(Transport('', timeout_seconds=3, client_version='synthetic'), lambda *a: 'a')
        with patch.object(upstream_read, 'streamablehttp_client', peer.connect):
            with self.assertRaises(PowerRefusal):
                await provider.state('switch.synthetic', lambda: None)
        self.assertEqual(self.telemetry.upstream_request_count, 1)
        self.assertGreater(self.telemetry.upstream_duration_ms, 0)
        self.assertEqual(self.telemetry.provider_dispatch_count, 0)  # no action fact
        self.balanced()

    async def test_positive_read_counts_one_exchange_and_one_action_fact(self):
        peer = MemoryPeer(catalog(EXTRAS).tools, 'switch.synthetic')
        provider = PowerProvider(Transport('', timeout_seconds=3, client_version='synthetic'), lambda *a: 'a')
        with patch.object(upstream_read, 'streamablehttp_client', peer.connect):
            state = await provider.state('switch.synthetic', lambda: None)
        self.assertEqual(state['state'], 'on')
        self.assertEqual(self.telemetry.upstream_request_count, 1)
        self.assertEqual(self.telemetry.provider_dispatch_count, 1)
        self.assertEqual(self.telemetry.upstream_max_concurrent_requests, 1)
        self.balanced()

    async def test_timeout_finishes_the_exchange(self):
        peer = MemoryPeer(catalog(EXTRAS).tools, 'switch.synthetic')
        peer.hang.add('ha_get_state')
        transport = Transport('', timeout_seconds=3, client_version='synthetic')
        transport.budget = 1
        provider = PowerProvider(transport, lambda *a: 'a')
        started = time.perf_counter()
        with patch.object(upstream_read, 'streamablehttp_client', peer.connect):
            with self.assertRaises(DashboardTransportError) as caught:
                await provider.state('switch.synthetic', lambda: None)
        self.assertEqual(caught.exception.category, 'timeout')
        self.assertEqual(self.telemetry.upstream_request_count, 1)
        self.assertGreaterEqual(self.telemetry.upstream_duration_ms, 900)
        self.assertLessEqual(self.telemetry.upstream_duration_ms, (time.perf_counter() - started) * 1000 + 1)
        self.assertEqual(self.telemetry.provider_dispatch_count, 0)
        self.balanced()

    async def test_public_timing_never_reports_an_unattempted_exchange(self):
        peer, service, request = self.operation('power', EXTRAS)
        setattr(self.telemetry, 'ordinary_power_binding', None)  # refused before contact
        result = await self.public_call(peer, service, request)
        self.assertFalse(result['success'])
        self.assertFalse(result['timing']['upstream_attempted'])
        self.assertEqual(result['timing']['upstream_request_count'], 0)
        self.assertEqual(peer.events, [])


class TaskGuidance(Harness):
    async def test_same_id_is_reconciliation_only_once_a_declaration_exists(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                tools = drifted(catalog(EXTRAS).tools, 'ha_call_service', 'inputSchema')
                peer, service, request = self.operation(kind, tools=tools)
                refused = await self.public_call(peer, service, request)
                self.assert_pre_save_refusal(refused, peer, service, request, 'provider_contract_mismatch')
                read = await self.read_task(service, request.task_id)
                self.assertFalse(read['success'])
                # No tombstone: the reviewed contract restored, the same fresh ID
                # proceeds once, and only then becomes reconciliation-only.
                peer.tools = catalog(EXTRAS).tools
                retried = await self.public_call(peer, service, request)
                self.assertEqual(retried.get('data', {}).get('state'), 'succeeded_verified', retried)
                self.assertEqual(retried['metadata']['task_id'], request.task_id)
                await self.public_call(peer, service, request)
                self.assertEqual(peer.writes, 1)
                self.assertEqual(service.locks.records(), ())

    async def test_expired_id_after_pre_save_failure_refuses_without_pointer_or_dispatch(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.clock = NOW
                tools = drifted(catalog().tools, 'ha_list_services', 'description')
                peer, service, request = self.operation(kind, tools=tools)
                await self.public_call(peer, service, request)
                self.clock = NOW + timedelta(seconds=301)
                peer.tools = catalog().tools
                expired = await self.public_call(peer, service, request)
                self.assertFalse(expired['success'])
                self.assertIn('operation_id_expired', json.dumps(expired))
                self.assertEqual(expired['metadata']['reconciliation'], 'no_task')
                self.assertNotIn('task_id', expired['metadata'])
                self.assertEqual(peer.writes, 0)
                self.assertIsNone(service.load(request.task_id))
                self.clock = NOW

    async def test_rebound_id_after_saved_task_never_dispatches_and_keeps_pointer(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                peer, service, request = self.operation(kind, EXTRAS)
                await self.public_call(peer, service, request)
                self.assertEqual(peer.writes, 1)
                rebound = self.request(kind, action='turn_on', operation_id=request.operation_id)
                peer.expected = rebound.arguments()
                result = await self.public_call(peer, service, rebound)
                self.assertFalse(result['success'])
                self.assertIn('operation_id_rebound', json.dumps(result))
                self.assertEqual(result['metadata']['task_id'], request.task_id)
                self.assertEqual(peer.writes, 1)
                self.assertEqual(service.locks.records(), ())

    async def test_unknown_durable_state_keeps_the_conservative_pointer(self):
        peer, service, request = self.operation('power')
        with patch.object(PowerService, '_path', side_effect=OSError(SECRET)):
            guidance = service.failure_guidance(request)
        self.assertEqual(guidance, {'task_id': request.task_id, 'reconciliation': 'get_execution_task'})


if __name__ == '__main__':
    unittest.main()
