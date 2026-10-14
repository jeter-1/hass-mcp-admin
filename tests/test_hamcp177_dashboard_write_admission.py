"""HAMCP-177: dependency-scoped governed dashboard-write admission.

The 77 descriptors come from committed exact-release evidence in runtime
order; the three additional ones carry the reported live names with
deliberately synthetic contracts. No endpoint, household dashboard or
filesystem path is supplied: the real pinned MCP ClientSession runs over
in-memory streams against a synthetic peer, behind the real dashboard
provider, gateway, governance service and F3 runtime.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
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
    DashboardTransportError, McpDashboardHandshake, McpDashboardTransport,
)
from ha_mcp_engineering.errors import DashboardProviderError, ErrorCode, GovernanceError
from ha_mcp_engineering.f3_dashboard.gateway import DashboardExecutionGateway
from ha_mcp_engineering.f3_dashboard.json_codec import upstream_config_hash
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
from ha_mcp_engineering.governance.service import ChangeGovernanceService
from ha_mcp_engineering.governance.storage import ChangePlanRepository
from ha_mcp_engineering.providers import upstream_dashboard
from ha_mcp_engineering.providers.upstream_dashboard import (
    DASHBOARD_WRITE_DEPENDENCIES, UpstreamDashboardProvider,
)
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.upstream_tool_policy import (
    load_reviewed_upstream_release_registry, validate_reviewed_release_catalog,
)
from test_dashboard_update_mvp import (
    _UnusedConfigurationGateway, _unrelated_lifecycle_provider_identity,
)

GETTER, GUIDE, SETTER = (
    'ha_config_get_dashboard', 'ha_get_skill_guide', 'ha_config_set_dashboard')
EXTRAS = ('ha_config_get_yaml', 'ha_list_files', 'ha_read_file')
COMPONENTS = ('inputSchema', 'description', 'annotations', 'outputSchema', 'title')
VERSIONS = ('8.5.0', '8.6.0')
SECRET = 'synthetic-private-exception-text'
KEY = 'I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-0123abcd'
URL_PATH = 'operations'


def catalog(extra_names=(), *, version='8.5.0'):
    folder = ROOT / 'docs/evidence/upstream-read-compatibility'
    raw = json.loads((folder / f'ha-mcp-{version}.json').read_text())
    review = json.loads((folder / f'ha-mcp-{version}-contract-review.json').read_text())
    by_name = {tool['name']: tool for tool in raw['tools']}
    tools = [deepcopy(by_name[name]) for name in review['runtime_catalog']['runtime_tool_order']]
    tools.extend({'name': name, 'description': 'Synthetic unreviewed tool; never called.',
                  'inputSchema': {'type': 'object', 'properties': {}}}
                 for name in extra_names)
    return tools


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


def handshake(tools, *, version='8.5.0', protocol='2025-03-26'):
    return McpDashboardHandshake(protocol, 'ha-mcp', version, tuple(tools), 1.0)


def text_result(value, *, is_error=False):
    return {'content': [{'type': 'text', 'text': json.dumps(value)}], 'isError': is_error}


class DashboardPeer:
    """Synthetic ha-mcp peer; actual ClientSession framing and task-group teardown."""

    def __init__(self, tools, *, version='8.5.0', protocol='2025-03-26'):
        self.tools, self.version, self.protocol = tools, version, protocol
        self.events, self.calls = [], []
        self.configuration = {'title': 'Before', 'views': []}
        self.hooks = {}  # tool name -> async callable(count) run before replying
        self.hang = set()
        self.drop = set()  # apply the call, then close without a response
        self.tools_after_guide = None
        self.reject_write = False

    def names(self):
        return [name for name, _ in self.calls]

    async def serve(self, incoming, outgoing):
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
                    result = {'tools': self.tools}
                elif request.method == 'tools/call':
                    name, args = request.params['name'], request.params.get('arguments') or {}
                    self.calls.append((name, deepcopy(args)))
                    count = self.names().count(name)
                    if name in self.hooks:
                        await self.hooks[name](count)
                    if name in self.hang:
                        await anyio.sleep_forever()
                    if name == GETTER and args.get('list_only') is True:
                        result = text_result({'success': True, 'action': 'list', 'count': 1,
                                              'dashboards': [{'id': URL_PATH, 'url_path': URL_PATH,
                                                              'mode': 'storage', 'title': 'Operations'}]})
                    elif name == GETTER:
                        result = text_result({'success': True, 'action': 'get', 'url_path': args['url_path'],
                                              'config': deepcopy(self.configuration),
                                              'config_hash': upstream_config_hash(self.configuration)})
                    elif name == GUIDE:
                        guide = '# Dashboard guide\n' + KEY
                        # The reviewed guide declares an object output schema;
                        # the pinned SDK enforces it on the client side.
                        result = {'content': [{'type': 'text', 'text': guide}],
                                  'structuredContent': {'success': True, 'content': guide},
                                  'isError': False}
                        if self.tools_after_guide is not None:
                            self.tools = self.tools_after_guide
                    elif name == SETTER:
                        if self.reject_write:
                            result = text_result({'success': False, 'error': 'synthetic rejection'},
                                                 is_error=True)
                        else:
                            self.configuration = deepcopy(args['config'])
                            if name in self.drop:
                                return
                            result = text_result({'success': True, 'action': 'update',
                                                  'url_path': args['url_path'], 'dashboard_created': False,
                                                  'config_updated': True, 'metadata_updated': False})
                    else:
                        raise AssertionError('Unexpected synthetic dispatch')
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


class Harness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.telemetry, token = begin_request('synthetic-HAMCP-177')
        self.addCleanup(end_request, token)
        self.core_ok = True

    def authorizer(self):
        return self.core_ok

    def provider(self, peer):
        provider = UpstreamDashboardProvider()
        provider._transport = McpDashboardTransport('', timeout_seconds=3,
                                                    client_version='synthetic')
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

    async def planned(self, peer, name='plans'):
        provider, service, runtime = await self.stack(peer, name)
        created = await service.create_dashboard_update_plan(
            title='Rename synthetic dashboard', description='Bounded existing-dashboard update.',
            url_path=URL_PATH,
            patch_operations=[{'operation_id': 'rename', 'operation': 'replace',
                               'path': '/title', 'value': 'After'}],
            expiration_minutes=30)
        pending = service.approve(created['plan_id'], created['plan_hash'])
        _review, csrf = await service.issue_external_csrf(created['plan_id'], pending['challenge_id'])
        await service.decide_external_approval(
            plan_id=created['plan_id'], challenge_id=pending['challenge_id'],
            expected_plan_hash=created['plan_hash'], approval_kind=pending['approval_kind'],
            approval_action=pending['approval_action'], csrf_nonce=csrf, decision='approve',
            approver_principal='home_assistant_admin_ingress:synthetic-reviewer')
        return provider, service, runtime, created

    def child(self, runtime, task_id):
        declarations = runtime.children.declarations_for_task(task_id)
        self.assertEqual(len(declarations), 1)
        return runtime.children.get(declarations[0]['child_id']).to_dict()

    def diagnostics(self, child):
        return {code for event in child['events'] for code in event['diagnostic_codes']}

    def balanced(self):
        self.assertEqual(self.telemetry.upstream_active_requests, 0)


class DependencyScopedAdmission(Harness):
    def test_dependency_set_is_exactly_the_three_tools_the_write_path_names(self):
        self.assertEqual(set(DASHBOARD_WRITE_DEPENDENCIES), {GETTER, GUIDE, SETTER})
        self.assertEqual(len(DASHBOARD_WRITE_DEPENDENCIES), 3)
        source = (ROOT / 'hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py').read_text()
        # Each transport operation names one fixed constant; nothing else is called.
        self.assertEqual(source.count('session.call_tool('), 1)

    async def test_governed_write_succeeds_once_with_stock_and_80_tool_catalogs(self):
        for version in VERSIONS:
            for extras in ((), EXTRAS):
                with self.subTest(version=version, tools=77 + len(extras)):
                    tools = catalog(extras, version=version)
                    self.assertEqual(len(tools), 77 + len(extras))
                    peer = DashboardPeer(tools, version=version)
                    _p, service, runtime, created = await self.planned(
                        peer, f'ok-{version}-{len(extras)}')
                    result = await service.apply(created['plan_id'], created['plan_hash'])
                    self.assertEqual(result['task_state'], 'succeeded_verified', result)
                    self.assertEqual(peer.names().count(SETTER), 1)
                    self.assertEqual(peer.configuration['title'], 'After')
                    self.assertLessEqual(set(peer.names()), set(DASHBOARD_WRITE_DEPENDENCIES))
                    self.assertFalse(set(EXTRAS).intersection(peer.names()))
                    child = self.child(runtime, result['task_id'])
                    self.assertEqual(child['normalized_outcome'], 'succeeded_verified')
                    try:
                        again = await service.apply(created['plan_id'], created['plan_hash'])
                    except GovernanceError:
                        pass
                    else:
                        self.assertEqual(again['task_id'], result['task_id'])
                    self.assertEqual(peer.names().count(SETTER), 1)
                    self.balanced()

    def test_authority_is_identical_for_stock_and_80_tool_catalogs(self):
        provider = UpstreamDashboardProvider()
        for version in VERSIONS:
            hashes = {provider._dashboard_provider_authority(
                handshake(catalog(extras, version=version), version=version))['evidence_hash']
                for extras in ((), EXTRAS, EXTRAS[:1])}
            self.assertEqual(len(hashes), 1, version)

    def test_extras_are_never_admitted_as_dashboard_tools(self):
        for name in EXTRAS:
            with self.subTest(tool=name), self.assertRaises(GovernanceError):
                upstream_dashboard.ensure_dashboard_tool_allowed(name)

    def test_whole_catalog_validator_is_unchanged_and_no_longer_gates_dashboard_writes(self):
        release = load_reviewed_upstream_release_registry().by_version['8.5.0']
        provider = UpstreamDashboardProvider()
        for extras in ((), EXTRAS, (EXTRAS[0],)):
            tools = catalog(extras)
            result = validate_reviewed_release_catalog(
                release, observed_server_name='ha-mcp', observed_upstream_version='8.5.0',
                observed_protocol_version='2025-03-26', tools=tools)
            self.assertEqual(result.valid, not extras)
            authority = provider._dashboard_provider_authority(handshake(tools))
            provider._validate_write_handshake(
                handshake(tools), expected_provider_authority_evidence_hash=authority['evidence_hash'])


class AdmissionRefusals(Harness):
    def assert_write_refuses(self, tools, category='reviewed_contract_mismatch', *,
                             version='8.5.0', protocol='2025-03-26', expected=None):
        provider = UpstreamDashboardProvider()
        if expected is None:
            expected = provider._dashboard_provider_authority(
                handshake(catalog(version='8.5.0')))['evidence_hash']
        with self.assertRaises(DashboardTransportError) as caught:
            provider._validate_write_handshake(
                handshake(tools, version=version, protocol=protocol),
                expected_provider_authority_evidence_hash=expected)
        self.assertEqual(caught.exception.category, category)
        self.assertIsNone(provider._optional_dashboard_provider_authority(
            handshake(tools, version=version, protocol=protocol)))

    def test_each_required_component_drift_refuses_with_or_without_extras(self):
        for extras in ((), EXTRAS):
            for name in DASHBOARD_WRITE_DEPENDENCIES:
                for component in COMPONENTS:
                    with self.subTest(extras=len(extras), tool=name, component=component):
                        tools = drifted(catalog(extras), name, component)
                        provider = UpstreamDashboardProvider()
                        with self.assertRaises(DashboardTransportError) as caught:
                            provider._dashboard_provider_authority(handshake(tools))
                        self.assertIn(caught.exception.category, {
                            'reviewed_contract_mismatch', 'reviewed_annotation_mismatch'})

    def test_unrequired_reviewed_tool_drift_grants_nothing_and_takes_nothing(self):
        base = catalog(EXTRAS)
        other = next(t['name'] for t in base if t['name'] not in DASHBOARD_WRITE_DEPENDENCIES
                     and t['name'] not in EXTRAS)
        tools = drifted(base, other, 'inputSchema')
        provider = UpstreamDashboardProvider()
        reference = provider._dashboard_provider_authority(handshake(catalog()))
        self.assertEqual(provider._dashboard_provider_authority(handshake(tools)), reference)

    def test_missing_or_duplicated_required_tool_refuses(self):
        for name in DASHBOARD_WRITE_DEPENDENCIES:
            tools = catalog(EXTRAS)
            index = next(i for i, tool in enumerate(tools) if tool['name'] == name)
            for shape, changed in (('missing', tools[:index] + tools[index + 1:]),
                                   ('duplicate', tools + [deepcopy(tools[index])])):
                with self.subTest(tool=name, shape=shape):
                    provider = UpstreamDashboardProvider()
                    with self.assertRaises(DashboardTransportError) as caught:
                        provider._dashboard_provider_authority(handshake(changed))
                    self.assertEqual(caught.exception.category, 'reviewed_contract_mismatch')

    def test_version_protocol_hold_and_revocation_refuse(self):
        registry = load_reviewed_upstream_release_registry()
        release = registry.by_version['8.5.0']
        held = replace(registry, releases=tuple(
            replace(item, provider_dispositions=tuple(
                (provider, 'held' if provider == 'dashboard' else state)
                for provider, state in item.provider_dispositions))
            if item is release else item for item in registry.releases))
        revoked = replace(registry, releases=tuple(
            replace(item, revoked=True) if item is release else item for item in registry.releases))
        provider = UpstreamDashboardProvider()
        expected = provider._dashboard_provider_authority(handshake(catalog()))['evidence_hash']
        with self.subTest(change='unreviewed version'), self.assertRaises(DashboardTransportError) as caught:
            provider._dashboard_provider_authority(handshake(catalog(EXTRAS), version='8.9.9'))
        self.assertEqual(caught.exception.category, 'upstream_version_mismatch')
        with self.subTest(change='other reviewed version'):
            with self.assertRaises(DashboardTransportError) as caught:
                provider._dashboard_provider_authority(handshake(catalog(EXTRAS), version='8.4.3'))
            self.assertEqual(caught.exception.category, 'reviewed_contract_mismatch')
        with self.subTest(change='authority from another version'):
            with self.assertRaises(DashboardTransportError) as caught:
                provider._validate_write_handshake(
                    handshake(catalog(EXTRAS, version='8.6.0'), version='8.6.0'),
                    expected_provider_authority_evidence_hash=expected)
            self.assertEqual(caught.exception.category, 'reviewed_contract_mismatch')
        with self.subTest(change='protocol'), self.assertRaises(DashboardTransportError):
            provider._validate_write_handshake(
                handshake(catalog(EXTRAS), protocol='2024-11-05'),
                expected_provider_authority_evidence_hash=expected)
        for change, value in (('hold', held), ('revocation', revoked)):
            with self.subTest(change=change):
                changed = next(item for item in value.releases if item.version == '8.5.0')
                self.assertEqual((changed.provider_disposition('dashboard'), changed.revoked),
                                 ('held', False) if change == 'hold' else ('admitted', True))
                with patch.object(upstream_dashboard, 'load_reviewed_upstream_release_registry',
                                  lambda: value):
                    with self.assertRaises(DashboardTransportError):
                        provider._validate_write_handshake(
                            handshake(catalog(EXTRAS)), expected_provider_authority_evidence_hash=expected)
                    self.assertIsNone(provider._optional_dashboard_provider_authority(
                        handshake(catalog(EXTRAS))))


class GovernedFlowRefusals(Harness):
    async def assert_pre_dispatch_refusal(self, peer, runtime, result):
        self.assertEqual(result['task_state'], 'failed_pre_dispatch', result)
        child = self.child(runtime, result['task_id'])
        self.assertEqual(child['normalized_outcome'], 'preflight_rejected')
        self.assertTrue(child['terminal'])
        self.assertNotIn(SETTER, peer.names())
        self.assertFalse(set(EXTRAS).intersection(peer.names()))
        self.assertEqual(peer.configuration['title'], 'Before')
        self.balanced()

    def assert_confirmed_no_dispatch(self, peer, runtime, result):
        self.assertEqual(result['task_state'], 'failed_post_dispatch', result)
        child = self.child(runtime, result['task_id'])
        self.assertEqual(child['normalized_outcome'], 'dispatch_failed_confirmed')
        self.assertTrue(child['terminal'])
        diagnostics = self.diagnostics(child)
        self.assertIn('provider_confirmed_no_mutation', diagnostics)
        self.assertNotIn('exact_readback_mismatch', diagnostics)
        self.assertNotIn(SETTER, peer.names())
        self.assertEqual(peer.configuration['title'], 'Before')
        self.balanced()

    async def test_required_drift_after_planning_refuses_before_dispatch(self):
        for name in DASHBOARD_WRITE_DEPENDENCIES:
            for component in COMPONENTS:
                with self.subTest(tool=name, component=component):
                    peer = DashboardPeer(catalog(EXTRAS))
                    _p, service, runtime, created = await self.planned(peer, f'{name}-{component}')
                    peer.tools = drifted(catalog(EXTRAS), name, component)
                    result = await service.apply(created['plan_id'], created['plan_hash'])
                    await self.assert_pre_dispatch_refusal(peer, runtime, result)

    async def test_missing_duplicate_version_and_protocol_changes_refuse_before_dispatch(self):
        tools = catalog(EXTRAS)
        changes = {}
        for name in DASHBOARD_WRITE_DEPENDENCIES:
            index = next(i for i, tool in enumerate(tools) if tool['name'] == name)
            changes[f'missing-{name}'] = {'tools': tools[:index] + tools[index + 1:]}
            changes[f'duplicate-{name}'] = {'tools': tools + [deepcopy(tools[index])]}
        changes['other-reviewed-version'] = {'version': '8.4.3'}
        changes['unreviewed-version'] = {'version': '8.9.9'}
        changes['protocol'] = {'protocol': '2024-11-05'}
        for label, change in changes.items():
            with self.subTest(change=label):
                peer = DashboardPeer(catalog(EXTRAS))
                _p, service, runtime, created = await self.planned(peer, label)
                for attribute, value in change.items():
                    setattr(peer, attribute, value)
                result = await service.apply(created['plan_id'], created['plan_hash'])
                await self.assert_pre_dispatch_refusal(peer, runtime, result)

    async def test_hold_or_revocation_after_planning_refuses_before_dispatch(self):
        registry = load_reviewed_upstream_release_registry()
        release = registry.by_version['8.5.0']
        held = replace(registry, releases=tuple(
            replace(item, provider_dispositions=tuple(
                (provider, 'held' if provider == 'dashboard' else state)
                for provider, state in item.provider_dispositions))
            if item is release else item for item in registry.releases))
        revoked = replace(registry, releases=tuple(
            replace(item, revoked=True) if item is release else item for item in registry.releases))
        for change, value in (('hold', held), ('revocation', revoked)):
            with self.subTest(change=change):
                peer = DashboardPeer(catalog(EXTRAS))
                _p, service, runtime, created = await self.planned(peer, change)
                with patch.object(upstream_dashboard, 'load_reviewed_upstream_release_registry',
                                  lambda: value):
                    result = await service.apply(created['plan_id'], created['plan_hash'])
                await self.assert_pre_dispatch_refusal(peer, runtime, result)

    async def test_setter_drift_between_guide_and_setter_is_confirmed_without_dispatch(self):
        for component in COMPONENTS:
            with self.subTest(component=component):
                peer = DashboardPeer(catalog(EXTRAS))
                _p, service, runtime, created = await self.planned(peer, f'late-{component}')
                peer.tools_after_guide = drifted(catalog(EXTRAS), SETTER, component)
                result = await service.apply(created['plan_id'], created['plan_hash'])
                self.assert_confirmed_no_dispatch(peer, runtime, result)
                with self.assertRaises(GovernanceError):
                    await service.apply(created['plan_id'], created['plan_hash'])
                self.assertNotIn(SETTER, peer.names())

    async def test_extras_appearing_or_vanishing_between_steps_grant_and_take_nothing(self):
        for before, after in (((), EXTRAS), (EXTRAS, ())):
            with self.subTest(before=len(before), after=len(after)):
                peer = DashboardPeer(catalog(before))
                _p, service, runtime, created = await self.planned(peer, f'flip-{len(before)}')
                peer.tools_after_guide = catalog(after)
                result = await service.apply(created['plan_id'], created['plan_hash'])
                self.assertEqual(result['task_state'], 'succeeded_verified', result)
                self.assertEqual(peer.names().count(SETTER), 1)

    async def test_core_retirement_between_discovery_and_dispatch_never_writes(self):
        # Retired while the guide read is in flight: the setter exchange is
        # admitted, then Core authority refuses immediately before tools/call.
        peer = DashboardPeer(catalog(EXTRAS))
        _p, service, runtime, created = await self.planned(peer)
        self.telemetry.core_dispatch_authorizer = self.authorizer

        async def retire(count):
            self.core_ok = False
        peer.hooks[GUIDE] = retire
        result = await service.apply(created['plan_id'], created['plan_hash'])
        self.assert_confirmed_no_dispatch(peer, runtime, result)
        # The setter session was opened and admitted, but never called.
        self.assertEqual(peer.events.count('initialize'), peer.events.count('tools/list'))
        self.assertEqual(peer.names()[-1], GUIDE)

    async def test_core_retirement_before_the_guide_refuses_before_dispatch(self):
        peer = DashboardPeer(catalog(EXTRAS))
        _p, service, runtime, created = await self.planned(peer)
        self.telemetry.core_dispatch_authorizer = self.authorizer
        reads = peer.names().count(GETTER)

        async def retire(count):
            if count == reads + 2:  # the apply preflight's configuration read
                self.core_ok = False
        peer.hooks[GETTER] = retire
        result = await service.apply(created['plan_id'], created['plan_hash'])
        await self.assert_pre_dispatch_refusal(peer, runtime, result)
        self.assertEqual(peer.names().count(GUIDE), 0)

    async def test_planning_refuses_without_admitted_write_dependencies(self):
        for name in DASHBOARD_WRITE_DEPENDENCIES:
            with self.subTest(tool=name):
                peer = DashboardPeer(drifted(catalog(EXTRAS), name, 'inputSchema'))
                _p, service, _r = await self.stack(peer, f'plan-{name}')
                with self.assertRaises(GovernanceError) as caught:
                    await service.create_dashboard_update_plan(
                        title='Rename synthetic dashboard', description='Bounded update.',
                        url_path=URL_PATH,
                        patch_operations=[{'operation_id': 'rename', 'operation': 'replace',
                                           'path': '/title', 'value': 'After'}],
                        expiration_minutes=30)
                self.assertEqual(caught.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
                self.assertFalse(caught.exception.retryable)
                self.assertEqual(caught.exception.details,
                                 {'reason': 'dashboard_provider_not_admitted'})
                self.assertNotIn(SETTER, peer.names())
                self.assertNotIn(GUIDE, peer.names())


class ExistingSemanticsWith80Tools(Harness):
    async def test_stale_dashboard_fails_before_dispatch(self):
        peer = DashboardPeer(catalog(EXTRAS))
        _p, service, runtime, created = await self.planned(peer)
        peer.configuration['title'] = 'External edit'
        result = await service.apply(created['plan_id'], created['plan_hash'])
        self.assertEqual(result['task_state'], 'failed_pre_dispatch', result)
        self.assertNotIn(SETTER, peer.names())
        self.assertEqual(peer.configuration['title'], 'External edit')

    async def test_concurrent_duplicate_apply_dispatches_at_most_once(self):
        peer = DashboardPeer(catalog(EXTRAS))
        _p, service, runtime, created = await self.planned(peer)
        results = await asyncio.gather(
            service.apply(created['plan_id'], created['plan_hash']),
            service.apply(created['plan_id'], created['plan_hash']),
            return_exceptions=True)
        self.assertEqual(peer.names().count(SETTER), 1)
        self.assertTrue(any(isinstance(item, dict) and item.get('task_state') == 'succeeded_verified'
                            for item in results), results)

    async def test_second_plan_restores_the_original_configuration_exactly(self):
        peer = DashboardPeer(catalog(EXTRAS))
        original = deepcopy(peer.configuration)
        _p, service, runtime, created = await self.planned(peer)
        self.assertEqual((await service.apply(created['plan_id'], created['plan_hash']))['task_state'],
                         'succeeded_verified')
        restored = await service.create_dashboard_update_plan(
            title='Restore synthetic dashboard', description='Bounded existing-dashboard update.',
            url_path=URL_PATH,
            patch_operations=[{'operation_id': 'restore', 'operation': 'replace',
                               'path': '/title', 'value': 'Before'}],
            expiration_minutes=30)
        pending = service.approve(restored['plan_id'], restored['plan_hash'])
        _review, csrf = await service.issue_external_csrf(restored['plan_id'], pending['challenge_id'])
        await service.decide_external_approval(
            plan_id=restored['plan_id'], challenge_id=pending['challenge_id'],
            expected_plan_hash=restored['plan_hash'], approval_kind=pending['approval_kind'],
            approval_action=pending['approval_action'], csrf_nonce=csrf, decision='approve',
            approver_principal='home_assistant_admin_ingress:synthetic-reviewer')
        result = await service.apply(restored['plan_id'], restored['plan_hash'])
        self.assertEqual(result['task_state'], 'succeeded_verified', result)
        self.assertEqual(peer.names().count(SETTER), 2)
        self.assertEqual(peer.configuration, original)

    async def test_lost_setter_response_is_read_back_without_redispatch(self):
        peer = DashboardPeer(catalog(EXTRAS))
        _p, service, runtime, created = await self.planned(peer)
        peer.drop.add(SETTER)
        result = await service.apply(created['plan_id'], created['plan_hash'])
        self.assertEqual(result['task_state'], 'succeeded_verified', result)
        self.assertEqual(peer.names().count(SETTER), 1)
        try:
            await service.apply(created['plan_id'], created['plan_hash'])
        except GovernanceError:
            pass
        self.assertEqual(peer.names().count(SETTER), 1)
        self.balanced()

    async def test_structured_rejection_with_unchanged_readback_is_not_a_mismatch(self):
        peer = DashboardPeer(catalog(EXTRAS))
        _p, service, runtime, created = await self.planned(peer)
        peer.reject_write = True
        result = await service.apply(created['plan_id'], created['plan_hash'])
        self.assertEqual(result['task_state'], 'failed_post_dispatch', result)
        self.assertEqual(peer.names().count(SETTER), 1)
        self.assertEqual(peer.configuration['title'], 'Before')

    async def test_each_exchange_counts_once_and_every_attempt_finishes(self):
        peer = DashboardPeer(catalog(EXTRAS))
        _p, service, runtime, created = await self.planned(peer)
        before_sessions = peer.events.count('initialize')
        before_count = self.telemetry.upstream_request_count
        result = await service.apply(created['plan_id'], created['plan_hash'])
        self.assertEqual(result['task_state'], 'succeeded_verified')
        self.assertEqual(self.telemetry.upstream_request_count - before_count,
                         peer.events.count('initialize') - before_sessions)
        self.balanced()


class TruthfulRefusal(Harness):
    async def authority(self, provider):
        return provider._dashboard_provider_authority(handshake(catalog()))['evidence_hash']

    async def write(self, provider):
        configuration = {'title': 'After', 'views': []}
        return await provider.execute_governed_dashboard_update(
            url_path=URL_PATH, configuration=configuration,
            config_hash=upstream_config_hash(configuration), best_practice_key=KEY,
            expected_provider_authority_evidence_hash=await self.authority(provider))

    def spy(self):
        seen = []
        original = mcp_client._classified_transport_error

        def classify(exc):
            seen.append(exc)
            return original(exc)
        self.enterContext(patch.object(mcp_client, '_classified_transport_error', classify))
        return seen

    def assert_refusal(self, caught, code, *, dispatched):
        error = caught.exception
        self.assertEqual(error.code, code)
        self.assertEqual(error.details['upstream_dispatch_occurred'], dispatched)
        self.assertNotIn(SECRET, json.dumps(error.details))
        self.assertNotEqual(error.code, ErrorCode.UPSTREAM_DASHBOARD_INTERNAL_ERROR)

    async def test_grouped_admission_refusal_keeps_its_bounded_non_retryable_code(self):
        for name in DASHBOARD_WRITE_DEPENDENCIES:
            with self.subTest(tool=name):
                peer = DashboardPeer(drifted(catalog(EXTRAS), name, 'inputSchema'))
                provider = self.provider(peer)
                seen = self.spy()
                for operation in ('write', 'guide'):
                    with self.assertRaises(DashboardProviderError) as caught:
                        if operation == 'write':
                            await self.write(provider)
                        else:
                            await provider.best_practices_acknowledgement_key(
                                expected_provider_authority_evidence_hash=await self.authority(provider))
                    self.assert_refusal(caught, ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH,
                                        dispatched=operation == 'guide')
                    self.assertFalse(caught.exception.retryable)
                self.assertTrue(seen and all(isinstance(item, BaseExceptionGroup) for item in seen))
                self.assertEqual(peer.calls, [])
                self.balanced()

    async def test_direct_admission_refusal_maps_the_same_way(self):
        tools = drifted(catalog(EXTRAS), SETTER, 'inputSchema')

        class Direct:
            async def execute_dashboard_write(self, arguments, validator):
                validator(handshake(tools))
                raise AssertionError('setter must not be reached')
        provider = UpstreamDashboardProvider()
        provider._transport = Direct()
        with self.assertRaises(DashboardProviderError) as caught:
            await self.write(provider)
        self.assert_refusal(caught, ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH,
                            dispatched=False)
        self.assertFalse(caught.exception.retryable)
        self.balanced()

    async def test_core_refusal_before_the_setter_call_is_not_a_dispatch(self):
        peer = DashboardPeer(catalog(EXTRAS))
        provider = self.provider(peer)
        seen = self.spy()
        self.telemetry.core_dispatch_authorizer = lambda: False
        with self.assertRaises(DashboardProviderError) as caught:
            await self.write(provider)
        self.assert_refusal(caught, ErrorCode.UPSTREAM_DASHBOARD_CONNECTION_FAILED, dispatched=False)
        self.assertTrue(seen and isinstance(seen[0], BaseExceptionGroup))
        self.assertEqual(peer.calls, [])
        self.assertEqual(peer.events.count('tools/list'), 1)
        self.balanced()

    def test_not_attempted_marker_survives_grouping_and_never_leaks_from_other_errors(self):
        classify = mcp_client._classified_transport_error
        refused = DashboardTransportError('connection_failed', tool_call_attempted=False)
        cases = (
            (BaseExceptionGroup('g', [refused]), False),
            (BaseExceptionGroup('g', [BaseExceptionGroup('h', [refused]), OSError(SECRET)]), False),
            (BaseExceptionGroup('g', [DashboardTransportError('connection_failed')]), True),
            (BaseExceptionGroup('g', [OSError(SECRET)]), True),
            (BaseExceptionGroup('g', [DashboardTransportError('timeout'), OSError(SECRET)]), True),
            (OSError(SECRET), True),
        )
        for exc, attempted in cases:
            with self.subTest(exc=repr(exc)[:40]):
                classified = classify(exc)
                self.assertEqual(classified.tool_call_attempted, attempted)
                self.assertNotIn(SECRET, json.dumps(classified.evidence_details()))
        self.assertTrue(DashboardTransportError('connection_failed').tool_call_attempted)

    async def test_real_transport_loss_after_the_call_stays_dispatched(self):
        peer = DashboardPeer(catalog(EXTRAS))
        peer.drop.add(SETTER)
        provider = self.provider(peer)
        with self.assertRaises(DashboardProviderError) as caught:
            await self.write(provider)
        self.assertEqual(caught.exception.details['upstream_dispatch_occurred'], True)
        self.assertNotEqual(caught.exception.code, ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH)
        self.assertEqual(peer.names(), [SETTER])
        self.balanced()

    async def test_timeout_after_the_call_stays_dispatched(self):
        peer = DashboardPeer(catalog(EXTRAS))
        peer.hang.add(SETTER)
        provider = self.provider(peer)
        with self.assertRaises(DashboardProviderError) as caught:
            await self.write(provider)
        self.assertEqual(caught.exception.code, ErrorCode.UPSTREAM_DASHBOARD_TIMEOUT)
        self.assertEqual(caught.exception.details['upstream_dispatch_occurred'], True)
        self.balanced()

    async def test_cancellation_propagates_unchanged(self):
        for stage in (GUIDE, SETTER):
            with self.subTest(stage=stage):
                peer = DashboardPeer(catalog(EXTRAS))
                peer.hang.add(stage)
                provider = self.provider(peer)

                async def run():
                    if stage == SETTER:
                        return await self.write(provider)
                    return await provider.best_practices_acknowledgement_key(
                        expected_provider_authority_evidence_hash=await self.authority(provider))
                task = asyncio.ensure_future(run())
                while stage not in peer.names():
                    await asyncio.sleep(0.01)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.balanced()

    async def test_cancelled_governed_setter_keeps_its_saved_intent_for_recovery(self):
        peer = DashboardPeer(catalog(EXTRAS))
        _p, service, runtime, created = await self.planned(peer)
        peer.hang.add(SETTER)
        task = asyncio.ensure_future(service.apply(created['plan_id'], created['plan_hash']))
        while SETTER not in peer.names():
            await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        stored = service.task_repository.get_for_plan(created['plan_id'])
        self.assertIsNotNone(stored)
        task_view = service.get_execution_task(stored.task_id)
        # Cancellation after the dispatch intent never claims a clean outcome.
        self.assertIsNone(task_view['terminal_outcome'], task_view)
        child = self.child(runtime, stored.task_id)
        self.assertEqual(child['state'], 'dispatching')
        self.assertIn('possibly_dispatched', self.diagnostics(child))
        self.assertNotIn('dispatch_failed_confirmed', self.diagnostics(child))
        self.assertEqual(peer.names().count(SETTER), 1)
        self.balanced()


if __name__ == '__main__':
    unittest.main()
