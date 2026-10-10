"""Exact 8.6 candidate: real Engineering boundaries, synthetic transports only."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hass_mcp_engineering_beta'), str(ROOT / 'tests')]
from mcp.server.fastmcp import FastMCP
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.upstream_tool_policy import (
    load_reviewed_upstream_release_registry, validate_reviewed_release_evidence,
    validate_reviewed_release_catalog, schema_fingerprint,
)
from test_ha_mcp_production_readmission import _GatewayTransport, _settings
from test_beta58_ha_mcp_8_4_3_continuity import EXACT_READS
import test_upstream_8_5_compatibility as previous
import test_typed_power as power

def capture():
    return previous.capture('8.6.0')

RESULTS = json.loads((ROOT / 'tests/fixtures/ha_mcp_8_6_0/read-results.json').read_text())

class Transport(_GatewayTransport):
    def __init__(self, tools, version='8.6.0'):
        super().__init__(tools, version=version)
        self.sent = []
        self.records = {r['upstream_name']:r for r in RESULTS['standalone']}
        self.override = None
        self.cancel = False

    async def execute_read(self, tool, arguments, **kwargs):
        exchange = await super().execute_read(tool, arguments, **kwargs)
        self.sent.append((tool, deepcopy(arguments)))
        if self.cancel:
            raise asyncio.CancelledError()
        raw = self.override if self.override is not None else self.records[tool]['result']
        return replace(exchange, call_result=deepcopy(raw))

class ReadCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def gateway(self, tools=None, version='8.6.0', protocol='2025-03-26'):
        transport = Transport(capture()['tools'] if tools is None else tools, version)
        transport.catalog = replace(transport.catalog, protocol_version=protocol)
        gateway = UpstreamReadGateway()
        gateway.configure(replace(_settings('synthetic-no-key'), ha_mcp_release_registry_enabled=False), transport=transport)
        await gateway.initialize(FastMCP('synthetic-860'))
        return gateway, transport

    async def call(self, gateway, name, args):
        telemetry, token = begin_request('synthetic-860')
        try:
            result = json.loads(await gateway._registered_tool_registry.snapshot()[name].run(args))
            return result, telemetry
        finally:
            end_request(token)

    async def test_public_descriptors_preserved_except_approved_skill_migration(self):
        g, t = await self.gateway()
        old, _ = await self.gateway(previous.capture()['tools'], '8.5.0')
        actual = g._registered_tool_registry.snapshot()
        self.assertEqual(set(actual), EXACT_READS)
        for name, tool in actual.items():
            with self.subTest(name=name):
                if name == 'ha_get_skill_guide':
                    self.assertEqual(set(tool.parameters['properties']), {'file'})
                    self.assertEqual(tool.parameters['properties']['file']['default'], 'SKILL.md')
                    self.assertIn('document', tool.description)
                    self.assertIn('skill', old._registered_tool_registry.snapshot()[name].parameters['properties'])
                else:
                    before = old._registered_tool_registry.snapshot()[name]
                    self.assertEqual(tool.parameters, before.parameters)
                    self.assertEqual(tool.description, before.description)
                    self.assertEqual(tool.annotations, before.annotations)
        self.assertEqual(t.sent, [])

    async def test_retained_actual_read_results_both_variants(self):
        for variant, records in RESULTS.items():
            g, t = await self.gateway()
            for record in records:
                with self.subTest(variant=variant, tool=record['public_name']):
                    args = dict(record['arguments'])
                    if record['public_name'] == 'ha_get_blueprint': args.pop('action')
                    t.override = record['result']
                    result, _ = await self.call(g, record['public_name'], args)
                    self.assertTrue(result['success'], result)
                    self.assertFalse(result['metadata']['fallback_occurred'])
            self.assertEqual(len(t.sent), 25)

    async def test_scene_lookup_cannot_become_search(self):
        g, t = await self.gateway()
        for args in [{}, {'scene_id':None}, {'scene_id':''}, {'scene_id':'x', 'query':'x'}, {'scene_id':'x','limit':2}, {'scene_id':'x','offset':1}, {'scene_id':'x','search_in_config':True}]:
            result, _ = await self.call(g, 'ha_config_get_scene', args)
            self.assertFalse(result['success'], result)
            self.assertFalse(result['metadata']['upstream_dispatch_occurred'])
        self.assertEqual(t.sent, [])
        await self.call(g, 'ha_config_get_scene', {'scene_id':'gateway_scene'})
        self.assertEqual(t.sent, [('ha_config_get_scene', {'scene_id':'gateway_scene'})])

    async def test_template_new_operations_and_invalid_timeout_have_zero_dispatch(self):
        g, t = await self.gateway()
        for args in [{'condition':{}}, {'template':'x','condition':None}, {'template':'x','strict':False}, {'template':'x','variables':{}}, *({'template':'x','timeout':x} for x in [0,-1,61,True,1.5])]:
            result, _ = await self.call(g, 'ha_eval_template', args)
            self.assertFalse(result['success'], result)
            self.assertFalse(result['metadata']['upstream_dispatch_occurred'])
        self.assertEqual(t.sent, [])
        for timeout in (1,3,60):
            result, _ = await self.call(g, 'ha_eval_template', {'template':'{{ 1 + 1 }}','timeout':timeout})
            self.assertTrue(result['success'], result)
            self.assertEqual(t.sent[-1], ('ha_eval_template', {'template':'{{ 1 + 1 }}','timeout':timeout,'report_errors':True,'condition':None,'variables':None,'strict':False}))

    async def test_skill_old_selector_and_traversal_are_zero_io(self):
        g, t = await self.gateway()
        for args in [{'skill':'automation'}, *({'file':p} for p in ['../x.md','/SKILL.md','references/../x.md','https://example.invalid/x.md','x%2fy.md','a\\b.md','', 'x'*513+'.md'])]:
            result, _ = await self.call(g, 'ha_get_skill_guide', args)
            self.assertFalse(result['success'], result)
            self.assertFalse(result['metadata']['upstream_dispatch_occurred'])
        self.assertEqual(t.sent, [])
        result, _ = await self.call(g, 'ha_get_skill_guide', {})
        self.assertTrue(result['success'], result)
        self.assertEqual(t.sent, [('ha_get_skill_guide', {'file':'SKILL.md'})])
        await self.call(g, 'ha_get_skill_guide', {'file':'references/examples.yaml'})
        self.assertEqual(t.sent[-1], ('ha_get_skill_guide', {'file':'references/examples.yaml'}))

    async def test_blueprint_constructs_only_list_get(self):
        g, t = await self.gateway()
        for field in ('action','url','yaml','config','confirm','overwrite','input'):
            result, _ = await self.call(g, 'ha_get_blueprint', {field:'synthetic'})
            self.assertFalse(result['success'], result)
        for path in ('../x.yaml','https://example.invalid/x.yaml','x%2Fy.yaml'):
            result, _ = await self.call(g, 'ha_get_blueprint', {'path':path})
            self.assertFalse(result['success'], result)
        self.assertEqual(t.sent, [])
        result, _ = await self.call(g, 'ha_get_blueprint', {})
        self.assertTrue(result['success'], result)
        self.assertEqual(t.sent, [('ha_manage_blueprints', {'action':'list','domain':'automation'})])
        t.override = {'isError':False,'structuredContent':{'success':True,'data':{'domain':'automation','path':'synthetic/read.yaml','metadata':{}}}}
        result, _ = await self.call(g, 'ha_get_blueprint', {'path':'synthetic/read.yaml'})
        self.assertEqual(result['metadata']['completeness'],'partial')
        self.assertEqual(t.sent[-1][1],{'action':'get','domain':'automation','path':'synthetic/read.yaml'})
        result, _ = await self.call(g, 'ha_get_blueprint', {'path':'different.yaml'})
        self.assertFalse(result['success'])

    async def test_every_descriptor_component_drift_withholds_target_keeps_sibling(self):
        mutations = {
            'description': lambda t:t.update(description=t['description']+' drift'),
            'title': lambda t:t.update(title='drift'),
            'schema': lambda t:t['inputSchema'].update(description='drift'),
            'annotations': lambda t:t['annotations'].update(title='drift'),
            'output': lambda t:t.update(outputSchema={'type':'string'}),
            'metadata': lambda t:t['_meta'].update(ha_mcp={'policy':{'enabled':True}}),
        }
        for key, mutate in mutations.items():
            with self.subTest(component=key):
                tools = capture()['tools']; mutate(next(t for t in tools if t['name']=='ha_eval_template'))
                g, t = await self.gateway(tools)
                self.assertNotIn('ha_eval_template',g._registered_tool_registry.snapshot())
                self.assertIn('ha_get_state',g._registered_tool_registry.snapshot())
                self.assertEqual(t.sent, [])

    async def test_unknown_identity_and_protocol_refuse(self):
        for version, protocol in [('8.6.1','2025-03-26'),('8.6.0','2099-01-01')]:
            g, t = await self.gateway(version=version,protocol=protocol)
            self.assertEqual(g._registered_tool_registry.snapshot(), {})
            self.assertEqual(t.sent, [])

    async def test_live_missing_duplicate_or_incomplete_catalog_never_dispatches(self):
        for kind in ('missing','duplicate','incomplete'):
            g, t = await self.gateway(); tools=list(t.catalog.tools)
            if kind=='missing':tools=[x for x in tools if x['name']!='ha_get_state']
            if kind=='duplicate':tools.append(next(x for x in tools if x['name']=='ha_get_state'))
            t.catalog=replace(t.catalog,tools=tuple(tools),catalog_complete=kind!='incomplete')
            result,_=await self.call(g,'ha_get_state',{'entity_id':'light.fixture'})
            self.assertFalse(result['success'],result);self.assertEqual(t.sent,[])

    async def test_errors_large_and_malformed_results_are_bounded_no_retry(self):
        g,t=await self.gateway()
        for raw in [{'isError':True,'content':[{'type':'text','text':json.dumps({'success':False,'error':{'code':'RESOURCE_NOT_FOUND','message':'synthetic'}})}]}, {'isError':False,'content':[]}, {'isError':False,'structuredContent':{'success':True,'huge':'x'*100000}}]:
            t.override=raw
            result,_=await self.call(g,'ha_get_skill_guide',{'file':'missing.md'})
            self.assertLessEqual(len(json.dumps(result).encode()),60000)
            self.assertFalse(result['metadata']['fallback_occurred'])
        self.assertEqual(len(t.sent),3)

    async def test_cancellation_settles_route_and_next_read_still_works(self):
        g,t=await self.gateway();t.cancel=True
        with self.assertRaises(asyncio.CancelledError):
            await self.call(g,'ha_get_state',{'entity_id':'light.fixture'})
        t.cancel=False
        result,_=await self.call(g,'ha_get_state',{'entity_id':'light.fixture'})
        self.assertTrue(result['success'],result);self.assertEqual(len(t.sent),2)

class ExactBindingsTests(unittest.TestCase):
    def test_all_evidence_and_exact_catalog(self):
        registry=validate_reviewed_release_evidence(repository_root=ROOT)
        release=registry.by_version['8.6.0']
        result=validate_reviewed_release_catalog(release,observed_server_name='ha-mcp',observed_upstream_version='8.6.0',observed_protocol_version='2025-03-26',tools=capture()['tools'])
        self.assertTrue(result.valid,result)
        self.assertEqual(dict(release.provider_dispositions)['backup'],'held')
        self.assertEqual(dict(release.provider_dispositions)['lifecycle'],'held')
        self.assertFalse(release.policy.by_name['ha_get_operation_status'].is_read_route)

    def test_core_strict_profile_accepts_only_compiled_device_binding(self):
        from ha_mcp_engineering.ha_core_readmission import routes
        for name in routes.DEVICE_DEPENDENT_DELEGATED_TOOLS:
            for version, expected in [('8.6.0',True),('8.6.1',False),('8.5.0',True)]:
                actual=routes.delegated_provider_compatibility(tool_name=name,core_version='2026.9.4',adapter_version=version,probe_profile=routes.CHILD_DEVICE_PROBE_PROFILE)
                self.assertEqual(actual[0],expected,(name,version,actual))

    def test_dashboard_absent_policy_is_exact_descriptor_only(self):
        from ha_mcp_engineering.providers.upstream_contracts import normalize_runtime_contract,CONTRACT_FAMILY_V3,ContractValidationError
        tool=next(t for t in capture()['tools'] if t['name']=='ha_config_get_dashboard')
        value=normalize_runtime_contract(tool,protocol_version='2025-03-26',contract_family=CONTRACT_FAMILY_V3)
        self.assertEqual(value.security_contract['runtime_policy_state'],{'present':False})
        for original in [tool,next(t for t in previous.capture()['tools'] if t['name']=='ha_config_get_dashboard')]:
            changed=deepcopy(original);changed['_meta'].pop('ha_mcp',None);changed['description']+=' drift'
            with self.assertRaises(ContractValidationError):normalize_runtime_contract(changed,protocol_version='2025-03-26',contract_family=CONTRACT_FAMILY_V3)

    def test_f3_setter_fingerprints_and_atomicity_stay_exact(self):
        from ha_mcp_engineering.f3_dashboard.provider import EXACT_CONTRACTS
        from ha_mcp_engineering.f3_dashboard.atomicity import assess_atomicity
        from ha_mcp_engineering.f3_dashboard.raw_evidence import EXPECTED_RELEASES
        release=load_reviewed_upstream_release_registry().by_version['8.6.0']
        expected=dict(release.tool_contracts)['ha_config_set_dashboard'];actual=EXACT_CONTRACTS['8.6.0']
        for field in ['input_schema_fingerprint','annotation_fingerprint','description_fingerprint','output_contract_fingerprint','runtime_contract_fingerprint']:
            self.assertEqual(getattr(actual,field),getattr(expected,field))
        self.assertEqual(EXPECTED_RELEASES['8.6.0'][0],release.entry_id)
        decision=assess_atomicity('8.6.0');self.assertEqual(decision.home_assistant_release,'2026.9.4')
        self.assertIn('post_write_readback_cannot_detect_an_overwritten_external_edit',decision.reason_codes)

class Fan860Tests(previous.Fan850Tests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.transport.catalog=replace(self.transport.catalog,server_version='8.6.0',tools=tuple(capture()['tools']))

    async def test_new_receipts_bind_850_and_reload_without_migration(self):
        req=self.request(percentage=66);result=await self.call(req)
        self.assertEqual(result['provider_contract'],previous.FAN_RELEASES['8.6.0'][2])
        self.assertEqual(self.service.load(req.task_id).provider_contract,result['provider_contract'])
        self.assertEqual(self.transport.writes,1);self.settled()

class Power860Tests(power.PowerTests):
    version='8.6.0'

class Dashboard860RuntimeTests(previous.Dashboard850RuntimeTests):
    async def asyncSetUp(self):
        await super().asyncSetUp();self.dashboard.version='8.6.0'
        original=previous.dashboard.make_preread

        def exact_preread(configuration=None, *, version='8.6.0', **kwargs):
            if version!='8.6.0':return original(configuration,version=version,**kwargs)
            from ha_mcp_engineering.f3_dashboard.identity import build_provider_authority,build_operational_identity,reviewed_tool_contract_hash
            from ha_mcp_engineering.f3_dashboard.json_codec import engineering_sha256
            from ha_mcp_engineering.f3_dashboard.provider import EXACT_CONTRACTS
            # Reuse only synthetic content/flags; construct all provider authority
            # and operational identity from the selected 8.6 release itself.
            template=original(configuration,version='8.5.0',**kwargs)
            release=load_reviewed_upstream_release_registry().by_version[version]
            authority=build_provider_authority(
                provider_slug='ha_mcp',server_name='ha-mcp',upstream_version=version,
                protocol_version='2025-03-26',compatibility_entry=release.entry_id,
                source_commit=release.source_commit,image_index_digest=release.image_index_digest,
                contract_family='ha_mcp_dashboard_read_v3',
                dashboard_attestation_fingerprint=release.dashboard_attestation_fingerprint,
                compiled_constraints_fingerprint=release.dashboard_compiled_constraints_fingerprint,
                getter_contract_hash=reviewed_tool_contract_hash(release.tool_contracts_by_name['ha_config_get_dashboard']),
                setter_contract_hash=engineering_sha256(asdict(EXACT_CONTRACTS[version])),
                catalog_fingerprint=release.catalog_fingerprint,
            )
            identity=build_operational_identity(authority,target_url_path=template.canonical_url_path,storage_mode='storage',baseline_upstream_config_hash=template.config_hash,baseline_engineering_sha256=engineering_sha256(template.configuration))
            return replace(template,upstream_version=version,compatibility_entry=release.entry_id,operational_identity=identity)
        patcher=patch.object(previous.dashboard,'make_preread',exact_preread)
        patcher.start();self.addCleanup(patcher.stop)

class Fan860RecoveryTests(previous.Fan850RecoveryTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.transport.catalog=replace(self.transport.catalog,server_version='8.6.0',tools=tuple(capture()['tools']))

from datetime import timedelta
from ha_mcp_engineering.fan.contracts import FanRefusal
readmission=previous.readmission
_signed_entry_for=previous._signed_entry_for

class Signed860Tests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        readmission.SignedGatewayReadmissionTests.setUp(self)
        self.release = self.compiled.by_version['8.6.0']
        self.capture = capture()
    _raw = readmission.SignedGatewayReadmissionTests._raw
    _registry = readmission.SignedGatewayReadmissionTests._registry
    _initialize = readmission.SignedGatewayReadmissionTests._initialize

    async def test_signed_matching_entry_preserves_closed_wrapper_and_fan_authority(self):
        entry = _signed_entry_for(self.release, version='8.6.0')
        g, t, health = await self._initialize(raw=self._raw(entry=entry), version='8.6.0')
        self.assertEqual(set(g._registered_tool_registry.snapshot()), EXACT_READS, health)
        self.assertTrue(g.fan_provider_authority_token('8.6.0'))
        raw = next((x for x in entry['tool_contracts'] if x['tool_name'] == 'ha_manage_blueprints'))
        self.assertFalse(raw['reviewed_automatic_read'])
        self.assertEqual(raw['policy_classification'], 'mixed_or_requires_wrapper')
        self.assertEqual(t.calls, 0)

    async def test_future_signed_entry_cannot_reuse_version_specific_wrapper(self):
        entry = _signed_entry_for(self.release, version='8.6.1')
        g, t, health = await self._initialize(raw=self._raw(entry=entry), version='8.6.1')
        self.assertEqual(g._registered_tool_registry.snapshot(), {}, health)
        with self.assertRaises(FanRefusal):
            g.fan_provider_authority_token('8.6.1')
        self.assertEqual(t.calls, 0)

    async def test_revoked_exact_entry_withholds_wrapper_and_fan(self):
        revocation = {'entry_id': self.release.entry_id, 'server_name': 'ha-mcp', 'version': '8.6.0', 'image_index_digest': self.release.image_index_digest, 'revoked_at': (self.now - timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%SZ'), 'reason': 'Synthetic exact release revocation.'}
        g, t, health = await self._initialize(raw=self._raw(entry=None, revocations=[revocation]), version='8.6.0')
        self.assertEqual(g._registered_tool_registry.snapshot(), {}, health)
        with self.assertRaises(FanRefusal):
            g.fan_provider_authority_token('8.6.0')
        self.assertEqual(t.calls, 0)

    async def test_authority_retired_during_blueprint_read_cannot_commit(self):
        entry = _signed_entry_for(self.release, version='8.6.0')
        g, t, health = await self._initialize(raw=self._raw(entry=entry), version='8.6.0')
        tool = g._registered_tool_registry.snapshot()['ha_get_blueprint']
        t.catalog = replace(t.catalog, server_version='8.6.1')
        result = json.loads(await tool.run({}))
        self.assertNotEqual(result.get('status'), 'success', result)
        self.assertEqual(t.calls, 0)
        health = g.health_snapshot()['automatic_readmission']
        self.assertEqual(health['issued_lease_count'], 0)

    async def test_timeout_after_dispatch_releases_authority_without_retry(self):
        from ha_mcp_engineering.clients.mcp import DashboardTransportError
        entry = _signed_entry_for(self.release, version='8.6.0')
        g, t, _ = await self._initialize(raw=self._raw(entry=entry), version='8.6.0')
        original = t.execute_read

        async def timeout(*args, **kwargs):
            await original(*args, **kwargs)
            raise DashboardTransportError('timeout')
        t.execute_read = timeout
        result = json.loads(await g._registered_tool_registry.snapshot()['ha_get_blueprint'].run({}))
        self.assertEqual(result['details']['failure_category'], 'timeout', result)
        self.assertTrue(result['metadata']['upstream_dispatch_occurred'])
        self.assertFalse(result['metadata']['fallback_occurred'])
        self.assertEqual(t.calls, 1)
        health = g.health_snapshot()['automatic_readmission']
        self.assertEqual(health['issued_lease_count'], 0)
        self.assertEqual(health['active_commit_count'], 0)

    async def test_cancel_after_dispatch_releases_authority_without_retry(self):
        entry = _signed_entry_for(self.release, version='8.6.0')
        g, t, _ = await self._initialize(raw=self._raw(entry=entry), version='8.6.0')
        original = t.execute_read
        started = asyncio.Event()
        wait = asyncio.Event()

        async def pending(*args, **kwargs):
            await original(*args, **kwargs)
            started.set()
            await wait.wait()
        t.execute_read = pending
        task = asyncio.create_task(g._registered_tool_registry.snapshot()['ha_get_blueprint'].run({}))
        try:
            await asyncio.wait_for(started.wait(), 2)
            self.assertEqual(g.health_snapshot()['automatic_readmission']['active_commit_count'], 1)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(t.calls, 1)
        health = g.health_snapshot()['automatic_readmission']
        self.assertEqual(health['issued_lease_count'], 0)
        self.assertEqual(health['active_commit_count'], 0)


class DashboardGuideWireTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the real fixed-guide transport and provider validation/parser.

    Only SDK I/O is replaced; exact captured catalogs still pass through the
    provider handshake. No fixture bypasses or supplies its acknowledgement.
    """

    def setUp(self):
        from contextlib import asynccontextmanager
        from types import SimpleNamespace
        from mcp import types
        from ha_mcp_engineering.clients import mcp as transport_module
        from ha_mcp_engineering.providers.upstream_dashboard import UpstreamDashboardProvider

        self.transport_module = transport_module
        self.version = '8.6.0'
        self.tools = deepcopy(capture()['tools'])
        self.calls = []
        self.closed = []
        self.entered = asyncio.Event()
        self.pending = False
        self.failure = None
        self.key = 'I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-0123abcd'
        self.payload = {'content': [{'type': 'text', 'text': self.key}], 'isError': False}
        owner = self

        class Session:
            async def initialize(self):
                return SimpleNamespace(protocolVersion='2025-03-26',
                    serverInfo=SimpleNamespace(name='ha-mcp', version=owner.version))

            async def list_tools(self, cursor):
                owner.assertIsNone(cursor)
                return types.ListToolsResult(tools=owner.tools)

            async def call_tool(self, name, arguments, **kwargs):
                owner.calls.append((name, deepcopy(arguments)))
                owner.entered.set()
                if owner.pending:
                    await asyncio.Event().wait()
                if owner.failure:
                    raise owner.failure
                return types.CallToolResult(**owner.payload)

        @asynccontextmanager
        async def streams(*args, **kwargs):
            try:
                yield None, None, None
            finally:
                owner.closed.append('streams')

        @asynccontextmanager
        async def session(*args, **kwargs):
            try:
                yield Session()
            finally:
                owner.closed.append('session')

        for name, replacement in [('streamablehttp_client', streams), ('ClientSession', session)]:
            patcher = patch.object(transport_module, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.transport = transport_module.McpDashboardTransport(
            'http://synthetic.invalid/mcp', timeout_seconds=1, client_version='synthetic')
        self.provider = UpstreamDashboardProvider()
        self.provider._transport = self.transport

    async def test_exact_860_fixed_guide_succeeds_with_removed_selector(self):
        self.assertEqual(await self.provider.best_practices_acknowledgement_key(), self.key)
        self.assertEqual(self.calls, [('ha_get_skill_guide', {'file': 'references/dashboard-guide.md'})])
        self.assertEqual(self.closed, ['session', 'streams'])

    async def test_exact_850_retains_historical_selector(self):
        self.version = '8.5.0'
        self.tools = deepcopy(previous.capture(self.version)['tools'])
        self.assertEqual(await self.provider.best_practices_acknowledgement_key(), self.key)
        self.assertEqual(self.calls, [('ha_get_skill_guide', {
            'skill': 'home-assistant-best-practices', 'file': 'references/dashboard-guide.md'})])

    async def test_caller_cannot_change_internal_file_or_arguments(self):
        for arguments in ({'file': 'SKILL.md'},
                          {'skill': 'other', 'file': 'references/dashboard-guide.md'},
                          {'skill': 'home-assistant-best-practices', 'file': '../dashboard-guide.md'},
                          {'skill': 'home-assistant-best-practices', 'file': 'references/dashboard-guide.md', 'extra': True}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(self.transport_module.DashboardTransportError) as exc:
                    await self.transport._run(tool_name='ha_get_skill_guide', arguments=arguments,
                        capability_validator=self.provider._validate_write_handshake)
                self.assertEqual(exc.exception.category, 'prohibited_argument')
        self.assertEqual(self.calls, [])

    async def test_version_and_catalog_drift_refuse_before_dispatch(self):
        from ha_mcp_engineering.errors import DashboardProviderError
        for drift in ('version', 'guide', 'setter'):
            self.version = '8.6.1' if drift == 'version' else '8.6.0'
            self.tools = deepcopy(capture()['tools'])
            if drift != 'version':
                name = 'ha_get_skill_guide' if drift == 'guide' else 'ha_config_set_dashboard'
                next(t for t in self.tools if t['name'] == name)['description'] += ' changed'
            with self.subTest(drift=drift), self.assertRaises(DashboardProviderError):
                await self.provider.best_practices_acknowledgement_key()
        self.assertEqual(self.calls, [])

    async def test_core_dispatch_authority_still_required(self):
        from ha_mcp_engineering.errors import DashboardProviderError
        telemetry, token = begin_request('synthetic-guide-authority')
        try:
            with patch.object(type(telemetry), 'authorize_core_dispatch', return_value=False):
                with self.assertRaises(DashboardProviderError):
                    await self.provider.best_practices_acknowledgement_key()
            self.assertEqual(self.calls, [])
            self.assertEqual(telemetry.upstream_active_requests, 0)
        finally:
            end_request(token)

    async def test_error_response_cannot_supply_a_key(self):
        from ha_mcp_engineering.errors import DashboardProviderError
        self.payload['isError'] = True
        with self.assertRaises(DashboardProviderError) as exc:
            await self.provider.best_practices_acknowledgement_key()
        self.assertEqual(exc.exception.details['failure_category'], 'upstream_error')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.closed, ['session', 'streams'])

    async def test_missing_key_is_not_manufactured(self):
        from ha_mcp_engineering.errors import DashboardProviderError
        self.payload['content'][0]['text'] = 'Guide with no acknowledgement.'
        with self.assertRaises(DashboardProviderError) as exc:
            await self.provider.best_practices_acknowledgement_key()
        self.assertEqual(exc.exception.details['failure_category'], 'invalid_response')
        self.assertEqual(len(self.calls), 1)

    async def test_oversized_response_remains_bounded(self):
        from ha_mcp_engineering.errors import DashboardProviderError
        self.payload['content'][0]['text'] = 'x' * self.transport_module.MAX_UPSTREAM_CONTENT_CHARS
        with self.assertRaises(DashboardProviderError) as exc:
            await self.provider.best_practices_acknowledgement_key()
        self.assertEqual(exc.exception.details['failure_category'], 'response_too_large')
        self.assertEqual(len(self.calls), 1)

    async def test_transport_failure_has_no_retry_and_closes_session(self):
        from ha_mcp_engineering.errors import DashboardProviderError
        self.failure = TimeoutError('synthetic timeout')
        with self.assertRaises(DashboardProviderError) as exc:
            await self.provider.best_practices_acknowledgement_key()
        self.assertEqual(exc.exception.details['failure_category'], 'timeout')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.closed, ['session', 'streams'])

    async def test_cancellation_closes_owned_session_without_retry(self):
        self.pending = True
        telemetry, token = begin_request('synthetic-guide-cancellation')
        task = asyncio.create_task(self.provider.best_practices_acknowledgement_key())
        try:
            await asyncio.wait_for(self.entered.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(self.closed, ['session', 'streams'])
            self.assertEqual(telemetry.upstream_active_requests, 0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            end_request(token)


class BlueprintContextProjectionTests(unittest.TestCase):
    """The offline count follows compiled bindings without executing source."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('compat860_context', ROOT / 'scripts/codex-context.py')
        self.context = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.context)
        self.runtime = Path('hass_mcp_engineering_beta/ha_mcp_engineering')

    def policy(self, version='8.6.0'):
        return json.loads((ROOT / self.runtime / ('upstream_tool_policy_' + version.replace('.', '_') + '.json')).read_text())

    def count(self, policy, root=ROOT):
        return self.context.blueprint_read_projection_count(root, policy)

    def blueprint(self, policy):
        return next(t for t in policy['tools'] if t['upstream_name'] == 'ha_manage_blueprints')

    def test_legacy_exact_binding(self):
        self.assertEqual(self.count(self.policy('8.5.0')), 1)

    def test_new_exact_binding(self):
        self.assertEqual(self.count(self.policy()), 1)

    def test_wrong_source(self):
        policy = self.policy()
        policy['reviewed_source_commit'] = '0' * 40
        self.assertEqual(self.count(policy), 0)

    def test_wrong_fingerprint(self):
        policy = self.policy()
        self.blueprint(policy)['input_schema_fingerprint'] = '0' * 64
        self.assertEqual(self.count(policy), 0)

    def test_uncompiled_wrapper(self):
        policy = self.policy()
        self.blueprint(policy)['argument_restrictions'] = ['uncompiled-wrapper']
        self.assertEqual(self.count(policy), 0)

    def test_write_annotation(self):
        policy = self.policy()
        self.blueprint(policy)['reviewed_annotations']['destructiveHint'] = True
        self.assertEqual(self.count(policy), 0)

    def test_missing_source(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.count(self.policy(), Path(directory)), 0)

    def source_control(self, suffix):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.runtime / 'providers/upstream_blueprint.py'
            (root / path).parent.mkdir(parents=True)
            (root / path).write_text((ROOT / path).read_text() + suffix)
            return self.count(self.policy(), root)

    def test_source_is_never_executed(self):
        self.assertEqual(self.source_control("\nraise RuntimeError('must_not_execute')\n"), 1)

    def test_ambiguous_binding_refused(self):
        policy = self.policy()
        fingerprint = self.blueprint(policy)['input_schema_fingerprint']
        duplicate = {'duplicate': ('8.6.0', policy['reviewed_source_commit'], fingerprint)}
        self.assertEqual(self.source_control('\nADAPTER_BINDINGS = ' + repr(duplicate) + '\n'), 0)


# The alternate descriptors are actual retained runtime observations. No matcher
# in these tests transforms a foreign observation into a trusted descriptor.
from ha_mcp_engineering.providers import upstream_search_8_6 as search_pair


def component_capture():
    evidence = json.loads((ROOT / 'docs/evidence/upstream-read-compatibility/ha-mcp-8.6.0-component-search.json').read_text())
    return evidence['component_catalog']


class SearchPairTests(unittest.IsolatedAsyncioTestCase):
    gateway = ReadCompatibilityTests.gateway
    call = ReadCompatibilityTests.call

    async def test_selected_strict_diagnostics_match_each_reviewed_catalog(self):
        for observed in (capture(), component_capture()):
            with self.subTest(catalog=schema_fingerprint(observed['tools'])):
                g, t = await self.gateway(observed['tools'])
                expected = schema_fingerprint({'tools': observed['tools']})
                health = g.health_snapshot()
                for field in ('strict_full_contract_fingerprint',
                              'reviewed_strict_full_contract_fingerprint',
                              'observed_strict_full_contract_fingerprint'):
                    self.assertEqual(health[field], expected, field)
                result, telemetry = await self.call(g, 'ha_search', {'query': 'fixture'})
                self.assertTrue(result['success'], result)
                self.assertEqual(telemetry.upstream_request_count, 1)
                self.assertEqual(t.calls, 1)

    async def test_strict_diagnostics_do_not_adopt_unreviewed_sibling(self):
        tools = component_capture()['tools']
        expected = schema_fingerprint({'tools': tools})
        next(t for t in tools if t['name'] == 'ha_get_state')['description'] += ' drift'
        g, t = await self.gateway(tools)
        health = g.health_snapshot()
        self.assertEqual(health['reviewed_strict_full_contract_fingerprint'], expected)
        self.assertEqual(health['observed_strict_full_contract_fingerprint'],
                         schema_fingerprint({'tools': tools}))
        self.assertNotEqual(health['reviewed_strict_full_contract_fingerprint'],
                            health['observed_strict_full_contract_fingerprint'])
        self.assertNotIn('ha_get_state', g._registered_tool_registry.snapshot())
        self.assertEqual(t.calls, 0)
        result, telemetry = await self.call(g, 'ha_search', {'query': 'fixture'})
        self.assertTrue(result['success'], result)
        self.assertEqual(telemetry.upstream_request_count, 1)
        self.assertEqual(t.calls, 1)

    async def test_both_catalogs_exact_public_projection_and_one_dispatch(self):
        release = load_reviewed_upstream_release_registry().by_version['8.6.0']
        old, _ = await self.gateway(previous.capture()['tools'], '8.5.0')
        before = old._registered_tool_registry.snapshot()['ha_search']
        for observed in (capture(), component_capture()):
            with self.subTest(catalog=schema_fingerprint(observed['tools'])):
                validation = validate_reviewed_release_catalog(release,
                    observed_server_name='ha-mcp', observed_upstream_version='8.6.0',
                    observed_protocol_version='2025-03-26', tools=observed['tools'])
                self.assertTrue(validation.valid, validation)
                g, t = await self.gateway(observed['tools'])
                tools = g._registered_tool_registry.snapshot()
                self.assertEqual(set(tools), EXACT_READS)
                current = tools['ha_search']
                for field in ('parameters', 'description', 'annotations'):
                    self.assertEqual(getattr(current, field), getattr(before, field))
                result, telemetry = await self.call(g, 'ha_search', {'query': 'fixture'})
                self.assertTrue(result['success'], result)
                self.assertFalse(result['metadata']['fallback_occurred'])
                self.assertEqual(t.sent, [('ha_search', {'query': 'fixture'})])
                self.assertEqual(telemetry.upstream_request_count, 1)
                self.assertEqual(telemetry.upstream_active_requests, 0)
                self.assertGreater(telemetry.upstream_duration_ms, 0)

    async def test_mutated_descriptor_surfaces_refuse_search_with_useful_sibling(self):
        mutations = {
            'name': lambda t: t.update(name='ha_search_forged'),
            'description': lambda t: t.update(description=t['description'] + ' drift'),
            'input': lambda t: t['inputSchema'].update(description='drift'),
            'output': lambda t: t.update(outputSchema={'type': 'string'}),
            'annotations': lambda t: t['annotations'].update(readOnlyHint=False),
            'title': lambda t: t.update(title='forged'),
            'metadata': lambda t: t['_meta'].update(unreviewed=True),
            'unknown': lambda t: t.update(forged_variant='component_unified'),
        }
        for observed in (capture(), component_capture()):
            for field, mutate in mutations.items():
                with self.subTest(field=field, component=observed is not None):
                    tools = deepcopy(observed['tools'])
                    mutate(next(t for t in tools if t['name'] == 'ha_search'))
                    g, t = await self.gateway(tools)
                    self.assertNotIn('ha_search', g._registered_tool_registry.snapshot())
                    if field == 'name':
                        self.assertEqual(t.sent, [])
                        continue  # Unknown names retain the existing whole-catalog refusal.
                    self.assertIn('ha_get_state', g._registered_tool_registry.snapshot())
                    result, _ = await self.call(g, 'ha_get_state', {'entity_id': 'fan.hamcp_contract_fan'})
                    self.assertTrue(result['success'], result)
                    self.assertEqual([n for n, _ in t.sent], ['ha_get_state'])

    async def test_missing_duplicate_hybrid_and_reordered_are_not_full_catalogs(self):
        release = load_reviewed_upstream_release_registry().by_version['8.6.0']
        for kind in ('missing', 'duplicate', 'hybrid', 'reordered'):
            tools = component_capture()['tools']
            target = next(t for t in tools if t['name'] == 'ha_search')
            if kind == 'missing': tools.remove(target)
            elif kind == 'duplicate': tools.append(deepcopy(target))
            elif kind == 'hybrid':
                target['inputSchema'] = next(t for t in capture()['tools'] if t['name'] == 'ha_search')['inputSchema']
            else: tools.reverse()
            result = validate_reviewed_release_catalog(release,
                observed_server_name='ha-mcp', observed_upstream_version='8.6.0',
                observed_protocol_version='2025-03-26', tools=tools)
            self.assertFalse(result.valid, (kind, result))
            if kind != 'reordered':
                g, t = await self.gateway(tools)
                self.assertNotIn('ha_search', g._registered_tool_registry.snapshot())
                self.assertEqual(t.sent, [])

    async def test_known_transitions_retire_old_search_without_harming_sibling(self):
        for first, second in ((capture(), component_capture()), (component_capture(), capture())):
            g, t = await self.gateway(first['tools'])
            old_search = g._registered_tool_registry.snapshot()['ha_search']
            old_sibling = g._registered_tool_registry.snapshot()['ha_get_state']
            args = {'entity_id': 'fan.hamcp_contract_fan'}
            self.assertTrue(json.loads(await old_sibling.run(args))['success'])
            t.catalog = replace(t.catalog, tools=tuple(second['tools']))
            self.assertTrue(json.loads(await old_sibling.run(args))['success'])
            count = t.calls
            result = json.loads(await old_search.run({'query': 'fixture'}))
            self.assertFalse(result['success'], result)
            self.assertFalse(result['metadata']['upstream_dispatch_occurred'])
            self.assertEqual(t.calls, count)
            self.assertTrue(json.loads(await old_sibling.run(args))['success'])
            await g.initialize(FastMCP('synthetic-search-refresh'))
            self.assertFalse(json.loads(await old_search.run({'query': 'fixture'}))['success'])
            result, _ = await self.call(g, 'ha_search', {'query': 'fixture'})
            self.assertTrue(result['success'], result)
            self.assertEqual([n for n, _ in t.sent].count('ha_search'), 1)

    async def test_legacy_arguments_preserve_absence_explicit_values_and_bounds(self):
        g, t = await self.gateway(component_capture()['tools'])
        accepted = [ {'query':'fixture'}, {'query':'fixture', 'include_config':False},
            {'query':'fixture','include_config':True,'search_types':['automation']},
            {'query':'fixture','config_time_budget':1}, {'query':'fixture','config_time_budget':300},
            {'domain_filter':'light', 'limit':1, 'offset':0} ]
        for args in accepted:
            result, _ = await self.call(g, 'ha_search', args)
            self.assertTrue(result['success'], result)
            self.assertEqual(t.sent[-1], ('ha_search', args))
        count = t.calls
        for args in ({'query':'x','config_time_budget':0}, {'query':'x','config_time_budget':301},
                     {'query':'x','config_time_budget':True}, {'query':'x','include_config':'false'},
                     {'query':'x','search_types':2}, {'query':'x','offset':-1},
                     {'query':'x','limit':0}, {'query':'x','unknown':True}):
            result, _ = await self.call(g, 'ha_search', args)
            self.assertFalse(result['success'], result)
            self.assertFalse(result['metadata']['upstream_dispatch_occurred'])
        self.assertEqual(t.calls, count)

    async def test_search_failure_cancel_and_timeout_settle_without_retry(self):
        from ha_mcp_engineering.clients.mcp import DashboardTransportError
        g, t = await self.gateway(component_capture()['tools'])
        for raw in ({'isError':True,'content':[{'type':'text','text':'synthetic failure'}]},
                    {'isError':False,'content':[]},
                    {'isError':False,'structuredContent':{'success':True,'huge':'x'*100000}}):
            t.override = raw
            result, telemetry = await self.call(g, 'ha_search', {'query':'fixture'})
            self.assertFalse(result['metadata']['fallback_occurred'])
            self.assertNotEqual(result['metadata'].get('completeness'), 'complete')
            self.assertLessEqual(len(json.dumps(result).encode()), 60000)
            self.assertEqual(telemetry.upstream_active_requests, 0)
            self.assertEqual(telemetry.upstream_request_count, 1)
        t.cancel = True
        with self.assertRaises(asyncio.CancelledError): await self.call(g, 'ha_search', {'query':'fixture'})
        t.cancel = False
        original = t.execute_read
        async def timeout(*args, **kwargs):
            await original(*args, **kwargs)
            raise DashboardTransportError('timeout')
        t.execute_read = timeout
        result, telemetry = await self.call(g, 'ha_search', {'query':'fixture'})
        self.assertEqual(result['details']['failure_category'], 'timeout')
        self.assertEqual(telemetry.upstream_active_requests, 0)
        self.assertEqual(t.calls, 5)
        t.execute_read = original
        t.override = None
        result, telemetry = await self.call(g, 'ha_search', {'query':'fixture'})
        self.assertTrue(result['success'], result)
        self.assertEqual(telemetry.upstream_active_requests, 0)


class EvidenceProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.evidence = json.loads((ROOT / 'docs/evidence/upstream-read-compatibility/ha-mcp-8.6.0-component-search.json').read_text())

    def test_component_evidence_has_no_absolute_host_paths(self):
        # Scope this new capture's portability without rewriting historical evidence.
        def check(value, location='$'):
            if isinstance(value, dict):
                for key, child in value.items():
                    check(child, f'{location}.{key}')
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    check(child, f'{location}[{index}]')
            elif isinstance(value, str):
                with self.subTest(location=location):
                    self.assertFalse(PurePosixPath(value).is_absolute())
                    self.assertFalse(PureWindowsPath(value).is_absolute())

        check(self.evidence)
        self.assertEqual(self.evidence['component_capture'],
                         self.evidence['variants']['component_unified']['catalog_capture'])
        self.assertEqual(self.evidence['reference_capture'],
                         self.evidence['variants']['reference']['catalog_capture'])

    def test_embedded_catalog_hash_is_publicly_reproducible(self):
        canonical = json.dumps(self.evidence['component_catalog'], sort_keys=True,
                               separators=(',', ':'), ensure_ascii=True,
                               allow_nan=False).encode('utf-8')
        self.assertEqual(hashlib.sha256(canonical).hexdigest(),
                         self.evidence['component_catalog_sha256'])

    def test_component_file_digest_matches_reviewed_reference(self):
        directory = ROOT / 'docs/evidence/upstream-read-compatibility'
        component = directory / 'ha-mcp-8.6.0-component-search.json'
        review = json.loads((directory / 'ha-mcp-8.6.0-contract-review.json').read_text())
        digest = hashlib.sha256(component.read_bytes()).hexdigest()
        self.assertIn(f'{component.name} sha256:{digest}',
                      review['changed_runtime_descriptors']['ha_search']['review'])


class SearchPairBindingTests(unittest.TestCase):
    def test_strict_variant_bindings_preserve_reference_release(self):
        release = load_reviewed_upstream_release_registry().by_version['8.6.0']
        before = asdict(release)
        for variant, observed in zip(search_pair.VARIANTS, (capture(), component_capture())):
            with self.subTest(variant=variant.name):
                expected = schema_fingerprint({'tools': observed['tools']})
                self.assertEqual(variant.strict_full_contract_fingerprint, expected)
                view = search_pair.search_catalog_view(release, observed['tools'])
                self.assertEqual(view.strict_full_contract_fingerprint, expected)
                self.assertEqual(view.strict_full_contract_fingerprint_model,
                                 release.strict_full_contract_fingerprint_model)
                self.assertEqual(asdict(release), before)
        self.assertIs(search_pair.search_catalog_view(release, capture()['tools']), release)

    def test_foreign_authority_and_changed_entry_cannot_select_pair(self):
        release = load_reviewed_upstream_release_registry().by_version['8.6.0']
        entry = release.policy.by_name['ha_search']
        raw = next(t for t in component_capture()['tools'] if t['name'] == 'ha_search')
        self.assertEqual(search_pair.resolve_search_variant(release, entry, raw).name, 'component_unified')
        for fields in ({'source_commit':'0'*40}, {'version':'8.6.1'}, {'server_name':'forged'},
                       {'allowed_protocol_versions':('2099-01-01',)}, {'revoked':True},
                       {'policy_sha256':'sha256:'+'0'*64}, {'policy_resource':'foreign.json'}):
            foreign = replace(release, **fields)
            self.assertIsNone(search_pair.resolve_search_variant(foreign, entry, raw), fields)
            self.assertIsNone(search_pair.search_capability_contract(foreign, entry), fields)
        for fields in ({'argument_restrictions':'ha-mcp-8.6.0-closed-read-v1'},
                       {'classification':'prohibited_write'}, {'source_evidence':()},
                       {'input_schema_fingerprint':'0'*64}):
            changed = replace(entry, **fields)
            self.assertIsNone(search_pair.resolve_search_variant(release, changed, raw), fields)

    def test_view_changes_only_search_and_preserves_raw_and_reference(self):
        release = load_reviewed_upstream_release_registry().by_version['8.6.0']
        tools = component_capture()['tools']
        before = deepcopy(tools)
        baseline = asdict(release)
        view = search_pair.search_catalog_view(release, tools)
        self.assertEqual(tools, before)
        self.assertEqual(asdict(release), baseline)
        self.assertNotEqual(view.catalog_fingerprint, release.catalog_fingerprint)
        for name, contract in release.tool_contracts:
            if name != 'ha_search': self.assertEqual(contract, view.tool_contracts_by_name[name])
        for entry in release.policy.tools:
            if entry.upstream_name != 'ha_search': self.assertEqual(entry, view.policy.by_name[entry.upstream_name])
        self.assertIs(search_pair.search_catalog_view(release, capture()['tools']), release)

    def test_logical_pair_and_profile_binding_do_not_hide_raw_difference(self):
        from ha_mcp_engineering.ha_mcp_readmission.ha_mcp import _profile_for_release
        release = load_reviewed_upstream_release_registry().by_version['8.6.0']
        entry = release.policy.by_name['ha_search']
        profile = _profile_for_release(release)
        self.assertIn(release.policy_sha256.removeprefix('sha256:')[:16], profile.profile_id)
        capability = next(c for c in profile.capabilities if c.capability_id == 'ha_search')
        self.assertEqual(capability.contract_fingerprint, search_pair.search_capability_contract(release, entry))
        self.assertEqual(len({v.runtime_contract_fingerprint for v in search_pair.VARIANTS}), 2)
        self.assertNotIn(capability.contract_fingerprint, {v.runtime_contract_fingerprint for v in search_pair.VARIANTS})


class ComponentSignedSearchTests(Signed860Tests):
    def setUp(self):
        super().setUp()
        self.capture = component_capture()

    async def test_signed_controls_preserve_selected_strict_diagnostics(self):
        entry = _signed_entry_for(self.release, version='8.6.0')
        for observed in (capture(), component_capture()):
            with self.subTest(catalog=schema_fingerprint(observed['tools'])):
                g, t, _ = await self._initialize(raw=self._raw(entry=entry),
                    version='8.6.0', tools=observed['tools'])
                expected = schema_fingerprint({'tools': observed['tools']})
                health = g.health_snapshot()
                for field in ('strict_full_contract_fingerprint',
                              'reviewed_strict_full_contract_fingerprint',
                              'observed_strict_full_contract_fingerprint'):
                    self.assertEqual(health[field], expected, field)
                self.assertEqual(set(g._registered_tool_registry.snapshot()), EXACT_READS)
                self.assertEqual(t.calls, 0)

    async def test_search_authority_loss_before_commit_refuses_without_retry(self):
        entry = _signed_entry_for(self.release, version='8.6.0')
        g, t, _ = await self._initialize(raw=self._raw(entry=entry), version='8.6.0')
        original = t.execute_read
        async def retire(*args, **kwargs):
            validator = kwargs['catalog_validator']
            def retire_after_validation(catalog):
                validator(catalog)
                g._readmission_coordinator.retire_surface_authority(readmission.UpstreamSurface.HA_MCP)
            kwargs['catalog_validator'] = retire_after_validation
            return await original(*args, **kwargs)
        t.execute_read = retire
        result = json.loads(await g._registered_tool_registry.snapshot()['ha_search'].run({'query':'fixture'}))
        self.assertFalse(result['success'], result)
        self.assertEqual(t.calls, 0)
        health = g.health_snapshot()['automatic_readmission']
        self.assertEqual(health['issued_lease_count'], 0)
        self.assertEqual(health['active_commit_count'], 0)

    async def test_signed_controls_preserve_known_transition_and_stale_handle(self):
        entry = _signed_entry_for(self.release, version='8.6.0')
        for before, after in ((capture(), component_capture()), (component_capture(), capture())):
            g, t, _ = await self._initialize(raw=self._raw(entry=entry), version='8.6.0', tools=before['tools'])
            stale = g._registered_tool_registry.snapshot()['ha_search']
            sibling = g._registered_tool_registry.snapshot()['ha_get_state']
            t.catalog = replace(t.catalog, tools=tuple(after['tools']))
            self.assertTrue(json.loads(await sibling.run({'entity_id':'fan.hamcp_contract_fan'}))['success'])
            count = t.calls
            result = json.loads(await stale.run({'query':'fixture'}))
            self.assertFalse(result['success'], result)
            self.assertEqual(t.calls, count)
            self.assertTrue(json.loads(await sibling.run({'entity_id':'fan.hamcp_contract_fan'}))['success'])
            await g.initialize(FastMCP('synthetic-signed-pair-refresh'))
            self.assertFalse(json.loads(await stale.run({'query':'fixture'}))['success'])
            self.assertTrue(json.loads(await g._registered_tool_registry.snapshot()['ha_search'].run({'query':'fixture'}))['success'])

    async def test_committed_read_settles_when_authority_is_retired_in_flight(self):
        entry = _signed_entry_for(self.release, version='8.6.0')
        g, t, _ = await self._initialize(raw=self._raw(entry=entry), version='8.6.0')
        original = t.execute_read
        async def retire(*args, **kwargs):
            result = await original(*args, **kwargs)
            health = g.health_snapshot()['automatic_readmission']
            self.assertEqual(health['active_commit_count'], 1)
            g._readmission_coordinator.retire_surface_authority(readmission.UpstreamSurface.HA_MCP)
            return result
        t.execute_read = retire
        tool = g._registered_tool_registry.snapshot()['ha_search']
        result = json.loads(await tool.run({'query':'fixture'}))
        # Dispatch was already committed. Preserve settlement instead of retrying
        # the request or presenting a false zero-I/O refusal.
        self.assertTrue(result['success'], result)
        self.assertEqual(t.calls, 1)
        self.assertFalse(json.loads(await tool.run({'query':'fixture'}))['success'])
        self.assertEqual(t.calls, 1)
        health = g.health_snapshot()['automatic_readmission']
        self.assertEqual(health['issued_lease_count'], 0)
        self.assertEqual(health['active_commit_count'], 0)


class SearchCompletenessTests(unittest.IsolatedAsyncioTestCase):
    gateway = ReadCompatibilityTests.gateway
    call = ReadCompatibilityTests.call

    async def test_controlled_provider_partial_warning_error_and_unknown_completeness(self):
        g, t = await self.gateway(component_capture()['tools'])
        base = {'success':True, 'entities':[{'entity_id':'light.synthetic'}],
                'count':1, 'entity_total_matches':1, 'config_total_matches':0,
                'has_more':False, 'entity_has_more':False, 'config_has_more':False,
                'partial':False, 'errors':[], 'warnings':[]}
        cases = [
            ('exhaustive', base, 'complete'),
            ('paged', {**base,'has_more':True,'next_offset':1}, 'complete'),
            ('explicit_partial', {**base,'partial':True,'partial_reason':'synthetic bounded fault'}, 'partial'),
            ('legacy_warning', {**base,'partial':True,'warnings':['component search path failed; served via legacy path']}, 'partial'),
            ('error', {**base,'partial':True,'errors':['synthetic unavailable registry']}, 'partial'),
            ('missing', {'success':True,'entities':[]}, 'partial'),
            ('malformed', {**base,'partial':'false'}, 'partial'),
        ]
        for label, data, expected in cases:
            with self.subTest(case=label):
                t.override = {'isError':False,'structuredContent':data}
                result, telemetry = await self.call(g, 'ha_search', {'query':'synthetic'})
                self.assertEqual(result['metadata']['completeness'], expected, result)
                self.assertFalse(result['metadata']['fallback_occurred'])
                if data.get('warnings'):self.assertEqual(result['data']['warnings'], data['warnings'])
                self.assertEqual(telemetry.upstream_request_count, 1)
                self.assertEqual(telemetry.upstream_active_requests, 0)
        self.assertEqual(t.calls, len(cases))

    async def test_actual_component_results_retain_semantics_both_image_variants(self):
        path = ROOT / 'tests/fixtures/ha_mcp_8_6_0/component-search-results.json'
        provenance = json.loads((path.parent / 'provenance.json').read_text())
        self.assertEqual('sha256:' + hashlib.sha256(path.read_bytes()).hexdigest(), provenance['component_results']['sha256'])
        records = json.loads(path.read_text())
        for image, cases in records.items():
            g, t = await self.gateway(component_capture()['tools'])
            for case in cases:
                with self.subTest(image=image, case=case['case']):
                    t.override = case['result']
                    result, telemetry = await self.call(g, 'ha_search', case['arguments'])
                    self.assertEqual(result['success'], case['engineering_success'])
                    self.assertEqual(result.get('data'), case['engineering_data'])
                    self.assertEqual(result['metadata'].get('completeness'), case['completeness'])
                    self.assertFalse(result['metadata']['fallback_occurred'])
                    self.assertEqual(telemetry.upstream_request_count, 1)
                    self.assertEqual(telemetry.upstream_active_requests, 0)
            self.assertEqual(t.calls, len(cases))


class OctoberPairTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_descriptors_keep_five_reads_under_exact_october_authority(self):
        from test_core_2026_10_device_registry import OctoberSignedSelectionTests
        from ha_mcp_engineering.ha_core_readmission.routes import DEVICE_DEPENDENT_DELEGATED_TOOLS
        case = OctoberSignedSelectionTests()
        await case.asyncSetUp()
        self.addCleanup(case.doCleanups)
        self.assertEqual((await case.publish())['compatible_count'], 21)
        for captured in (capture(), component_capture()):
            transport = Transport(captured['tools'])
            gateway = UpstreamReadGateway()
            gateway.configure(replace(_settings('synthetic-october-860'),
                ha_mcp_release_registry_enabled=False), transport=transport, core_runtime=case.core)
            await gateway.initialize(FastMCP('synthetic-october-860'))
            self.assertTrue(DEVICE_DEPENDENT_DELEGATED_TOOLS <= gateway._registered_tool_registry.snapshot().keys())
            result, telemetry = await ReadCompatibilityTests.call(self, gateway, 'ha_search', {'query': 'fixture'})
            self.assertTrue(result['success'], result)
            self.assertEqual(telemetry.upstream_request_count, 1)
            self.assertEqual(telemetry.upstream_active_requests, 0)
            self.assertFalse(result['metadata']['fallback_occurred'])
        self.assertEqual(case.core.health_snapshot()['active_commit_count'], 0)

    async def test_october_expiry_withdraws_delegated_search_without_dispatch(self):
        from test_core_2026_10_device_registry import OctoberSignedSelectionTests, NOW
        case = OctoberSignedSelectionTests()
        await case.asyncSetUp()
        self.addCleanup(case.doCleanups)
        await case.publish()
        transport = Transport(component_capture()['tools'])
        gateway = UpstreamReadGateway()
        gateway.configure(replace(_settings('synthetic-october-revoked'),
            ha_mcp_release_registry_enabled=False), transport=transport, core_runtime=case.core)
        await gateway.initialize(FastMCP('synthetic-october-revoked'))
        handle = gateway._registered_tool_registry.snapshot()['ha_search']
        case.now = NOW + timedelta(days=400)
        await case.core.reconcile_once('synthetic_october_expired')
        result = json.loads(await handle.run({'query': 'fixture'}))
        self.assertFalse(result['success'], result)
        self.assertEqual(transport.calls, 0)

    def test_october_binding_does_not_change_profile_or_admit_later_core(self):
        from ha_mcp_engineering.ha_core_readmission.probe_profiles import CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE as profile
        from ha_mcp_engineering.ha_core_readmission.routes import delegated_provider_compatibility, DEVICE_DEPENDENT_DELEGATED_TOOLS
        self.assertEqual(profile.contract_fingerprint,
            'sha256:8dad86e454ca58ae558c9229ebd0fd7b748a3a2ff2a9bbb860a1f97a71eaee6b')
        self.assertEqual(profile.delegated_device_adapters, ('8.5.0',))
        for name in DEVICE_DEPENDENT_DELEGATED_TOOLS:
            for core, upstream, selected, expected in (
                ('2026.10.0', '8.6.0', profile, True),
                ('2026.10.1', '8.6.0', profile, False),
                ('2026.9.4', '8.6.0', profile, False),
                ('2026.10.0', '8.6.1', profile, False),
                ('2026.10.0', '8.6.0', replace(profile, profile_id='unknown'), False),
            ):
                with self.subTest(tool=name, core=core, upstream=upstream, profile=selected.profile_id):
                    result, _ = delegated_provider_compatibility(tool_name=name,
                        core_version=core, adapter_version=upstream, probe_profile=selected)
                    self.assertEqual(result, expected)
