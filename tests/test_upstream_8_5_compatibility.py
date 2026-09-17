"""Exact 8.5.0 compatibility with real Engineering routing and durable execution."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hass_mcp_engineering_beta'), str(ROOT / 'tests')]
from mcp.server.fastmcp import FastMCP
from ha_mcp_engineering.clients.upstream_read import McpReadCatalog, McpReadResult
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
from ha_mcp_engineering.providers.upstream_blueprint import read_arguments, completeness
from ha_mcp_engineering.providers.upstream_fan import FanProvider
from ha_mcp_engineering.fan.contracts import FAN_CONTRACT, FAN_RELEASES, FanRefusal
from ha_mcp_engineering.upstream_tool_policy import load_reviewed_upstream_release_registry, validate_reviewed_release_evidence, validate_reviewed_release_catalog
from ha_mcp_engineering.request_context import begin_request, end_request
from test_ha_mcp_production_readmission import _GatewayTransport, _settings
from test_beta58_ha_mcp_8_4_3_continuity import EXACT_READS
import test_typed_fan as fan

def capture(version='8.5.0'):
    data = json.loads((ROOT / f'docs/evidence/upstream-read-compatibility/ha-mcp-{version}.json').read_text())
    review = json.loads((ROOT / f'docs/evidence/upstream-read-compatibility/ha-mcp-{version}-contract-review.json').read_text())
    by = {t['name']: t for t in data['tools']}
    data['tools'] = [by[n] for n in review['runtime_catalog']['runtime_tool_order']]
    return data
RESULTS = json.loads((ROOT / 'tests/fixtures/ha_mcp_8_5_0/read-results.json').read_text())

class GatewayTransport(_GatewayTransport):

    def __init__(self, tools):
        super().__init__(tools, version='8.5.0')
        self.sent = []
        self.result = RESULTS['standalone-call-05.json']['result']

    async def execute_read(self, tool, arguments, **kwargs):
        exchange = await super().execute_read(tool, arguments, **kwargs)
        self.sent.append((tool, deepcopy(arguments)))
        return replace(exchange, call_result=deepcopy(self.result))

class CompatibilityTests(unittest.IsolatedAsyncioTestCase):

    async def gateway(self, tools=None, version='8.5.0'):
        transport = GatewayTransport(tools or capture()['tools'])
        transport.catalog = replace(transport.catalog, server_version=version)
        g = UpstreamReadGateway()
        g.configure(replace(_settings('synthetic-no-key'), ha_mcp_release_registry_enabled=False), transport=transport)
        await g.initialize(FastMCP('synthetic-850'))
        return (g, transport)

    async def test_25_public_reads_and_blueprint_schema_unchanged(self):
        g, t = await self.gateway()
        tools = g._registered_tool_registry.snapshot()
        self.assertEqual(set(tools), EXACT_READS, g.health_snapshot())
        old = next((t for t in capture('8.4.3')['tools'] if t['name'] == 'ha_get_blueprint'))
        self.assertEqual(tools['ha_get_blueprint'].parameters, old['inputSchema'])
        self.assertNotIn('ha_manage_blueprints', tools)
        self.assertNotIn('ha_call_service', tools)
        old_gateway, _ = await self.gateway(capture('8.4.3')['tools'], version='8.4.3')
        old_tools = old_gateway._registered_tool_registry.snapshot()
        for name, tool in tools.items():
            with self.subTest(tool=name):
                self.assertEqual(tool.parameters, old_tools[name].parameters)
                self.assertEqual(tool.description, old_tools[name].description)
                self.assertEqual(tool.annotations, old_tools[name].annotations)
        self.assertEqual(t.sent, [])

    async def call(self, g, args):
        _, token = begin_request('synthetic-blueprint')
        try:
            tool = g._registered_tool_registry.snapshot()['ha_get_blueprint']
            return json.loads(await tool.run(args))
        finally:
            end_request(token)

    async def test_blueprint_metadata_and_full_configuration_stay_distinct(self):
        for variant, expected in [('standalone', 'partial'), ('addon', 'complete')]:
            g, t = await self.gateway()
            record = RESULTS[f'{variant}-call-05.json']
            t.result = record['result']
            result = await self.call(g, {'domain': 'automation', 'path': 'assessment/read.yaml'})
            self.assertEqual(result['metadata']['completeness'], expected, result)
            self.assertEqual(t.sent, [('ha_manage_blueprints', record['arguments'])])
            self.assertEqual('config' in result['data']['data'], variant == 'addon')
            if variant == 'addon':
                self.assertEqual(result['data']['data']['yaml_source'], 'component')

    async def test_list_constructs_only_read_action(self):
        g, t = await self.gateway()
        t.result = RESULTS['standalone-call-04.json']['result']
        result = await self.call(g, {})
        self.assertEqual(t.sent, [('ha_manage_blueprints', {'action': 'list', 'domain': 'automation'})])
        self.assertEqual(result['metadata']['completeness'], 'complete')

    async def test_forbidden_arguments_never_dispatch(self):
        g, t = await self.gateway()
        for field, value in [('action', 'list'), ('action', 'get'), ('action', 'import'), ('action', 'save'), ('action', 'delete'), ('action', 'substitute'), ('url', 'https://example.invalid/x'), ('yaml', 'synthetic'), ('confirm', True), ('overwrite', True), ('input', {})]:
            with self.subTest(field=field, value=value):
                result = await self.call(g, {field: value})
                self.assertNotEqual(result.get('status'), 'success', result)
                self.assertFalse(result['metadata']['upstream_dispatch_occurred'])
        self.assertEqual(t.sent, [])

    async def test_invalid_paths_domains_and_result_identity_refuse(self):
        g, t = await self.gateway()
        for args in ({'path': '../x.yaml'}, {'path': 'https://example.invalid/x.yaml'}, {'path': 'x%2Fy.yaml'}, {'domain': 'light'}, {'path': 42}, {'path': 'a/../x.yaml'}):
            result = await self.call(g, args)
            self.assertNotEqual(result.get('status'), 'success', result)
            self.assertFalse(result['metadata']['upstream_dispatch_occurred'])
        self.assertEqual(t.sent, [])
        t.result = deepcopy(t.result)
        t.result['structuredContent']['data']['path'] = 'other/path.yaml'
        result = await self.call(g, {'path': 'assessment/read.yaml'})
        self.assertNotEqual(result.get('status'), 'success')
        self.assertEqual(len(t.sent), 1)

    async def test_mixed_descriptor_drift_withholds_wrapper(self):
        tools = capture()['tools']
        item = next((t for t in tools if t['name'] == 'ha_manage_blueprints'))
        item['description'] += ' drift'
        g, t = await self.gateway(tools)
        self.assertNotIn('ha_get_blueprint', g._registered_tool_registry.snapshot())
        self.assertEqual(t.sent, [])

    def test_source_download_never_claims_installed_completeness(self):
        data = deepcopy(RESULTS['addon-call-05.json']['result']['structuredContent'])
        data['data']['yaml_source'] = 'source_url'
        self.assertTrue(completeness(data)[0])

    def test_registry_evidence_and_both_policy_modes(self):
        registry = load_reviewed_upstream_release_registry()
        validate_reviewed_release_evidence(repository_root=ROOT)
        release = registry.by_version['8.5.0']
        self.assertEqual(release.policy.by_name['ha_manage_blueprints'].classification, 'mixed_or_requires_wrapper')
        for variant in ('standalone', 'addon'):
            tools = capture()['tools']
            if variant == 'addon':
                for t in tools:
                    t['_meta']['ha_mcp']['policy'].update(deployment='addon', enabled=True, live=True)
            result = validate_reviewed_release_catalog(release, observed_server_name='ha-mcp', observed_upstream_version='8.5.0', observed_protocol_version='2025-03-26', tools=tools)
            self.assertTrue(result.valid, result)

    async def test_text_only_error_is_canonical_and_never_retried(self):
        g, t = await self.gateway()
        error = {'success': False, 'error': {'code': 'RESOURCE_NOT_FOUND', 'message': 'synthetic missing blueprint'}}
        t.result = {'isError': True, 'content': [{'type': 'text', 'text': json.dumps(error)}]}
        result = await self.call(g, {'path': 'missing.yaml'})
        self.assertFalse(result['success'], result)
        self.assertEqual(result['details']['failure_category'], 'resource_not_found', result)
        self.assertTrue(result['metadata']['upstream_dispatch_occurred'])
        self.assertFalse(result['metadata']['fallback_occurred'])
        self.assertEqual(len(t.sent), 1)

    async def test_malformed_and_oversized_results_remain_bounded(self):
        g, t = await self.gateway()
        cases = [{'isError': False, 'content': []}, {'isError': False, 'structuredContent': {'success': True, 'data': {'domain': 'automation', 'count': True, 'blueprints': []}}}, {'isError': False, 'structuredContent': {'success': True, 'data': {'domain': 'automation', 'count': 0, 'blueprints': [{}]}}}, {'isError': False, 'structuredContent': {'success': True, 'data': {'domain': 'automation', 'count': 1, 'blueprints': [{'name': 'synthetic', 'many_fields': {str(i): 'x' * 100 for i in range(600)}}]}}}]
        for raw in cases:
            t.result = raw
            result = await self.call(g, {})
            self.assertFalse(result['success'], result)
            self.assertLessEqual(len(json.dumps(result).encode()), 60000)
            self.assertFalse(result['metadata']['fallback_occurred'])
        self.assertEqual(len(t.sent), len(cases))

    async def test_complete_configuration_preserves_raw_input_and_unicode(self):
        g, t = await self.gateway()
        t.result = deepcopy(RESULTS['addon-call-05.json']['result'])
        data = t.result['structuredContent']['data']
        data['config']['alias'] = 'Synthetic café fixture'
        original = deepcopy(t.result)
        result = await self.call(g, {'domain': 'automation', 'path': 'assessment/read.yaml'})
        self.assertTrue(result['success'], result)
        self.assertEqual(result['metadata']['completeness'], 'complete')
        self.assertEqual(result['data']['data']['config'], original['structuredContent']['data']['config'])
        self.assertEqual(t.result, original)

    async def test_unknown_duplicate_missing_and_incomplete_catalogs_refuse(self):
        g, t = await self.gateway(version='8.5.1')
        self.assertEqual(g._registered_tool_registry.snapshot(), {})
        for kind in ('duplicate', 'missing', 'incomplete'):
            g, t = await self.gateway()
            tool = g._registered_tool_registry.snapshot()['ha_get_blueprint']
            tools = list(t.catalog.tools)
            if kind == 'duplicate':
                tools.append(next((x for x in tools if x['name'] == 'ha_manage_blueprints')))
            if kind == 'missing':
                tools = [x for x in tools if x['name'] != 'ha_manage_blueprints']
            t.catalog = replace(t.catalog, tools=tuple(tools), catalog_complete=kind != 'incomplete')
            result = json.loads(await tool.run({}))
            self.assertFalse(result['success'], result)
            self.assertEqual(t.sent, [], kind)

    def test_policy_data_cannot_enable_an_uncompiled_adapter(self):
        from ha_mcp_engineering.upstream_tool_policy import load_upstream_tool_policy, UpstreamToolPolicyError
        path = ROOT / 'hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy_8_5_0.json'
        value = json.loads(path.read_text())
        binding = {'expected_version': '8.5.0', 'expected_source_tag': 'v8.5.0', 'expected_source_commit': '311d6dc273fb4e9a5b8cde0de15f69472a64fe44'}
        self.assertTrue(load_upstream_tool_policy(path, **binding).by_name['ha_manage_blueprints'].is_read_route)
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'policy.json'
            for changes in ({'argument_restrictions': ['uncompiled-adapter']}, {'classification': 'automatic_read'}, {'exposed_name': 'ha_manage_blueprints'}):
                original = deepcopy(value)
                next((x for x in original['tools'] if x['upstream_name'] == 'ha_manage_blueprints')).update(changes)
                candidate.write_text(json.dumps(original))
                with self.assertRaises(UpstreamToolPolicyError):
                    load_upstream_tool_policy(candidate, **binding)

class Fan850Tests(fan.FanTests):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.transport.catalog = replace(self.transport.catalog, server_version='8.5.0', tools=tuple(capture()['tools']))
        self.provider.authority_token = lambda version='8.4.3': self.transport.authority

    async def test_new_receipts_bind_850_and_reload_without_migration(self):
        req = self.request(percentage=66)
        result = await self.call(req)
        self.assertEqual(result['provider_contract'], FAN_RELEASES['8.5.0'][2])
        self.assertEqual(self.service.load(req.task_id).provider_contract, result['provider_contract'])
        self.assertEqual(self.transport.writes, 1)
        self.settled()

    async def test_provider_swap_between_prepare_and_dispatch_never_writes(self):

        async def change(tool):
            if tool == 'ha_get_state':
                self.transport.catalog = replace(self.transport.catalog, server_version='8.4.3', tools=tuple(capture('8.4.3')['tools']))
        self.transport.before = change
        try:
            await self.call(self.request(percentage=66))
        except FanRefusal:
            pass
        self.assertEqual(self.transport.writes, 0)

    async def test_historical_writer_hash_is_preserved_and_never_relabels(self):
        fixture = ROOT / 'tests/fixtures/fan_beta1'
        raw = (fixture / 'receipt.json').read_bytes()
        proof = json.loads((fixture / 'provenance.json').read_text())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), proof['receipt_sha256'])
        target = self.service._path(proof['task_id'])
        target.write_bytes(raw)
        prepared = self.service.load(proof['task_id'])
        self.assertEqual(prepared.provider_contract, FAN_CONTRACT)
        self.assertEqual(target.read_bytes(), raw)
        with self.assertRaises(FanRefusal):
            await self.service.adapter.preflight(prepared, acquired_locks=())
        self.assertEqual(self.transport.writes, 0)
import test_dashboard_update_mvp as dashboard
import test_typed_fan_review as fan_review

class Dashboard850RuntimeTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        await dashboard.DashboardUpdateRuntimeTests.asyncSetUp(self)
        self.dashboard.version = '8.5.0'
    asyncTearDown = dashboard.DashboardUpdateRuntimeTests.asyncTearDown
    _plan = dashboard.DashboardUpdateRuntimeTests._plan
    _approve = dashboard.DashboardUpdateRuntimeTests._approve
    _execution_evidence = dashboard.DashboardUpdateRuntimeTests._execution_evidence
    test_approved_update = dashboard.DashboardUpdateRuntimeTests.test_approved_update_dispatches_once_and_exactly_verifies
    test_duplicate_apply = dashboard.DashboardUpdateRuntimeTests.test_concurrent_duplicate_apply_commits_at_most_once
    test_exact_restoration = dashboard.DashboardUpdateRuntimeTests.test_second_plan_restores_original_configuration_exactly
    test_stale = dashboard.DashboardUpdateRuntimeTests.test_stale_dashboard_fails_before_dispatch
    test_provider_drift = dashboard.DashboardUpdateRuntimeTests.test_provider_authority_drift_fails_before_dispatch
    test_lost_response = dashboard.DashboardUpdateRuntimeTests.test_lost_response_is_read_back_without_redispatch
    test_failed_response = dashboard.DashboardUpdateRuntimeTests.test_failed_setter_is_truthful_and_never_retried
    test_structured_rejection = dashboard.DashboardUpdateRuntimeTests.test_structured_rejection_and_unchanged_reread_is_not_mismatch

class Dashboard850ProviderTests(unittest.IsolatedAsyncioTestCase):

    async def _execute(self, transport):
        from ha_mcp_engineering.providers.upstream_dashboard import UpstreamDashboardProvider
        from ha_mcp_engineering.f3_dashboard.json_codec import upstream_config_hash
        provider = UpstreamDashboardProvider()
        provider._transport = transport
        key = await provider.best_practices_acknowledgement_key()
        return await provider.execute_governed_dashboard_update(
            url_path='operations',
            configuration={'title': 'After', 'views': []},
            config_hash=upstream_config_hash(transport.configuration),
            best_practice_key=key,
            expected_provider_authority_evidence_hash=(
                provider._dashboard_provider_authority(transport.handshake)['evidence_hash']
            ),
        )

    async def test_upstream_verification_claim_is_only_provider_acknowledgement(self):
        # Synthetic representations of 8.5.0's native/legacy outcome fields.
        # Engineering's separate full-config readback remains authoritative.
        for upstream_verified in (True, False, None):
            with self.subTest(upstream_verified=upstream_verified):
                transport = dashboard._ExactProviderTransport(version='8.5.0')
                transport.write_payload.update(
                    write_committed=True, post_write_verified=upstream_verified
                )
                result = await self._execute(transport)
                self.assertTrue(result['success_claimed'])
                self.assertTrue(result['provider_response_received'])
                self.assertTrue(result['non_atomic'])
                self.assertNotIn('verified', result)
                self.assertNotIn('post_write_verified', result)
                self.assertEqual(transport.write_count, 1)
                self.assertFalse(result['fallback_occurred'])

    async def test_committed_but_failed_response_never_retries(self):
        from ha_mcp_engineering.providers.upstream_dashboard import DashboardProviderError
        transport = dashboard._ExactProviderTransport(version='8.5.0')
        transport.write_payload.update(
            success=False, write_committed=True, post_write_verified=False
        )
        with self.assertRaises(DashboardProviderError) as caught:
            await self._execute(transport)
        self.assertTrue(caught.exception.details['provider_response_received'])
        self.assertEqual(transport.write_count, 1)
        self.assertEqual(transport.configuration, {'title': 'After', 'views': []})

    async def test_partial_acknowledgement_never_retries(self):
        from ha_mcp_engineering.providers.upstream_dashboard import DashboardProviderError
        transport = dashboard._ExactProviderTransport(version='8.5.0')
        del transport.write_payload['config_updated']
        with self.assertRaises(DashboardProviderError):
            await self._execute(transport)
        self.assertEqual(transport.write_count, 1)

    async def test_exact_full_catalog_guide_and_setter_admission(self):
        from ha_mcp_engineering.providers.upstream_dashboard import UpstreamDashboardProvider
        from ha_mcp_engineering.f3_dashboard.json_codec import upstream_config_hash
        transport = dashboard._ExactProviderTransport(version='8.5.0')
        provider = UpstreamDashboardProvider()
        provider._transport = transport
        key = await provider.best_practices_acknowledgement_key()
        result = await provider.execute_governed_dashboard_update(url_path='operations', configuration={'title': 'After', 'views': []}, config_hash=upstream_config_hash(transport.configuration), best_practice_key=key, expected_provider_authority_evidence_hash=provider._dashboard_provider_authority(transport.handshake)['evidence_hash'])
        self.assertEqual(transport.write_count, 1)
        self.assertEqual(transport.guide_count, 1)
        self.assertFalse(result['fallback_occurred'])
        self.assertNotIn('json_patch', transport.arguments)
        self.assertNotIn('python_transform', transport.arguments)

class Fan850RecoveryTests(fan_review.RecoveryProbes):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.transport.catalog = replace(self.transport.catalog, server_version='8.5.0', tools=tuple(capture()['tools']))
        self.service.provider.authority_token = lambda version='8.4.3': self.transport.authority

class Fan850AuditTests(fan_review.LifecycleAuditTests):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.transport.catalog = replace(self.transport.catalog, server_version='8.5.0', tools=tuple(capture()['tools']))
        self.provider.authority_token = lambda version='8.4.3': self.transport.authority
from datetime import timedelta
import tests.test_ha_mcp_production_readmission as readmission
from tests.test_ha_mcp_production_readmission import _signed_entry_for

class Signed850Tests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        readmission.SignedGatewayReadmissionTests.setUp(self)
        self.release = self.compiled.by_version['8.5.0']
        self.capture = capture()
    _raw = readmission.SignedGatewayReadmissionTests._raw
    _registry = readmission.SignedGatewayReadmissionTests._registry
    _initialize = readmission.SignedGatewayReadmissionTests._initialize

    async def test_signed_matching_entry_preserves_closed_wrapper_and_fan_authority(self):
        entry = _signed_entry_for(self.release, version='8.5.0')
        g, t, health = await self._initialize(raw=self._raw(entry=entry), version='8.5.0')
        self.assertEqual(set(g._registered_tool_registry.snapshot()), EXACT_READS, health)
        self.assertTrue(g.fan_provider_authority_token('8.5.0'))
        raw = next((x for x in entry['tool_contracts'] if x['tool_name'] == 'ha_manage_blueprints'))
        self.assertFalse(raw['reviewed_automatic_read'])
        self.assertEqual(raw['policy_classification'], 'mixed_or_requires_wrapper')
        self.assertEqual(t.calls, 0)

    async def test_future_signed_entry_cannot_reuse_version_specific_wrapper(self):
        entry = _signed_entry_for(self.release, version='8.5.1')
        g, t, health = await self._initialize(raw=self._raw(entry=entry), version='8.5.1')
        self.assertEqual(g._registered_tool_registry.snapshot(), {}, health)
        with self.assertRaises(FanRefusal):
            g.fan_provider_authority_token('8.5.1')
        self.assertEqual(t.calls, 0)

    async def test_revoked_exact_entry_withholds_wrapper_and_fan(self):
        revocation = {'entry_id': self.release.entry_id, 'server_name': 'ha-mcp', 'version': '8.5.0', 'image_index_digest': self.release.image_index_digest, 'revoked_at': (self.now - timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%SZ'), 'reason': 'Synthetic exact release revocation.'}
        g, t, health = await self._initialize(raw=self._raw(entry=None, revocations=[revocation]), version='8.5.0')
        self.assertEqual(g._registered_tool_registry.snapshot(), {}, health)
        with self.assertRaises(FanRefusal):
            g.fan_provider_authority_token('8.5.0')
        self.assertEqual(t.calls, 0)

    async def test_authority_retired_during_blueprint_read_cannot_commit(self):
        entry = _signed_entry_for(self.release, version='8.5.0')
        g, t, health = await self._initialize(raw=self._raw(entry=entry), version='8.5.0')
        tool = g._registered_tool_registry.snapshot()['ha_get_blueprint']
        t.catalog = replace(t.catalog, server_version='8.5.1')
        result = json.loads(await tool.run({}))
        self.assertNotEqual(result.get('status'), 'success', result)
        self.assertEqual(t.calls, 0)
        health = g.health_snapshot()['automatic_readmission']
        self.assertEqual(health['issued_lease_count'], 0)

    async def test_timeout_after_dispatch_releases_authority_without_retry(self):
        from ha_mcp_engineering.clients.mcp import DashboardTransportError
        entry = _signed_entry_for(self.release, version='8.5.0')
        g, t, _ = await self._initialize(raw=self._raw(entry=entry), version='8.5.0')
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
        entry = _signed_entry_for(self.release, version='8.5.0')
        g, t, _ = await self._initialize(raw=self._raw(entry=entry), version='8.5.0')
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
import tests.test_core_update_continuity as core_continuity

class Core850Tests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = core_continuity.CoreContinuityTests.asyncSetUp
    runtime = core_continuity.CoreContinuityTests.runtime
    assert_admitted = core_continuity.CoreContinuityTests.assert_admitted

    async def test_existing_signed_core_profile_admits_only_exact_device_adapters(self):
        from ha_mcp_engineering.ha_core_readmission.routes import DEVICE_DEPENDENT_DELEGATED_TOOLS
        core, _ = await self.runtime()
        self.assert_admitted(core)
        required = ('core.delegated_device_effective_area',)
        for name in sorted(DEVICE_DEPENDENT_DELEGATED_TOOLS):
            for version in ('8.4.1', '8.5.1'):
                self.assertIsNone(core.acquire(required, delegated_tool=name, delegated_adapter_version=version))
            for version in ('8.4.3', '8.5.0'):
                lease = core.acquire(required, delegated_tool=name, delegated_adapter_version=version)
                self.assertIsNotNone(lease, (name, version))
                commits = core.consume(lease)
                self.assertIsNotNone(commits)
                self.assertTrue(core.revalidate(lease, commits))
                self.assertTrue(core.finish(commits))
        health = core.health_snapshot()
        self.assertEqual(health['issued_lease_count'], 0)
        self.assertEqual(health['active_commit_count'], 0)
        self.assertEqual(health['fallback_count'], 0)

    async def test_blueprint_projection_retains_core_authority_and_expiry_fence(self):
        core, _ = await self.runtime()
        self.assert_admitted(core)
        t = GatewayTransport(capture()['tools'])
        g = UpstreamReadGateway()
        g.configure(replace(_settings('synthetic-no-key'), ha_mcp_release_registry_enabled=False), transport=t, core_runtime=core)
        await g.initialize(FastMCP('synthetic-core-850'))
        original = t.execute_read

        async def observe(*args, **kwargs):
            result = await original(*args, **kwargs)
            self.assertEqual(core.health_snapshot()['active_commit_count'], 1)
            return result
        t.execute_read = observe
        tool = g._registered_tool_registry.snapshot()['ha_get_blueprint']
        result = json.loads(await tool.run({'path': 'assessment/read.yaml'}))
        self.assertTrue(result['success'], result)
        self.assertEqual(len(t.sent), 1)
        self.assertEqual(core.health_snapshot()['active_commit_count'], 0)
        self.clock += timedelta(days=2)
        result = json.loads(await tool.run({'path': 'assessment/read.yaml'}))
        self.assertFalse(result['success'], result)
        self.assertEqual(len(t.sent), 1)
        self.assertEqual(core.health_snapshot()['issued_lease_count'], 0)
        self.assertEqual(core.health_snapshot()['active_commit_count'], 0)

    def test_published_core_probe_fingerprint_is_unchanged(self):
        from ha_mcp_engineering.ha_core_readmission.probe_profiles import CHILD_DEVICE_PROBE_PROFILE
        self.assertEqual(CHILD_DEVICE_PROBE_PROFILE.contract_fingerprint, 'sha256:c2706d08f75ecef2a7d37265df6eddb03f95bc97d87a08d8632f861202594f46')
if __name__ == '__main__':
    unittest.main()
