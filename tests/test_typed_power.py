"""Light/switch contracts over real Engineering provider, gateway and F3 stores.

All targets, transport responses, authority inputs and audit files are synthetic.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'hass_mcp_engineering_beta'), str(ROOT)]
from tests.test_typed_fan import Clock, Core
from ha_mcp_engineering.clients.upstream_read import McpReadCatalog
from ha_mcp_engineering.power.contracts import PowerRequest, PowerRefusal, POWER_RELEASES, digest
from ha_mcp_engineering.power.service import PowerService, PowerCoreAuthority, POWER_OPERATIONS
from ha_mcp_engineering.providers.upstream_power import PowerProvider
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.tools.power import control_power
from ha_mcp_engineering.tools.governance import get_execution_task


class PowerTransport:
    def __init__(self, version):
        folder = ROOT/'docs/evidence/upstream-read-compatibility'
        raw = json.loads((folder/f'ha-mcp-{version}.json').read_text())
        review = json.loads((folder/f'ha-mcp-{version}-contract-review.json').read_text())
        by_name = {item['name']: item for item in raw['tools']}
        names = review['runtime_catalog']['runtime_tool_order']
        self.catalog = McpReadCatalog(protocol_version='2025-03-26', server_name='ha-mcp',
                                     server_version=version, tools=tuple(by_name[name] for name in names), connection_latency_ms=0)
        self.states = {entity: {'entity_id': entity, 'state':'off', 'last_updated':'synthetic-revision-1'}
                       for entity in ['light.synthetic', 'switch.synthetic', 'light.other']}
        self.calls, self.writes = [], 0
        self.mode, self.authority = 'success', 'synthetic-current'
        self.before = None

    async def execute_read(self, tool, args, *, catalog_validator, before_dispatch, **kwargs):
        catalog_validator(self.catalog)
        if self.before:
            await self.before(tool)
        await before_dispatch()
        self.calls.append((tool, deepcopy(args)))
        if tool == 'ha_get_state':
            assert set(args) == {'entity_id', 'fields'}
            assert args['fields'] == ['entity_id', 'state', 'last_updated']
            result = {'data':deepcopy(self.states[args['entity_id']])}
        elif tool == 'ha_list_services':
            assert args == {'domain':args['domain'], 'limit':50, 'offset':0, 'detail_level':'summary'}
            assert args['domain'] in {'light', 'switch'}
            result = {'success': True, 'services':{args['domain']+'.'+action:{} for action in ['turn_on','turn_off']}}
            if self.mode == 'missing_service':
                result['services'] = {}
        elif tool == 'ha_call_service':
            assert set(args) == {'domain','service','entity_id','data','wait','return_response','verbose'}
            assert args['domain'] in {'light','switch'} and args['entity_id'].split('.')[0] == args['domain']
            assert args['service'] in {'turn_on','turn_off'} and args['data'] == {}
            assert args['wait'] is False and args['return_response'] is False and args['verbose'] is False
            self.writes += 1
            if self.mode != 'ack_only':
                self.states[args['entity_id']].update(state='on' if args['service']=='turn_on' else 'off',
                                                     last_updated=f'synthetic-revision-{self.writes+1}')
            if self.mode == 'timeout':
                raise TimeoutError('synthetic-private-provider-text')
            if self.mode == 'malformed':
                return SimpleNamespace(call_result={'structuredContent':None})
            result = {'success': True}
        else:
            raise AssertionError('unrelated provider reachability: '+tool)
        return SimpleNamespace(call_result={'structuredContent':result, 'isError':False})


class PowerTests(unittest.IsolatedAsyncioTestCase):
    version = '8.5.0'

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.clock, self.core = Clock(), Core()
        self.transport = PowerTransport(self.version)
        self.provider = PowerProvider(self.transport, lambda *args:self.transport.authority)
        self.service = PowerService(self.tmp.name, self.provider, PowerCoreAuthority(self.core), now=self.clock)
        self.addAsyncCleanup(self.service.close)
        self.telemetry, token = begin_request('synthetic-power-request')
        self.addCleanup(end_request, token)

    def request(self, entity='light.synthetic', action='turn_on', **kwargs):
        return PowerRequest(entity_id=entity, action=action,
                            operation_id=f'{int(self.clock().timestamp())}-{uuid.uuid4().hex}', **kwargs)

    async def call(self, request):
        self.telemetry.ordinary_power_binding = digest(request.model_dump())
        return await self.service.control(request)

    def settled(self):
        self.assertFalse(self.core.leases or self.core.commits)
        self.assertEqual(self.service.locks.records(), ())
        self.assertEqual(self.service.health()['fallback_count'], 0)

    async def test_exact_on_off_both_domains_one_dispatch_each(self):
        for entity in ['light.synthetic', 'switch.synthetic']:
            for action in ['turn_on','turn_off']:
                result = await self.call(self.request(entity, action))
                self.assertEqual(result['state'], 'succeeded_verified', result)
                self.assertEqual(result['provider'], 'upstream_typed_power')
                self.assertEqual(result['provider_contract'], POWER_RELEASES[self.version][2])
                self.assertEqual(result['provider_attempt_count'], 1)
                self.assertTrue(result['dispatch_intent_recorded'])
                self.assertTrue(result['provider_response_received'])
                self.assertIsNone(result['dispatched_at'])
                self.assertFalse(result['physical_feedback_verified'])
                self.assertNotIn('percentage', result)
                self.assertNotIn('plan_id', result)
                self.settled()
        self.assertEqual(self.transport.writes, 4)

    async def test_incomplete_or_critical_consequence_does_not_block_exact_action(self):
        # Source integration/consumer hints do not become authorization denials.
        for consequence in ['high', 'critical', 'safety-critical', 'unknown', 'incomplete']:
            self.transport.states['switch.synthetic'].update(state='off', last_updated=consequence,
                attributes={'device_class':'outlet', 'synthetic_consequence':consequence})
            result = await self.call(self.request('switch.synthetic'))
            self.assertEqual(result['state'], 'succeeded_verified')
            self.assertEqual(result['consequence_coverage'], 'incomplete')
            self.assertIn('consumer_coverage_incomplete', result['consequences'])
        self.assertEqual(self.transport.writes, 5); self.settled()

    async def test_noop_has_zero_attempt_and_no_intent(self):
        for entity in ['light.synthetic', 'switch.synthetic']:
            result = await self.call(self.request(entity, 'turn_off'))
            self.assertEqual(result['state'], 'succeeded_verified')
            self.assertEqual(result['provider_attempt_count'], 0)
            self.assertFalse(result['dispatch_intent_recorded'])
            self.assertFalse(result['provider_response_received'])
        self.assertEqual(self.transport.writes, 0); self.settled()

    async def test_repeated_request_and_detail_reads_never_mutate(self):
        request = self.request(); first = await self.call(request)
        for _ in range(3):
            self.assertEqual(await self.call(request), first)
            with patch.object(POWER_OPERATIONS, 'service', self.service):
                result = json.loads(await get_execution_task(request.task_id))
            self.assertIn('succeeded_verified', json.dumps(result))
        self.assertEqual(self.transport.writes, 1); self.settled()

    async def test_changed_action_or_target_reusing_id_refuses(self):
        request = self.request(); await self.call(request)
        for changes in [{'action':'turn_off'}, {'entity_id':'switch.synthetic'}]:
            with self.assertRaisesRegex(PowerRefusal, 'rebound'):
                await self.call(request.model_copy(update=changes))
        self.assertEqual(self.transport.writes, 1)

    async def test_other_binding_cannot_authorize_power(self):
        request = self.request()
        self.telemetry.ordinary_fan_binding = digest(request.model_dump())
        with self.assertRaisesRegex(PowerRefusal, 'authorization'):
            await self.service.control(request)
        self.assertEqual(self.transport.calls, [])

    async def test_core_unavailable_or_wrong_version_refuses_before_provider(self):
        for version, allowed in [('2026.9.1',True), ('2026.9.3',True), ('2026.9.2',False)]:
            self.core.version, self.core.allowed = version, allowed
            with self.assertRaisesRegex(PowerRefusal, 'core_authority'):
                await self.call(self.request())
        self.assertEqual(self.transport.calls, []); self.settled()

    async def test_old_or_future_new_operation_refuses(self):
        for seconds in [-301,31]:
            request = self.request().model_copy(update={'operation_id':f'{int(self.clock().timestamp())+seconds}-'+uuid.uuid4().hex})
            with self.assertRaisesRegex(PowerRefusal, 'expired'): await self.call(request)
        self.assertEqual(self.transport.calls, [])

    async def test_invalid_state_revision_or_target_refuses(self):
        original = deepcopy(self.transport.states['light.synthetic'])
        for change in [{'state':'unknown'}, {'state':'unavailable'}, {'state':[]}, {'entity_id':'light.other'}, {'last_updated':None}]:
            self.transport.states['light.synthetic'] = {**original, **change}
            with self.assertRaises(PowerRefusal): await self.call(self.request())
        self.assertEqual(self.transport.writes, 0); self.settled()

    async def test_missing_service_refuses(self):
        self.transport.mode = 'missing_service'
        with self.assertRaisesRegex(PowerRefusal, 'service_unavailable'): await self.call(self.request())
        self.assertEqual(self.transport.writes, 0); self.settled()

    async def test_provider_identity_or_descriptor_drift_refuses(self):
        original = self.transport.catalog
        for change in [{'server_name':'other'}, {'server_version':'8.5.1'}, {'catalog_complete':False},
                       {'protocol_version':'2025-11-25'}, {'tools':[]}]:
            self.transport.catalog = replace(original, **change)
            with self.assertRaises(PowerRefusal): await self.call(self.request())
        self.assertEqual(self.transport.writes, 0); self.settled()

    async def test_retirement_at_dispatch_never_writes(self):
        for retired in ['core', 'provider', 'connector']:
            async def retire(tool):
                if tool == 'ha_call_service':
                    if retired == 'core': self.core.allowed = False
                    elif retired == 'provider': self.transport.authority = 'retired'
                    else: self.telemetry.ordinary_power_binding = None
            self.transport.before = retire
            result = await self.call(self.request())
            self.assertNotEqual(result['state'], 'succeeded_verified', result)
            self.assertEqual(self.transport.writes, 0)
            self.core.allowed = True; self.transport.authority = 'synthetic-current'
        self.settled()

    async def test_changed_revision_at_dispatch_refuses_even_same_state(self):
        async def change(tool):
            if tool == 'ha_call_service': self.transport.states['light.synthetic']['last_updated'] = 'outside-edit'
        self.transport.before = change
        result = await self.call(self.request())
        self.assertNotEqual(result['state'], 'succeeded_verified')
        self.assertEqual(self.transport.writes, 0); self.settled()

    async def test_uncertain_response_then_readback_never_replays(self):
        for mode in ['timeout', 'malformed']:
            self.transport.mode = mode
            self.transport.states['light.synthetic']['state'] = 'off'
            request = self.request(); result = await self.call(request)
            self.assertEqual(result['state'], 'succeeded_verified', result)
            self.assertFalse(result['provider_response_received'])
            self.assertNotIn('synthetic-private-provider-text', json.dumps(result))
            await self.call(request)
        self.assertEqual(self.transport.writes, 2); self.settled()

    async def test_ack_without_effect_remains_unverified_then_recovers_readonly(self):
        self.transport.mode = 'ack_only'; request = self.request()
        result = await self.call(request)
        self.assertFalse(result['terminal']); self.assertEqual(result['state'], 'observing')
        self.transport.states['light.synthetic'].update(state='on',last_updated='later')
        self.clock.advance(121)
        rebuilt = PowerService(self.tmp.name, self.provider, self.service.core, now=self.clock)
        self.addAsyncCleanup(rebuilt.close)
        result = await rebuilt.reconcile(request.task_id)
        self.assertEqual(result['state'], 'succeeded_verified', result)
        self.assertEqual(self.transport.writes, 1); self.settled()

    async def test_unverified_terminal_holds_only_target_and_other_entity_works(self):
        self.transport.mode = 'ack_only'; request = self.request()
        await self.call(request); self.clock.advance(181)
        result = await self.service.reconcile(request.task_id)
        self.assertEqual(result['state'], 'manual_review_required')
        self.assertEqual({x.key for x in self.service.locks.records()}, {'entity:light.synthetic'})
        self.transport.mode = 'success'
        other = await self.call(self.request('switch.synthetic'))
        self.assertEqual(other['state'], 'succeeded_verified')
        again = await self.call(self.request())
        self.assertNotEqual(again['state'], 'succeeded_verified')
        self.assertEqual(self.transport.writes, 2)
        self.assertFalse(self.core.leases or self.core.commits)

    async def test_duplicate_overlapping_active_owner_dispatches_once(self):
        entered, resume = asyncio.Event(), asyncio.Event()
        async def block(tool):
            if tool == 'ha_call_service': entered.set(); await resume.wait()
        self.transport.before = block; request = self.request()
        pending = asyncio.create_task(self.call(request))
        await entered.wait()
        duplicate = await self.call(request)
        self.assertFalse(duplicate['terminal'])
        await self.service.recover_once()
        self.assertEqual(self.transport.writes, 0)
        resume.set(); result = await pending
        self.assertEqual(result['state'], 'succeeded_verified')
        self.assertEqual(self.transport.writes, 1); self.settled()

    async def test_cancelled_caller_retires_preintent_authority(self):
        entered, resume = asyncio.Event(), asyncio.Event()
        async def block(tool):
            if tool == 'ha_call_service': entered.set(); await resume.wait()
        self.transport.before = block
        pending = asyncio.create_task(self.call(self.request())); await entered.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError): await pending
        self.telemetry.ordinary_power_binding = None
        resume.set(); await asyncio.gather(*list(self.service.active.values()))
        self.assertEqual(self.transport.writes, 0); self.settled()

    async def test_ownerless_preintent_declaration_is_cancelled_without_dispatch(self):
        request = self.request()
        prepared = await self.service.adapter.prepare(request); self.service.save(prepared)
        self.clock.advance(301)
        result = await self.service.reconcile(request.task_id)
        self.assertEqual(result['state'], 'cancelled_pre_dispatch')
        self.assertEqual(self.transport.writes, 0); self.settled()

    async def test_response_budget_and_flat_task_receipt_retain_reconciliation(self):
        request = self.request(); self.telemetry.ordinary_power_binding = digest(request.model_dump())
        with patch.object(POWER_OPERATIONS, 'service', self.service):
            result = json.loads(await control_power(**request.model_dump()))
        self.assertIn('succeeded_verified', json.dumps(result))
        from ha_mcp_engineering.models.responses import SuccessResponse
        receipt = self.service.receipt(request.task_id)
        encoded = SuccessResponse(operation='get_execution_task', summary='synthetic',
            request_id='x'*128, data=receipt).to_json(1024)
        value = json.loads(encoded)
        self.assertLessEqual(len(encoded.encode()), 1024)
        self.assertIn(request.task_id, encoded)
        self.assertIn(receipt['operation_hash'], encoded)
        self.assertIn('succeeded_verified', encoded)
        self.assertEqual(self.transport.writes, 1)

    async def test_audit_failure_does_not_authorize_a_second_dispatch(self):
        self.service.executions.audit = SimpleNamespace(write_batch=lambda _: (_ for _ in ()).throw(OSError('synthetic-private')))
        request = self.request(); result = await self.call(request)
        self.assertEqual(result['state'], 'succeeded_verified')
        await self.call(request)
        self.assertGreater(self.service.health()['audit_projection_failures'], 0)
        self.assertEqual(self.transport.writes, 1); self.settled()

    async def test_background_recovery_keeps_provider_and_original_request_attribution(self):
        from ha_mcp_engineering.audit import AuditLogger
        audit_path = Path(self.tmp.name)/'audit.jsonl'
        self.service.executions.audit = AuditLogger(str(audit_path), 'synthetic-audit-secret')
        self.transport.mode = 'ack_only'; request = self.request()
        await self.call(request)
        self.transport.states['light.synthetic'].update(state='on', last_updated='late')
        self.clock.advance(121)
        self.telemetry.request_id = 'unrelated-read-request'
        await self.service.recover_once()
        rows = [json.loads(line) for line in audit_path.read_text().splitlines()]
        lifecycle = [x for x in rows if x.get('operation_class') == 'typed_power_lifecycle']
        self.assertTrue(lifecycle)
        self.assertTrue(any(x.get('read_only_recovery') and x.get('state') == 'succeeded_verified' for x in lifecycle))
        self.assertEqual({x['provider'] for x in lifecycle}, {'upstream_typed_power'})
        self.assertEqual({x['request_id'] for x in lifecycle}, {'synthetic-power-request'})
        self.assertEqual(self.transport.writes, 1); self.settled()


class Power843Tests(PowerTests):
    version = '8.4.3'


class PowerBoundaryTests(unittest.TestCase):
    def test_closed_request_rejects_other_domains_bulk_toggle_and_service_data(self):
        valid = dict(entity_id='light.synthetic', action='turn_on', operation_id='1789552800-'+'a'*32)
        changes = [{'entity_id':x} for x in ['fan.synthetic','lock.synthetic','cover.synthetic','climate.synthetic','light.*','all','light.a,light.b','light.a light.b',['light.a']]]
        changes += [{'action':'toggle'},{'action':'set_percentage'},{'data':{}},{'brightness':50},{'percentage':50},
                    {'domain':'light'}, {'area_id':'synthetic'}, {'device_id':'synthetic'}, {'wait':True}, {'ws_command':'synthetic'}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                PowerRequest.model_validate({**valid, **change})

    def test_power_and_fan_task_namespaces_do_not_collide(self):
        from ha_mcp_engineering.fan.contracts import FanRequest
        operation = '1789552800-'+'a'*32
        self.assertNotEqual(PowerRequest(entity_id='light.synthetic', action='turn_on',operation_id=operation).task_id,
                            FanRequest(entity_id='fan.synthetic',action='turn_on',operation_id=operation).task_id)

    def test_provider_authority_is_distinct_and_respects_retained_denial(self):
        from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
        from tests.test_readonly_upstream_gateway import settings
        gateway = UpstreamReadGateway(); gateway.configure(settings())
        for version in ['8.4.3','8.5.0']:
            self.assertNotEqual(gateway.fan_provider_authority_token(version),gateway.power_provider_authority_token(version))
        gateway._readmission_selector = SimpleNamespace(select=lambda **kw:SimpleNamespace(
            authority=SimpleNamespace(decisions=[SimpleNamespace(status=SimpleNamespace(value='deny_only'))])))
        with self.assertRaisesRegex(PowerRefusal, 'denied'): gateway.power_provider_authority_token('8.5.0')
        with self.assertRaises(PowerRefusal): gateway.power_provider_authority_token('8.5.1')

    def test_descriptor_routing_and_metadata_agree(self):
        from ha_mcp_engineering.tools.registry import get_registered_server, ENGINEERING_STATIC_TOOL_COUNT
        from ha_mcp_engineering.capabilities import capability_for_tool, CAPABILITY_PROVIDER_MATRIX
        from ha_mcp_engineering.providers.routing import routing_for_tool
        from ha_mcp_engineering.mcp_sdk_compatibility import McpSdkToolRegistry
        tools = McpSdkToolRegistry(get_registered_server()).snapshot()
        tool = tools['control_power']
        self.assertEqual(set(tool.parameters['properties']), {'entity_id','action','operation_id'})
        self.assertEqual(tool.parameters['properties']['action']['enum'], ['turn_on','turn_off'])
        self.assertFalse(tool.annotations.readOnlyHint)
        self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT, 53)
        self.assertEqual(capability_for_tool('control_power')['provider'], 'upstream_typed_power')
        self.assertEqual(routing_for_tool('control_power').fallback_providers, ())
        matrix = next(x for x in CAPABILITY_PROVIDER_MATRIX if x['tool']=='control_power')
        for release in POWER_RELEASES.values(): self.assertIn(release[2], matrix['trust_profile'])


class PowerGatewayTests(unittest.TestCase):
    def test_actual_authenticated_gateway_exact_authority_audit_and_refusals(self):
        from tests.test_mcp_inbound_security import settings_for, SECRET
        from tests.same_thread_asgi_client import SameThreadAsgiTestClient
        from ha_mcp_engineering.mcp_server import create_mcp_server
        from ha_mcp_engineering.routing import AuthenticatedMcpGateway
        from ha_mcp_engineering.audit import AuditLogger
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory); clock, core = Clock(), Core()
            transport = PowerTransport('8.5.0'); audit = AuditLogger(settings.audit_path, SECRET)
            service = PowerService(directory, PowerProvider(transport, lambda *args:transport.authority),
                                   PowerCoreAuthority(core), now=clock, audit=audit)
            server = create_mcp_server(settings); server.tool()(control_power)
            inner = server.streamable_http_app()
            gateway = AuthenticatedMcpGateway(inner, settings, audit)
            args = dict(entity_id='light.synthetic', action='turn_on',
                        operation_id=f'{int(clock().timestamp())}-{uuid.uuid4().hex}')
            body = {'jsonrpc':'2.0', 'id':1, 'method':'tools/call', 'params':{'name':'control_power','arguments':args}}
            headers = {'accept':'application/json, text/event-stream'}
            with patch.object(POWER_OPERATIONS, 'service', service), SameThreadAsgiTestClient(
                    gateway, lifespan_app=inner, base_url='http://127.0.0.1:8100') as client:
                # Authentication/transport policy still prevents entry to the tool.
                self.assertEqual(client.post('/synthetic-wrong/mcp', json=body, headers=headers).status_code, 404)
                self.assertEqual(client.post('/'+SECRET+'/mcp', json=body,
                    headers={**headers,'origin':'https://hostile.invalid'}).status_code, 403)
                self.assertEqual(transport.calls, [])
                for change in [{'entity_id':'fan.synthetic'}, {'data':{}}, {'action':'toggle'}, {'entity_id':['light.synthetic']}]:
                    bad = deepcopy(body); bad['params']['arguments'].update(change)
                    self.assertEqual(client.post('/'+SECRET+'/mcp', json=bad, headers=headers).status_code, 400)
                self.assertEqual(transport.calls, [])
                response = client.post('/'+SECRET+'/mcp', json=body, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertIn('succeeded_verified', response.text)
                self.assertIn('upstream_typed_power', response.text)
                # Reconciliation of the same operation is permitted; no second write.
                repeat = client.post('/'+SECRET+'/mcp', json=body, headers=headers)
                self.assertIn('succeeded_verified', repeat.text)
                core.allowed = False
                refused = deepcopy(body); refused['params']['arguments']['operation_id'] = f'{int(clock().timestamp())}-{uuid.uuid4().hex}'
                result = client.post('/'+SECRET+'/mcp', json=refused, headers=headers)
                self.assertIn('power_core_authority_unavailable', result.text)
            self.assertEqual(transport.writes, 1)
            rows = [json.loads(line) for line in Path(settings.audit_path).read_text().splitlines()]
            successful = [r for r in rows if r.get('analysis_summary',{}).get('power_outcome')=='succeeded_verified']
            self.assertEqual(len(successful), 2)
            self.assertTrue(all(r['analysis_summary']['provider']=='upstream_typed_power' for r in successful))
            self.assertTrue(any(r.get('analysis_summary',{}).get('power_outcome')=='request_failed_reconcile_task' for r in rows))
            self.assertTrue(any(r.get('operation_class')=='typed_power_lifecycle' and r.get('state')=='succeeded_verified' for r in rows))
            self.assertFalse(core.leases or core.commits)
            self.assertEqual(service.locks.records(), ())

    def test_request_authority_is_retired_after_gateway_returns(self):
        from tests.test_mcp_inbound_security import settings_for, SECRET
        from tests.same_thread_asgi_client import SameThreadAsgiTestClient
        from ha_mcp_engineering.mcp_server import create_mcp_server
        from ha_mcp_engineering.routing import AuthenticatedMcpGateway
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.request_context import current_telemetry
        seen = []
        class Service:
            async def control(self, request):
                telemetry = current_telemetry()
                assert telemetry.ordinary_power_binding == digest(request.model_dump())
                assert telemetry.ordinary_fan_binding is None
                seen.append(telemetry)
                return {'state':'synthetic-authorized'}
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory); server = create_mcp_server(settings); server.tool()(control_power)
            inner = server.streamable_http_app(); gateway = AuthenticatedMcpGateway(inner,settings,AuditLogger(settings.audit_path, SECRET))
            args = dict(entity_id='switch.synthetic', action='turn_off', operation_id='1789552800-'+'a'*32)
            with patch.object(POWER_OPERATIONS,'service',Service()), SameThreadAsgiTestClient(gateway,lifespan_app=inner,base_url='http://127.0.0.1:8100') as client:
                response = client.post('/'+SECRET+'/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'control_power','arguments':args}},headers={'accept':'application/json, text/event-stream'})
                self.assertIn('synthetic-authorized',response.text)
            self.assertEqual(len(seen),1)
            self.assertIsNone(seen[0].ordinary_power_binding)


class PowerSignedAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from tests.test_core_update_continuity import CoreContinuityTests
        self.fixture = CoreContinuityTests(); await self.fixture.asyncSetUp()
        self.addCleanup(self.fixture.doCleanups)

    async def test_real_signed_core_authority_for_each_closed_domain(self):
        from ha_mcp_engineering.f3.contracts import OperationTarget
        runtime, _ = await self.fixture.runtime()
        guard = PowerCoreAuthority(runtime)
        for domain in ['light','switch']:
            token = await guard.acquire(SimpleNamespace(target=OperationTarget(domain,domain+'.synthetic')))
            self.assertIsNotNone(token)
            commits = guard.consume(token); self.assertIsNotNone(commits)
            self.assertTrue(guard.revalidate(token,commits)); guard.finish(commits)
        self.assertEqual(runtime.health_snapshot()['issued_lease_count'],0)
        self.assertEqual(runtime.health_snapshot()['active_commit_count'],0)
        self.fixture.assert_admitted(runtime,17)

    async def test_real_retained_revocation_retires_power_authority(self):
        from core_registry_fixtures import core_revocation
        runtime, _ = await self.fixture.runtime(); guard = PowerCoreAuthority(runtime)
        target = SimpleNamespace(target=SimpleNamespace(target_type="light",target_id="light.synthetic"))
        token = await guard.acquire(target); self.assertIsNotNone(token)
        commits = guard.consume(token)
        self.fixture.raw = self.fixture.next_journal(entries=[],revocations=[core_revocation()])
        self.assertTrue(await self.fixture.registry.refresh())
        self.assertFalse(guard.revalidate(token,commits)); guard.finish(commits)
        self.assertIsNone(await guard.acquire(target))

    async def test_missing_signed_positive_authority_cannot_grant_power(self):
        self.fixture.raw = self.fixture.signer.journal_raw(entries=[])
        runtime, _ = await self.fixture.runtime()
        self.assertIsNone(await PowerCoreAuthority(runtime).acquire(
            SimpleNamespace(target=SimpleNamespace(target_type="switch", target_id="switch.synthetic"))))
