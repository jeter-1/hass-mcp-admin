"""Exact 8.6 candidate: real Engineering boundaries, synthetic transports only."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
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
