"""HAMCP-175: characterize beta.5's defect, without changing production code.

The 77 descriptors come from committed exact-8.5.0 evidence in runtime order.
The additional descriptors have the three reported live names and deliberately
synthetic, empty contracts: this tests the name-set/count dependency, not a
byte-identical live catalog. No endpoint, household target or filesystem path
is supplied to the peer. The real pinned MCP ClientSession uses memory streams.
"""
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
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
from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.ha_core_readmission.runtime import CoreRuntime
from ha_mcp_engineering.power.contracts import PowerRequest, PowerRefusal, digest
from ha_mcp_engineering.power.service import PowerService, PowerCoreAuthority, POWER_OPERATIONS
from ha_mcp_engineering.providers.upstream_power import PowerProvider
from ha_mcp_engineering.providers.upstream_fan import FanProvider
from ha_mcp_engineering.fan.contracts import FanRefusal, FanRequest
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.tools.power import control_power
from ha_mcp_engineering.tools.governance import get_execution_task, GOVERNANCE
from ha_mcp_engineering.upstream_tool_policy import (
    load_reviewed_upstream_release_registry, validate_reviewed_release_catalog,
)

EXTRAS = ('ha_config_get_yaml', 'ha_list_files', 'ha_read_file')


def catalog(extra_names=()):
    folder = ROOT / 'docs/evidence/upstream-read-compatibility'
    raw = json.loads((folder / 'ha-mcp-8.5.0.json').read_text())
    review = json.loads((folder / 'ha-mcp-8.5.0-contract-review.json').read_text())
    by_name = {tool['name']: tool for tool in raw['tools']}
    tools = [by_name[name] for name in review['runtime_catalog']['runtime_tool_order']]
    tools.extend({'name': name, 'description': 'Synthetic unreviewed tool; never called.',
                  'inputSchema': {'type': 'object', 'properties': {}}}
                 for name in extra_names)
    return upstream_read.McpReadCatalog('2025-03-26', 'ha-mcp', '8.5.0', tuple(tools), 0)


class MemoryPeer:
    """Synthetic MCP peer; actual ClientSession framing and task-group teardown."""
    def __init__(self, tools):
        self.tools = tools
        self.events = []
        self.calls = []
        self.writes = 0
        self.state = 'on'

    async def serve(self, incoming, outgoing):
        async with incoming, outgoing:
            async for message in incoming:
                request = message.message.root
                self.events.append(request.method)
                if not isinstance(request, types.JSONRPCRequest):
                    continue
                if request.method == 'initialize':
                    result = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}},
                              'serverInfo': {'name': 'ha-mcp', 'version': '8.5.0'}}
                elif request.method == 'tools/list':
                    result = {'tools': self.tools}
                elif request.method == 'tools/call':
                    name, args = request.params['name'], request.params['arguments']
                    self.calls.append((name, deepcopy(args)))
                    if name == 'ha_list_services':
                        value = {'success': True, 'services': {args['domain'] + '.' + action: {}
                                 for action in ('turn_off', 'turn_on', 'set_percentage')}}
                    elif name == 'ha_get_state':
                        value = {'data': {'entity_id': 'switch.synthetic', 'state': self.state,
                                         'last_updated': f'synthetic-revision-{self.writes}'}}
                    elif name == 'ha_call_service':
                        assert args == {'domain': 'switch', 'service': 'turn_off',
                                        'entity_id': 'switch.synthetic', 'data': {}, 'wait': False,
                                        'return_response': False, 'verbose': False}
                        self.writes += 1
                        self.state = 'off'
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
        send_to_client, read = anyio.create_memory_object_stream(10)
        write, read_from_client = anyio.create_memory_object_stream(10)
        async with anyio.create_task_group() as group:
            group.start_soon(self.serve, read_from_client, send_to_client)
            try:
                yield read, write, lambda: 'synthetic-session'
            finally:
                group.cancel_scope.cancel()


