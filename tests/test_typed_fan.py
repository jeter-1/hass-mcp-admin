"""HAMCP-141: synthetic targets/transports, real wrapper, F3 and durable stores."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'hass_mcp_engineering_beta'), str(ROOT/'tests')]
from ha_mcp_engineering.fan.contracts import FanRequest, FanRefusal, digest
from ha_mcp_engineering.fan.service import FanService, FAN_OPERATIONS
from ha_mcp_engineering.fan.authority import FanCoreAuthority
from ha_mcp_engineering.providers.upstream_fan import FanProvider
from ha_mcp_engineering.clients.upstream_read import McpReadCatalog
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.f3.models import LockOwner, LockTiming
from ha_mcp_engineering.f3.contracts import LockRequest, LockScope, LockMode
from ha_mcp_engineering.tools.fan import control_fan
from ha_mcp_engineering.tools.governance import get_execution_task


def catalog():
    raw = json.loads((ROOT/'docs/evidence/upstream-read-compatibility/ha-mcp-8.4.3.json').read_text())
    review = json.loads((ROOT/"docs/evidence/upstream-read-compatibility/ha-mcp-8.4.3-contract-review.json").read_text())
    by_name = {item["name"]:item for item in raw["tools"]}
    raw["tools"] = tuple(by_name[name] for name in review["runtime_catalog"]["runtime_tool_order"])
    return raw


class Clock:
    def __init__(self):
        self.value = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
    def __call__(self):
        return self.value
    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class Core:
    """Synthetic authority input; the production adapter/guard remain in use."""
    def __init__(self):
        self.allowed = True
        self.version = '2026.9.2'
        self.generation = 1
        self.leases = set()
        self.commits = set()
    async def reconcile_once(self, trigger):
        return None
    def route_status(self, requirements):
        # This fixture models the shipped legacy contract, not signed
        # applicability for the newly compiled operation profiles.
        return {"available": False}
    def acquire(self, requirements, **kwargs):
        if not self.allowed or kwargs['expected_core_version'] != self.version:
            return None
        token = (uuid.uuid4().hex, self.generation)
        self.leases.add(token)
        return token
    def consume(self, token):
        if token not in self.leases or not self.allowed:
            return None
        self.leases.remove(token);self.commits.add(token)
        return token
    def revalidate(self, token, commit):
        return self.allowed and token == commit and token in self.commits and token[1] == self.generation
    def release(self, token):
        self.leases.discard(token)
    def finish(self, token):
        self.commits.discard(token)


class Transport:
    def __init__(self):
        raw = catalog()
        self.catalog = McpReadCatalog(
            protocol_version='2025-03-26', server_name='ha-mcp', server_version='8.4.3',
            tools=raw['tools'], connection_latency_ms=0,
        )
        self.state = {'entity_id':'fan.synthetic', 'state':'off',
                      'attributes':{'percentage':0, 'supported_features':49},
                      'last_updated':'synthetic-revision-1'}
        self.calls = [];self.writes = 0
        self.mode = 'success';self.authority = 'valid'
        self.before = None
    async def execute_read(self, tool, args, *, catalog_validator, before_dispatch, **kwargs):
        catalog_validator(self.catalog)
        if self.before:
            await self.before(tool)
        await before_dispatch()
        self.calls.append((tool, deepcopy(args)))
        if tool == 'ha_get_state':
            result = {'data':deepcopy(self.state)}
        elif tool == 'ha_list_services':
            result = {'success':True,'services':{f'fan.{name}':{} for name in ('turn_on','turn_off','set_percentage')}}
        elif tool == 'ha_call_service':
            self.writes += 1
            self.assert_fixed(args)
            if self.mode != 'ack_only':
                percent = args['data'].get('percentage')
                self.state['state'] = 'off' if args['service']=='turn_off' or percent==0 else 'on'
                self.state['attributes']['percentage'] = (0 if self.state['state']=='off' else percent if percent is not None else 50)
                self.state['last_updated'] = f'synthetic-revision-{self.writes+1}'
            if self.mode == 'timeout':
                raise TimeoutError('synthetic secret must never escape')
            if self.mode == 'malformed':
                return SimpleNamespace(call_result={'structuredContent':None})
            result = {'success':True}
        else:
            raise AssertionError('unrelated provider call')
        return SimpleNamespace(call_result={'structuredContent':result,'isError':False})
    def assert_fixed(self, args):
        assert set(args) == {'domain','service','entity_id','data','wait','return_response','verbose'}
        assert args['domain']=='fan' and args['entity_id']=='fan.synthetic'
        assert args['wait'] is False and args['return_response'] is False and args['verbose'] is False


class FanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.clock=Clock();self.core=Core();self.transport=Transport()
        self.provider=FanProvider(self.transport, lambda:self.transport.authority)
        self.service=FanService(self.tmp.name,self.provider,FanCoreAuthority(self.core),now=self.clock)
        self.addAsyncCleanup(self.service.close)
        self.telemetry,self.token=begin_request('synthetic-request')
        self.addCleanup(end_request,self.token)
    def request(self, action='turn_on', percentage=None, **kwargs):
        return FanRequest(entity_id='fan.synthetic',action=action,percentage=percentage,
                          operation_id=f'{int(self.clock().timestamp())}-{uuid.uuid4().hex}',**kwargs)
    async def call(self, request):
        self.telemetry.ordinary_fan_binding=digest(request.model_dump())
        return await self.service.control(request)
    def settled(self):
        self.assertEqual(self.core.leases,set());self.assertEqual(self.core.commits,set())
        self.assertEqual(self.service.locks.records(),())
    async def test_on_percentage_off_use_one_call_each_and_truthful_receipts(self):
        for action, percent in [('turn_on',50),('set_percentage',70),('turn_off',None)]:
            result=await self.call(self.request(action,percent))
            self.assertEqual(result['state'],'succeeded_verified',result)
            self.assertEqual(result['provider_attempt_count'],1)
            self.assertTrue(result['dispatch_intent_recorded'])
            self.assertIsNone(result['dispatched_at'])
            self.assertNotIn('plan_id',result)
            self.settled()
        self.assertEqual(self.transport.writes,3)
    async def test_zero_percentage_turns_off_and_noop_is_zero_dispatch(self):
        result=await self.call(self.request('set_percentage',0))
        self.assertEqual(result['state'],'succeeded_verified',result)
        self.assertEqual(self.transport.writes,0);self.settled()
    async def test_duplicate_reads_never_repeat_mutation(self):
        request=self.request(percentage=50)
        first=await self.call(request)
        for _ in range(3):
            result=await self.call(request)
            self.assertEqual(result,first)
        self.assertEqual(self.transport.writes,1);self.settled()
    async def test_changed_arguments_reusing_id_refuse(self):
        request=self.request(percentage=50);await self.call(request)
        changed=request.model_copy(update={'percentage':75})
        with self.assertRaisesRegex(FanRefusal,'rebound'):await self.call(changed)
        self.assertEqual(self.transport.writes,1)
    async def test_no_gateway_authority_never_reaches_provider(self):
        with self.assertRaisesRegex(FanRefusal,'authorization'):await self.service.control(self.request())
        self.assertEqual(self.transport.calls,[])
    async def test_wrong_version_and_withheld_core_refuse_before_io(self):
        for version,allowed in [('2026.9.1',True),('2026.9.3',True),('2026.9.2',False)]:
            self.core.version=version;self.core.allowed=allowed
            with self.assertRaisesRegex(FanRefusal,'core_authority'):await self.call(self.request())
        self.assertEqual(self.transport.calls,[]);self.settled()
    async def test_timeout_or_malformed_ack_then_exact_readback_is_not_replayed(self):
        for mode in ['timeout','malformed']:
            self.transport.mode=mode;self.transport.state['state']='off'
            request=self.request(percentage=50);result=await self.call(request)
            self.assertEqual(result['state'],'succeeded_verified',result)
            self.assertFalse(result['provider_response_received'])
            await self.call(request)
        self.assertEqual(self.transport.writes,2);self.settled()
    async def test_ack_without_effect_observes_then_readonly_recovery_succeeds(self):
        self.transport.mode='ack_only';request=self.request(percentage=50)
        result=await self.call(request)
        self.assertFalse(result['terminal']);self.assertNotEqual(result['state'],'succeeded_verified')
        self.transport.state.update(state='on',last_updated='later')
        self.transport.state['attributes']['percentage']=50
        self.clock.advance(121)
        result=await self.service.reconcile(request.task_id)
        self.assertEqual(result['state'],'succeeded_verified',result)
        self.assertEqual(self.transport.writes,1);self.settled()
    async def test_core_retirement_at_dispatch_never_writes(self):
        async def retire(tool):
            if tool=='ha_call_service':self.core.allowed=False
        self.transport.before=retire
        result=await self.call(self.request())
        self.assertEqual(self.transport.writes,0)
        self.assertNotEqual(result['state'],'succeeded_verified')
    async def test_gateway_authority_retired_at_dispatch_never_writes(self):
        async def retire(tool):
            if tool=='ha_call_service':self.telemetry.ordinary_fan_binding=None
        self.transport.before=retire
        result=await self.call(self.request())
        self.assertEqual(self.transport.writes,0)
        self.assertNotEqual(result['state'],'succeeded_verified')
    async def test_provider_generation_changed_in_session_refuses(self):
        async def retire(tool):
            if tool=='ha_call_service':self.transport.authority='retired'
        self.transport.before=retire
        result=await self.call(self.request())
        self.assertEqual(self.transport.writes,0)
        self.assertNotEqual(result['state'],'succeeded_verified')
    async def test_provider_schema_drift_refuses(self):
        self.transport.catalog=replace(self.transport.catalog,tools=[])
        with self.assertRaisesRegex(FanRefusal,'contract'):await self.call(self.request())
        self.assertEqual(self.transport.writes,0)
    async def test_unknown_or_old_id_never_dispatches(self):
        for seconds in [-301,31]:
            request=self.request().model_copy(update={'operation_id':f'{int(self.clock().timestamp())+seconds}-{uuid.uuid4().hex}'})
            with self.assertRaisesRegex(FanRefusal,'expired'):await self.call(request)
        self.assertEqual(self.transport.writes,0)
    async def test_unavailable_or_ambiguous_target_refuses(self):
        for key,value in [('entity_id','fan.other'),('state','unavailable')]:
            original=self.transport.state[key];self.transport.state[key]=value
            with self.assertRaises(FanRefusal):await self.call(self.request())
            self.transport.state[key]=original
        self.assertEqual(self.transport.writes,0)
    async def test_real_tool_and_supported_task_reader(self):
        request=self.request(percentage=50)
        self.telemetry.ordinary_fan_binding=digest(request.model_dump())
        with patch.object(FAN_OPERATIONS,'service',self.service):
            value=json.loads(await control_fan(**request.model_dump()))
            self.assertTrue(value['success'],value)
            self.assertEqual(value['data']['state'],'succeeded_verified')
            read=json.loads(await get_execution_task(request.task_id))
            self.assertEqual(read['data'],value['data'])
        self.assertEqual(self.transport.writes,1)
    async def test_concurrent_exact_duplicate_has_one_owner(self):
        request=self.request(percentage=50)
        self.telemetry.ordinary_fan_binding=digest(request.model_dump())
        values=await asyncio.gather(self.service.control(request),self.service.control(request))
        self.assertEqual(self.transport.writes,1)
        self.assertTrue(all(value['task_id']==request.task_id for value in values));self.settled()
    async def test_separate_service_duplicate_defers_to_original_owner(self):
        second = FanService(self.tmp.name, self.provider, FanCoreAuthority(self.core), now=self.clock)
        self.addAsyncCleanup(second.close)
        request = self.request(percentage=50)
        self.telemetry.ordinary_fan_binding = digest(request.model_dump())
        results = await asyncio.gather(self.service.control(request), second.control(request))
        final = await second.reconcile(request.task_id)
        self.assertTrue(all(item['task_id'] == request.task_id for item in results))
        self.assertEqual(final['state'], 'succeeded_verified')
        self.assertEqual(self.transport.writes, 1)
        self.settled()

    async def test_ownerless_declaration_cancels_only_after_original_id_expires(self):
        request = self.request(percentage=50)
        prepared = await self.service.adapter.prepare(request)
        self.service.save(prepared)
        early = await self.service.reconcile(request.task_id)
        self.assertEqual(early['state'], 'created')
        self.assertEqual(self.transport.writes, 0)
        self.clock.advance(301)
        expired = await self.service.reconcile(request.task_id)
        self.assertEqual(expired['state'], 'cancelled_pre_dispatch')
        repeated = await self.call(request)
        self.assertEqual(repeated, expired)
        self.assertEqual(self.transport.writes, 0)
        self.settled()

    async def test_restart_lock_contends_with_fan(self):
        owner=LockOwner('synthetic-owner','synthetic-restart',None,'restart','synthetic-attempt')
        held=self.service.locks.acquire_once(
            (LockRequest('home_assistant:core',(LockScope.RESOURCE,),LockMode.EXCLUSIVE,('synthetic',)),),
            owner=owner,timing=LockTiming(120,10,0),now=self.clock())
        result=await self.call(self.request())
        self.assertEqual(result['terminal_outcome'],'lock_conflict',result)
        self.assertEqual(self.transport.writes,0)
        self.service.locks.release(held);self.settled()

    async def test_later_duplicate_uses_receipt_without_fresh_id(self):
        request=self.request(percentage=50);await self.call(request)
        self.clock.advance(500)
        result=await self.call(request)
        self.assertEqual(result['state'],'succeeded_verified');self.assertEqual(self.transport.writes,1)
    async def test_changed_state_during_core_await_is_refused(self):
        async def change(tool):
            if tool=='ha_call_service':self.transport.state['last_updated']='external-change'
        self.transport.before=change
        result=await self.call(self.request(percentage=50))
        self.assertEqual(self.transport.writes,0);self.assertEqual(result['state'],'failed_pre_dispatch');self.settled()
    async def test_quantized_readback_never_claims_exact_success(self):
        self.transport.mode='ack_only';request=self.request(percentage=51)
        await self.call(request)
        self.transport.state['state']='on';self.transport.state['attributes']['percentage']=50
        self.clock.advance(10)
        result=await self.service.reconcile(request.task_id)
        self.assertNotEqual(result['state'],'succeeded_verified');self.assertEqual(self.transport.writes,1)
    async def test_cancelled_request_after_intent_finishes_readonly(self):
        entered=asyncio.Event();resume=asyncio.Event()
        async def block(tool):
            if tool=='ha_get_state' and self.transport.writes:
                entered.set();await resume.wait()
        self.transport.before=block;request=self.request(percentage=50)
        self.telemetry.ordinary_fan_binding=digest(request.model_dump())
        pending=asyncio.create_task(self.service.control(request))
        await entered.wait();pending.cancel()
        with self.assertRaises(asyncio.CancelledError):await pending
        self.telemetry.ordinary_fan_binding=None
        resume.set()
        await asyncio.gather(*list(self.service.active.values()))
        self.assertEqual(self.service.receipt(request.task_id)['state'],'succeeded_verified')
        self.assertEqual(self.transport.writes,1);self.settled()
    async def test_owner_process_loss_after_intent_never_redispatches(self):
        entered=asyncio.Event();resume=asyncio.Event()
        async def block(tool):
            if tool=='ha_get_state' and self.transport.writes:
                entered.set();await resume.wait()
        self.transport.before=block;request=self.request(percentage=50)
        self.telemetry.ordinary_fan_binding=digest(request.model_dump())
        pending=asyncio.create_task(self.service.control(request));await entered.wait()
        await self.service.close()
        with self.assertRaises(asyncio.CancelledError):await pending
        self.clock.advance(121);self.transport.before=None
        replacement=FanService(self.tmp.name,self.provider,FanCoreAuthority(self.core),now=self.clock)
        await replacement.recover_once()
        self.assertEqual(replacement.receipt(request.task_id)['state'],'succeeded_verified')
        self.assertEqual(self.transport.writes,1);self.settled()
    async def test_exhausted_observation_retains_hold_and_does_not_redispatch(self):
        self.transport.mode='ack_only';request=self.request(percentage=50)
        await self.call(request);self.clock.advance(181)
        result=await self.service.reconcile(request.task_id)
        self.assertEqual(result['state'],'manual_review_required',result)
        self.assertTrue(any(item.conflict_hold for item in self.service.locks.records()))
        for _ in range(3):await self.service.reconcile(request.task_id)
        self.assertEqual(self.transport.writes,1)
    async def test_minimum_budget_retains_operation_and_dispatch_facts(self):
        request=self.request(percentage=50);await self.call(request)
        self.telemetry.request_id='x'*128
        from ha_mcp_engineering.models.responses import SuccessResponse
        rendered=SuccessResponse(operation='get_execution_task',summary='read',data=self.service.receipt(request.task_id)).to_json(1024)
        self.assertLessEqual(len(rendered.encode()),1024)
        parsed=json.loads(rendered)
        for key in ['task_id','operation_id','operation_hash','state','provider_attempt_count','dispatch_intent_recorded']:
            self.assertEqual(parsed['data'][key],self.service.receipt(request.task_id)[key])
    async def test_existing_source_record_capacity_refuses_new_operations(self):
        with patch('ha_mcp_engineering.fan.service.MAX_RECORDS',0):
            with self.assertRaisesRegex(FanRefusal,'capacity'):await self.call(self.request())
        self.assertEqual(self.transport.writes,0)
    async def test_incomplete_catalog_cannot_dispatch(self):
        self.transport.catalog=replace(self.transport.catalog,catalog_complete=False)
        with self.assertRaises(FanRefusal):await self.call(self.request())
        self.assertEqual(self.transport.writes,0)


class RequestTests(unittest.TestCase):
    def test_closed_arguments_and_strict_types(self):
        valid={'entity_id':'fan.synthetic','action':'turn_on','operation_id':'1789552800-'+'a'*32}
        for changes in [{'entity_id':'fan.*'},{'entity_id':['fan.synthetic']},{'entity_id':'light.synthetic'},
                        {'action':'toggle'},{'percentage':True},{'percentage':'50'},{'percentage':50.5},
                        {'percentage':-1},{'percentage':101},{'ws_command':'x'},{'data':{}},
                        {'action':'turn_off','percentage':1},{'action':'set_percentage'}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                FanRequest.model_validate({**valid,**changes}).checked()


class CoreAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from tests.test_core_update_continuity import CoreContinuityTests
        self.fixture=CoreContinuityTests();await self.fixture.asyncSetUp()
        self.addCleanup(self.fixture.doCleanups)
    async def test_signed_core_plus_separate_compiled_fan_contract(self):
        runtime,_=await self.fixture.runtime()
        guard=FanCoreAuthority(runtime)
        token=await guard.acquire(SimpleNamespace(target=SimpleNamespace(target_type="fan",target_id="fan.synthetic")));self.assertIsNotNone(token)
        commits=guard.consume(token);self.assertIsNotNone(commits)
        self.assertTrue(guard.revalidate(token,commits));guard.finish(commits)
        health=runtime.health_snapshot()
        self.assertEqual(health['issued_lease_count'],0);self.assertEqual(health['active_commit_count'],0)
        self.fixture.assert_admitted(runtime,17)
    async def test_old_compiled_core_keeps_reads_but_no_fan(self):
        runtime,_=await self.fixture.runtime('2026.9.1')
        self.assertIsNone(await FanCoreAuthority(runtime).acquire())
        self.fixture.assert_admitted(runtime,17)
    async def test_retained_revocation_retires_fan_without_fallback(self):
        from core_registry_fixtures import core_revocation
        runtime,_=await self.fixture.runtime();guard=FanCoreAuthority(runtime)
        token=await guard.acquire(SimpleNamespace(target=SimpleNamespace(target_type="fan",target_id="fan.synthetic")));commits=guard.consume(token)
        self.fixture.raw=self.fixture.next_journal(entries=[],revocations=[core_revocation()])
        self.assertTrue(await self.fixture.registry.refresh())
        self.assertFalse(guard.revalidate(token,commits));guard.finish(commits)
        self.assertIsNone(await guard.acquire())
    async def test_missing_signed_positive_authority_cannot_be_manufactured(self):
        self.fixture.raw=self.fixture.signer.journal_raw(entries=[])
        runtime,_=await self.fixture.runtime()
        self.assertIsNone(await FanCoreAuthority(runtime).acquire())


class GatewayFanTests(unittest.TestCase):
    def test_actual_authenticated_gateway_binds_exact_request_and_retires_it(self):
        from tests.test_mcp_inbound_security import settings_for, SECRET
        from tests.same_thread_asgi_client import SameThreadAsgiTestClient
        from ha_mcp_engineering.mcp_server import create_mcp_server
        from ha_mcp_engineering.routing import AuthenticatedMcpGateway
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.request_context import current_telemetry
        with tempfile.TemporaryDirectory() as directory:
            settings=settings_for(directory)
            server=create_mcp_server(settings);server.tool()(control_fan)
            inner=server.streamable_http_app()
            gateway=AuthenticatedMcpGateway(inner,settings,AuditLogger(settings.audit_path,SECRET))
            seen=[]
            class Service:
                async def control(self,request):
                    seen.append((request,current_telemetry()))
                    assert current_telemetry().ordinary_fan_binding==digest(request.model_dump())
                    return {'state':'synthetic-authorized'}
            args={'entity_id':'fan.synthetic','action':'turn_on','operation_id':'1789552800-'+'a'*32}
            with patch.object(FAN_OPERATIONS,'service',Service()), SameThreadAsgiTestClient(
                gateway,lifespan_app=inner,base_url='http://127.0.0.1:8100') as client:
                body={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'control_fan','arguments':args}}
                headers={'accept':'application/json, text/event-stream'}
                response=client.post('/'+SECRET+'/mcp',json=body,headers=headers)
                self.assertEqual(response.status_code,200);self.assertIn('synthetic-authorized',response.text)
                wrong=client.post('/synthetic-wrong/mcp',json=body,headers=headers)
                self.assertEqual(wrong.status_code,404)
                hostile=client.post('/'+SECRET+'/mcp',json=body,headers={**headers,'origin':'https://hostile.invalid'})
                self.assertEqual(hostile.status_code,403)
                extra=deepcopy(body);extra['params']['arguments']['ws_command']='unrelated'
                client.post('/'+SECRET+'/mcp',json=extra,headers=headers)
            self.assertEqual(len(seen),1)
            self.assertIsNone(seen[0][1].ordinary_fan_binding)

class ProviderBoundaryTests(unittest.TestCase):
    def test_compiled_selection_and_retained_denials(self):
        from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
        from tests.test_readonly_upstream_gateway import settings
        gateway=UpstreamReadGateway();gateway.configure(settings())
        token=gateway.fan_provider_authority_token()
        self.assertEqual(len(token),64)
        gateway._readmission_selector=SimpleNamespace(select=lambda **kw:SimpleNamespace(
            authority=SimpleNamespace(decisions=[SimpleNamespace(status=SimpleNamespace(value='deny_only'))])))
        with self.assertRaisesRegex(FanRefusal,'denied'):gateway.fan_provider_authority_token()
    def test_identity_protocol_and_response_refusals(self):
        provider=FanProvider(None,lambda:'test-authority')
        baseline=Transport().catalog
        for changes in [{'server_name':'other'},{'server_version':'8.4.4'},
                        {'protocol_version':'2025-11-25'},{'catalog_complete':False}]:
            with self.subTest(changes=changes),self.assertRaises(FanRefusal):
                provider.validate_catalog(replace(baseline,**changes))
        for envelope in [None,{}, {'isError':True,'structuredContent':{}},
                         {'content':[{'text':'{"success":true}'}]},
                         {'structuredContent':{'payload':'x'*60001}}]:
            with self.subTest(envelope_type=type(envelope)),self.assertRaises(FanRefusal):provider.decode(envelope)
    def test_old_compiled_probe_fingerprints_remain_exact(self):
        from ha_mcp_engineering.ha_core_readmission.probe_profiles import LEGACY_PROBE_PROFILE, CHILD_DEVICE_PROBE_PROFILE
        from ha_mcp_engineering.ha_core_readmission import CORE_CAPABILITY_PROFILES, compiled_exact_authority
        self.assertEqual(len(CORE_CAPABILITY_PROFILES),17)
        self.assertEqual(LEGACY_PROBE_PROFILE.contract_fingerprint,'sha256:017d6f12f0363f71385cece5bd72f9f8ddfc9adfec8787026411fe9933d1115a')
        self.assertEqual(CHILD_DEVICE_PROBE_PROFILE.contract_fingerprint,'sha256:c2706d08f75ecef2a7d37265df6eddb03f95bc97d87a08d8632f861202594f46')
        self.assertEqual(compiled_exact_authority('2026.9.2'),())