class PowerCatalogReproduction(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
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

    def operation(self, extra_names=()):
        peer = MemoryPeer(catalog(extra_names).tools)
        transport = upstream_read.McpReadGatewayTransport('', timeout_seconds=3, client_version='synthetic')
        provider = PowerProvider(transport, lambda *args: 'synthetic-authority')
        service = PowerService(self.root / uuid.uuid4().hex, provider, PowerCoreAuthority(self.core), now=lambda: NOW)
        self.addAsyncCleanup(service.close)
        request = PowerRequest(entity_id='switch.synthetic', action='turn_off',
                               operation_id=f'{int(NOW.timestamp())}-{uuid.uuid4().hex}')
        self.telemetry.ordinary_power_binding = digest(request.model_dump())
        return peer, service, request

    async def public_call(self, peer, service, request):
        with patch.object(upstream_read, 'streamablehttp_client', peer.connect), \
             patch.object(POWER_OPERATIONS, 'service', service):
            return json.loads(await control_power(**request.model_dump()))

    async def test_stock_77_succeeds_on_both_core_versions_and_same_id_never_redispatches(self):
        for version in ('2026.10.0', '2026.10.1'):
            with self.subTest(core=version):
                self.source.version = version
                await self.core.reconcile_once('synthetic-core-change')
                peer, service, request = self.operation()
                result = await self.public_call(peer, service, request)
                self.assertEqual(result.get('data', {}).get('state'), 'succeeded_verified', result)
                self.assertEqual(result['data']['core_binding']['version'], version)
                self.assertEqual(peer.writes, 1)
                replay = await self.public_call(peer, service, request)
                self.assertEqual(replay['data'], result['data'])
                with patch.object(POWER_OPERATIONS, 'service', service):
                    read = json.loads(await get_execution_task(request.task_id))
                self.assertEqual(read['data']['state'], 'succeeded_verified')
                self.assertEqual(peer.writes, 1)
                self.assertEqual(service.locks.records(), ())

    async def test_80_reproduces_public_error_missing_receipt_and_silent_logging(self):
        peer, service, request = self.operation(EXTRAS)
        with self.assertNoLogs('ha_mcp_engineering', level='WARNING'):
            result = await self.public_call(peer, service, request)
        self.assertEqual(result['error'], 'DashboardTransportError', result)
        self.assertEqual(result['error_code'], 'internal_server_error')
        self.assertFalse(result['retryable'])
        self.assertFalse(result['timing']['upstream_attempted'])
        self.assertEqual(result['timing']['upstream_request_count'], 0)
        self.assertEqual(result['metadata']['task_id'], request.task_id)
        self.assertEqual(peer.events, ['initialize', 'notifications/initialized', 'tools/list'])
        self.assertEqual(peer.calls, [])
        self.assertEqual(peer.writes, 0)
        self.assertEqual(peer.state, 'on')
        self.assertIsNone(service.load(request.task_id))
        self.assertIsNone(service.executions.get(request.task_id))
        self.assertEqual(service.health()['receipt_count'], 0)
        self.assertEqual(service.locks.records(), ())
        self.assertEqual(self.telemetry.audit_context['power_outcome'], 'request_failed_reconcile_task')
        class MissingGovernance:
            def get_execution_task(self, task_id):
                raise GovernanceError(ErrorCode.EXECUTION_TASK_NOT_FOUND)
        with patch.object(POWER_OPERATIONS, 'service', service), \
             patch.object(GOVERNANCE, 'require', return_value=MissingGovernance()):
            read = json.loads(await get_execution_task(request.task_id))
        self.assertEqual(read['error_code'], 'execution_task_not_found')
        replay = await self.public_call(peer, service, request)
        self.assertEqual(replay['error'], 'DashboardTransportError')
        self.assertEqual(peer.events.count('initialize'), 2)
        self.assertEqual(peer.writes, 0)

    async def test_exact_validator_explains_rejection_without_required_tool_drift(self):
        release = load_reviewed_upstream_release_registry().by_version['8.5.0']
        for extras in ((), EXTRAS, (EXTRAS[0],)):
            c = catalog(extras)
            result = validate_reviewed_release_catalog(release,
                observed_server_name=c.server_name, observed_upstream_version=c.server_version,
                observed_protocol_version=c.protocol_version, tools=c.tools)
            self.assertEqual(result.valid, not extras)
            self.assertEqual(result.reviewed_accounted_count, 77)
            self.assertEqual(result.missing_tool_count, 0)
            self.assertEqual(result.additional_tool_count, len(extras))
            self.assertEqual(set(result.additional_tools), set(extras))
            self.assertTrue(all(n == 0 for _, n in result.component_mismatch_counts))
            for provider_type, refusal_type in ((PowerProvider, PowerRefusal), (FanProvider, FanRefusal)):
                provider = provider_type(None, lambda *args: 'synthetic-authority')
                if extras:
                    with self.assertRaisesRegex(refusal_type, 'provider_contract_mismatch'):
                        provider.validate_catalog(c)
                else:
                    provider.validate_catalog(c)

    async def test_real_sdk_context_erases_typed_validator_refusal(self):
        peer, service, request = self.operation(EXTRAS)
        with patch.object(upstream_read, 'streamablehttp_client', peer.connect):
            with self.assertRaises(DashboardTransportError) as caught:
                await service.control(request)
        self.assertEqual(caught.exception.category, 'internal_error')
        def causes(exc):
            if isinstance(exc, BaseExceptionGroup):
                for child in exc.exceptions:
                    yield from causes(child)
            elif isinstance(exc, upstream_read.CatalogValidationFailure):
                yield exc.cause
        original = list(causes(caught.exception.__context__))
        self.assertEqual(len(original), 1)
        self.assertIsInstance(original[0], PowerRefusal)
        self.assertEqual(str(original[0]), 'power_provider_contract_mismatch')
        # No task/lock/action existed when the category was lost.
        self.assertIsNone(service.load(request.task_id))
        self.assertEqual(peer.calls, [])

    async def test_read_subset_still_exposes_25_and_does_not_admit_extras_or_writes(self):
        from mcp.server.fastmcp import FastMCP
        from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
        from test_ha_mcp_production_readmission import _settings
        from test_upstream_8_5_compatibility import GatewayTransport
        gateway = UpstreamReadGateway()
        transport = GatewayTransport(catalog(EXTRAS).tools)
        gateway.configure(replace(_settings('synthetic-no-key'), ha_mcp_release_registry_enabled=False),
                          transport=transport)
        await gateway.initialize(FastMCP('synthetic-investigation'))
        tools = gateway._registered_tool_registry.snapshot()
        self.assertEqual(len(tools), 25)
        self.assertFalse(set(EXTRAS).intersection(tools))
        self.assertNotIn('ha_call_service', tools)
        self.assertIsInstance(gateway.power_provider_authority_token('8.5.0'), str)

    async def test_dashboard_writes_reject_extras_but_backup_and_lifecycle_are_already_held(self):
        from ha_mcp_engineering.clients.mcp import McpDashboardHandshake
        from ha_mcp_engineering.providers.upstream_dashboard import UpstreamDashboardProvider
        from ha_mcp_engineering.providers.operational_backup import (
            ReviewedOperationalBackupProvider, OperationalBackupProviderError,
        )
        from ha_mcp_engineering.providers.operational_lifecycle import (
            ReviewedOperationalLifecycleProvider, OperationalLifecycleProviderError,
        )
        for extras in ((), EXTRAS):
            c = catalog(extras)
            dashboard = UpstreamDashboardProvider()
            handshake = McpDashboardHandshake(c.protocol_version, c.server_name, c.server_version, c.tools, 0)
            dashboard._validate_handshake(handshake)  # ordinary dashboard read still admits
            backup = ReviewedOperationalBackupProvider()
            lifecycle = ReviewedOperationalLifecycleProvider()
            checks = (
                ('dashboard_write', lambda: dashboard._validate_write_handshake(handshake),
                 DashboardTransportError, 'reviewed_contract_mismatch'),
                ('backup', lambda: backup._validate_catalog(c),
                 OperationalBackupProviderError, 'upstream_version_mismatch'),
                ('reload', lambda: lifecycle._validate_catalog(c, ('ha_reload_core',), operation='reload_yaml'),
                 OperationalLifecycleProviderError, 'upstream_version_mismatch'),
                ('restart', lambda: lifecycle._validate_catalog(c, ('ha_restart',), operation='restart_home_assistant'),
                 OperationalLifecycleProviderError, 'upstream_version_mismatch'),
                ('addon_read', lambda: lifecycle._validate_catalog(c, ('ha_get_app',), operation='restart_addon'),
                 OperationalLifecycleProviderError, 'upstream_version_mismatch'),
            )
            for name, check, error_type, category in checks:
                with self.subTest(provider=name, count=len(c.tools)):
                    if extras or name != 'dashboard_write':
                        with self.assertRaises(error_type) as caught:
                            check()
                        self.assertEqual(caught.exception.category, category)
                    else:
                        check()

    async def test_fan_preparation_has_the_same_session_failure(self):
        for extras in ((), EXTRAS):
            peer, service, _ = self.operation(extras)
            provider = FanProvider(service.provider.transport, lambda *args: 'synthetic-authority')
            request = FanRequest(entity_id='fan.synthetic', action='turn_off',
                                 operation_id=f'{int(NOW.timestamp())}-{uuid.uuid4().hex}')
            with patch.object(upstream_read, 'streamablehttp_client', peer.connect):
                if extras:
                    with self.assertRaises(DashboardTransportError) as caught:
                        await provider.services(request, lambda: None)
                    self.assertEqual(caught.exception.category, 'internal_error')
                    self.assertEqual(peer.calls, [])
                else:
                    await provider.services(request, lambda: None)
                    self.assertEqual([name for name, _ in peer.calls], ['ha_list_services'])
            self.assertEqual(peer.writes, 0)

    async def test_required_tool_drift_refuses_without_dispatch(self):
        for kind in ('missing', 'changed', 'duplicate'):
            peer, service, request = self.operation()
            tools = list(deepcopy(peer.tools))
            index = next(i for i, tool in enumerate(tools) if tool['name'] == 'ha_call_service')
            if kind == 'missing':
                tools.pop(index)
            elif kind == 'changed':
                tools[index]['inputSchema'] = {'type': 'object', 'properties': {}}
            else:
                tools.append(deepcopy(tools[index]))
            peer.tools = tools
            result = await self.public_call(peer, service, request)
            self.assertFalse(result['success'])
            self.assertEqual(peer.calls, [])
            self.assertEqual(peer.writes, 0)
            self.assertIsNone(service.load(request.task_id))

    async def test_same_id_is_not_reconciliation_until_a_declaration_exists(self):
        peer, service, request = self.operation(EXTRAS)
        refused = await self.public_call(peer, service, request)
        self.assertFalse(refused['success'])
        self.assertIsNone(service.load(request.task_id))
        # Offline only: restore the stock fixture while the synthetic ID is fresh.
        peer.tools = catalog().tools
        retried = await self.public_call(peer, service, request)
        self.assertEqual(retried.get('data', {}).get('state'), 'succeeded_verified', retried)
        self.assertEqual(peer.writes, 1)
        await self.public_call(peer, service, request)
        self.assertEqual(peer.writes, 1)


if __name__ == '__main__':
    unittest.main()
