"""Independent review regressions: real fan execution and synthetic providers."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'hass_mcp_engineering_beta')]
from tests.test_typed_fan import Clock, Core, Transport
from ha_mcp_engineering.fan.service import FanService, FAN_OPERATIONS
from ha_mcp_engineering.fan.contracts import FanRequest, digest
from ha_mcp_engineering.fan.authority import FanCoreAuthority
from ha_mcp_engineering.providers.upstream_fan import FanProvider
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.f3.contracts import HA_MCP_PROVIDER_LOCK_KEY, LockRequest, LockScope, LockMode
from ha_mcp_engineering.f3.models import LockOwner, LockTiming
from ha_mcp_engineering.f3.locks import LockConflict


class RecoveryFixture:
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock, self.core, self.transport = Clock(), Core(), Transport()
        self.service = FanService(self.tmp.name, FanProvider(self.transport, lambda: self.transport.authority), FanCoreAuthority(self.core), now=self.clock)
        self.addAsyncCleanup(self.service.close)
        self.telemetry, token = begin_request('independent-synthetic-request')
        self.addCleanup(end_request, token)

    def request(self, entity='fan.synthetic', percentage=50):
        return FanRequest(entity_id=entity, action='turn_on', percentage=percentage,
                          operation_id=f'{int(self.clock().timestamp())}-{uuid.uuid4().hex}')

    async def call(self, request):
        self.telemetry.ordinary_fan_binding = digest(request.model_dump())
        return await self.service.control(request)

    async def unresolved(self):
        self.transport.mode = 'ack_only'
        request = self.request()
        result = await self.call(request)
        self.assertEqual(self.transport.writes, 1)
        self.assertFalse(result['terminal'])
        self.clock.advance(181)
        result = await self.service.reconcile(request.task_id)
        self.assertEqual(result['state'], 'manual_review_required')
        return request


class RecoveryProbes(RecoveryFixture, unittest.IsolatedAsyncioTestCase):
    async def test_unresolved_fan_retains_only_its_target(self):
        request = await self.unresolved()
        holds = self.service.locks.records()
        self.assertEqual({x.key for x in holds}, {'entity:' + request.entity_id})

    async def test_other_fan_still_runs_after_unresolved_target(self):
        await self.unresolved()
        self.transport.mode = 'success'
        self.transport.state['entity_id'] = 'fan.independent'
        # Extend only the synthetic network target; all candidate lock/executor
        # code is unchanged and uses the new request's exact target.
        self.transport.assert_fixed = lambda args: self.assertEqual(args['entity_id'], 'fan.independent')
        result = await self.call(self.request('fan.independent'))
        self.assertEqual(result['state'], 'succeeded_verified')

    async def test_shared_dependencies_remain_available(self):
        await self.unresolved()
        self.clock.advance(86400)
        blocked = []
        for mode in (LockMode.SHARED, LockMode.EXCLUSIVE):
            for key, scope in [('home_assistant:core', LockScope.RESOURCE), (HA_MCP_PROVIDER_LOCK_KEY, LockScope.PROVIDER)]:
                try:
                    handle = self.service.locks.acquire_once(
                        (LockRequest(key, (scope,), mode, ('independent_operation',)),),
                        owner=LockOwner('other-owner', 'other-task', None, 'other-operation', 'other-attempt'),
                        timing=LockTiming(120, 10, 0), now=self.clock())
                except LockConflict:
                    blocked.append(key)
                else:
                    self.service.locks.release(handle)
        self.assertEqual(blocked, [])

    async def test_same_target_remains_protected_without_redispatch(self):
        request = await self.unresolved()
        for _ in range(3):
            self.assertEqual((await self.service.reconcile(request.task_id))['state'], 'manual_review_required')
        self.assertEqual(self.transport.writes, 1)
        self.assertTrue(any(x.key == 'entity:fan.synthetic' and x.conflict_hold for x in self.service.locks.records()))

    async def test_cancel_before_intent_never_dispatches(self):
        entered, resume = asyncio.Event(), asyncio.Event()
        async def block(tool):
            if tool == 'ha_call_service':
                entered.set()
                await resume.wait()
        self.transport.before = block
        pending = asyncio.create_task(self.call(self.request()))
        await entered.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.telemetry.ordinary_fan_binding = None
        resume.set()
        await asyncio.gather(*list(self.service.active.values()))
        self.assertEqual(self.transport.writes, 0)
        self.assertEqual(self.service.locks.records(), ())
        self.assertFalse(self.core.leases or self.core.commits)


class AuditProbe(unittest.TestCase):
    def test_real_gateway_retains_selected_provider_attribution(self):
        from tests.test_mcp_inbound_security import settings_for, SECRET
        from tests.same_thread_asgi_client import SameThreadAsgiTestClient
        from ha_mcp_engineering.mcp_server import create_mcp_server
        from ha_mcp_engineering.routing import AuthenticatedMcpGateway
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.tools.fan import control_fan
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            clock, core, transport = Clock(), Core(), Transport()
            audit = AuditLogger(settings.audit_path, SECRET)
            service = FanService(directory, FanProvider(transport, lambda: transport.authority), FanCoreAuthority(core), now=clock, audit=audit)
            server = create_mcp_server(settings)
            server.tool()(control_fan)
            inner = server.streamable_http_app()
            gateway = AuthenticatedMcpGateway(inner, settings, audit)
            args = dict(entity_id='fan.synthetic', action='turn_on', percentage=50,
                        operation_id=f'{int(clock().timestamp())}-{uuid.uuid4().hex}')
            with patch.object(FAN_OPERATIONS, 'service', service), SameThreadAsgiTestClient(gateway, lifespan_app=inner, base_url='http://127.0.0.1:8100') as client:
                response = client.post('/'+SECRET+'/mcp', json={'jsonrpc':'2.0', 'id':1, 'method':'tools/call', 'params':{'name':'control_fan','arguments':args}}, headers={'accept':'application/json, text/event-stream'})
                self.assertEqual(response.status_code, 200)
                self.assertIn('succeeded_verified', response.text)
                core.allowed = False
                refused_args = {**args, 'operation_id': f'{int(clock().timestamp())}-{uuid.uuid4().hex}'}
                refused = client.post('/'+SECRET+'/mcp', json={'jsonrpc':'2.0', 'id':2, 'method':'tools/call', 'params':{'name':'control_fan','arguments':refused_args}}, headers={'accept':'application/json, text/event-stream'})
                self.assertEqual(refused.status_code, 200)
                self.assertIn('fan_core_authority_unavailable', refused.text)
            self.assertEqual(transport.writes, 1)
            entries = [json.loads(line) for line in Path(settings.audit_path).read_text().splitlines()]
            rows = [x for x in entries if x.get('tool_name') == 'control_fan']
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]['analysis_summary']['fan_outcome'], 'request_failed_reconcile_task')
            self.assertEqual(rows[1]['analysis_summary']['provider'], 'upstream_typed_fan')
            self.assertEqual(rows[1]['analysis_summary']['operation_id'], refused_args['operation_id'])
            self.assertIn('upstream_typed_fan', json.dumps(rows[0]))
            self.assertEqual(rows[0]['analysis_summary']['fan_outcome'], 'succeeded_verified')
            self.assertEqual(rows[0]['analysis_summary']['provider_attempt_count'], 1)
            self.assertTrue(any(r.get('state') == 'succeeded_verified' for r in entries))



class HoldRecoveryTests(RecoveryFixture, unittest.IsolatedAsyncioTestCase):
    async def test_reconstruction_preserves_target_and_generation(self):
        request = await self.unresolved()
        before = self.service.locks.records()
        rebuilt = FanService(self.tmp.name, self.service.provider, self.service.core, now=self.clock)
        self.addAsyncCleanup(rebuilt.close)
        self.clock.advance(86400)
        await rebuilt.recover_once()
        result = await rebuilt.reconcile(request.task_id)
        self.assertEqual(rebuilt.locks.records(), before)
        self.assertEqual(result['retained_locks'], [dict(key=before[0].key, generation=before[0].generation,
                                                       mode=before[0].mode, conflict_hold=True)])
        self.assertEqual(rebuilt.health()['conflict_hold_count'], 1)
        self.assertEqual(rebuilt.health()['conflict_hold_task_count'], 1)
        self.assertEqual(self.transport.writes, 1)

    async def test_failed_atomic_promotion_preserves_keys_then_reconciles(self):
        from ha_mcp_engineering.f3.locks import LockStorageError
        self.transport.mode = 'ack_only'
        request = self.request()
        await self.call(request)
        self.clock.advance(181)
        before = self.service.locks.records()
        promotion_entered = False
        def fail(stage):
            nonlocal promotion_entered
            if stage == 'during_selective_hold_promotion':
                promotion_entered = True
            if promotion_entered and stage == 'before_state_replace':
                raise LockStorageError('synthetic storage fault')
        self.service.locks._fault_hook = fail
        result = await self.service.reconcile(request.task_id)
        self.assertEqual(result['state'], 'manual_review_required')
        self.assertEqual({x.key for x in self.service.locks.records()}, {x.key for x in before})
        self.assertFalse(any(x.conflict_hold for x in self.service.locks.records()))
        self.service.locks._fault_hook = None
        await self.service.recover_once()
        self.assertEqual({x.key for x in self.service.locks.records()}, {'entity:fan.synthetic'})
        self.assertTrue(self.service.locks.records()[0].conflict_hold)
        self.assertEqual(self.transport.writes, 1)
        self.assertFalse(self.core.leases or self.core.commits)

    async def test_failed_promotion_rejects_changed_owner_without_clearing(self):
        from ha_mcp_engineering.f3.locks import LockOwnershipError
        request = await self.unresolved()
        record = self.service.executions.get(request.task_id)
        record.identity['owner_id'] = 'synthetic-other-owner'
        before = self.service.locks.records()
        with self.assertRaises(LockOwnershipError):
            self.service.locks.reconcile_terminal_hold(record)
        self.assertEqual(self.service.locks.records(), before)
        self.assertEqual(self.transport.writes, 1)

    async def test_settled_hold_does_not_consume_background_budget(self):
        await self.unresolved()
        with patch.object(self.service, 'reconcile') as reconcile:
            await self.service.recover_once()
        reconcile.assert_not_called()

    async def test_new_operation_on_same_unresolved_target_cannot_dispatch(self):
        await self.unresolved()
        result = await self.call(self.request())
        self.assertEqual(result['state'], 'failed_pre_dispatch')
        self.assertEqual(result['provider_attempt_count'], 0)
        self.assertEqual(self.transport.writes, 1)

    async def test_dependencies_remain_locked_during_active_dispatch(self):
        entered, resume = asyncio.Event(), asyncio.Event()
        async def block(tool):
            if tool == 'ha_call_service':
                entered.set()
                await resume.wait()
        self.transport.before = block
        pending = asyncio.create_task(self.call(self.request()))
        await entered.wait()
        try:
            keys = {x.key for x in self.service.locks.records()}
            self.assertEqual(keys, {'entity:fan.synthetic', 'home_assistant:core', HA_MCP_PROVIDER_LOCK_KEY})
            for key in ('home_assistant:core', HA_MCP_PROVIDER_LOCK_KEY):
                with self.assertRaises(LockConflict):
                    self.service.locks.acquire_once(
                        (LockRequest(key, (LockScope.RESOURCE,), LockMode.EXCLUSIVE, ('restart',)),),
                        owner=LockOwner('other', 'other-task', None, 'restart', 'other-attempt'),
                        timing=LockTiming(120, 10, 0), now=self.clock())
        finally:
            resume.set()
            await pending
        self.assertEqual(self.service.locks.records(), ())


class LifecycleAuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from ha_mcp_engineering.audit import AuditLogger
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.audit_path = Path(self.tmp.name) / 'audit.jsonl'
        self.audit = AuditLogger(str(self.audit_path), 'synthetic-secret-never-export')
        self.clock, self.core, self.transport = Clock(), Core(), Transport()
        self.provider = FanProvider(self.transport, lambda: self.transport.authority)
        self.service = FanService(self.tmp.name, self.provider, FanCoreAuthority(self.core), now=self.clock, audit=self.audit)
        self.addAsyncCleanup(self.service.close)
        self.telemetry, self.token = begin_request('synthetic-original-request')
        self.addCleanup(end_request, self.token)

    def request(self):
        return FanRequest(entity_id='fan.synthetic', action='turn_on', percentage=50,
                          operation_id=f'{int(self.clock().timestamp())}-{uuid.uuid4().hex}')

    async def call(self, request):
        self.telemetry.ordinary_fan_binding = digest(request.model_dump())
        return await self.service.control(request)

    def entries(self):
        return [json.loads(line) for line in self.audit_path.read_text().splitlines()]

    def assert_safe(self, request):
        rows = self.entries()
        self.assertEqual(len(rows), len({r['audit_event_id'] for r in rows}))
        for row in rows:
            self.assertEqual(row['task_id'], request.task_id)
            self.assertEqual(row['operation_id'], request.operation_id)
            self.assertEqual(row['request_id'], 'synthetic-original-request')
            self.assertEqual(row['provider'], 'upstream_typed_fan')
            self.assertEqual(row['fallback'], 'none')
            self.assertNotIn('plan_id', row)
            self.assertNotIn('approval', row['event'])
            self.assertNotIn('synthetic-secret-never-export', json.dumps(row))
            self.assertNotIn('synthetic secret must never escape', json.dumps(row))
            self.assertLess(len(json.dumps(row)), 2048)
        self.assertEqual(self.service.health()['audit_projection_failures'], 0)
        return rows

    async def test_success_replay_and_reconstruction_are_idempotent(self):
        request = self.request()
        await self.call(request)
        before = self.entries()
        rebuilt = FanService(self.tmp.name, self.provider, self.service.core, now=self.clock, audit=self.audit)
        self.addAsyncCleanup(rebuilt.close)
        for _ in range(3):
            await rebuilt.reconcile(request.task_id)
        self.assertEqual(self.entries(), before)
        rows = self.assert_safe(request)
        final = [r for r in rows if r['event'] == 'fan_execution_snapshot'][-1]
        self.assertEqual(final['state'], 'succeeded_verified')
        self.assertTrue(final['verified'])
        self.assertTrue(final['dispatch_intent_recorded'])
        self.assertIsNone(final['dispatched_at'])
        self.assertEqual(final['provider_attempt_count'], 1)
        self.assertEqual(self.transport.writes, 1)

    async def test_ambiguous_response_has_possible_dispatch_and_verification(self):
        self.transport.mode = 'timeout'
        request = self.request()
        await self.call(request)
        rows = self.assert_safe(request)
        uncertain = [r for r in rows if r.get('outcome') == 'dispatch_indeterminate']
        self.assertTrue(uncertain)
        self.assertFalse(uncertain[0]['provider_response_received'])
        self.assertIsNone(uncertain[0]['verified'])
        self.assertIsNone(uncertain[0]['dispatched_at'])
        self.assertEqual(rows[-1]['state'], 'succeeded_verified')
        self.assertEqual(self.transport.writes, 1)

    async def test_background_recovery_after_request_retirement_keeps_correlation(self):
        self.transport.mode = 'ack_only'
        request = self.request()
        result = await self.call(request)
        self.assertEqual(result['state'], 'observing')
        self.telemetry.ordinary_fan_binding = None
        self.transport.state['state'] = 'on'
        self.transport.state['attributes']['percentage'] = 50
        self.clock.advance(121)  # Existing lock lease must expire before owner transfer.
        # A fresh context has no ordinary authorization and a different request.
        _, token = begin_request('synthetic-unrelated-background')
        try:
            rebuilt = FanService(self.tmp.name, self.provider, self.service.core, now=self.clock, audit=self.audit)
            self.addAsyncCleanup(rebuilt.close)
            await rebuilt.recover_once()
            self.assertEqual(rebuilt.receipt(request.task_id)['state'], 'succeeded_verified')
            before = self.entries()
            await rebuilt.recover_once()
            await rebuilt.reconcile(request.task_id)
            self.assertEqual(self.entries(), before)
        finally:
            end_request(token)
        rows = self.assert_safe(request)
        self.assertTrue(any(r['event'] == 'fan_recovery_claimed' and r['read_only_recovery'] for r in rows))
        self.assertEqual(rows[-1]['state'], 'succeeded_verified')
        self.assertTrue(rows[-1]['read_only_recovery'])
        self.assertEqual(self.transport.writes, 1)
        self.assertFalse(self.core.leases or self.core.commits)
        self.assertEqual(rebuilt.locks.records(), ())

    async def test_predispatch_refusal_is_not_delivery_or_approval(self):
        async def change(tool):
            if tool == 'ha_call_service':
                self.transport.state['last_updated'] = 'synthetic-external-change'
        self.transport.before = change
        request = self.request()
        result = await self.call(request)
        self.assertEqual(result['state'], 'failed_pre_dispatch')
        rows = self.assert_safe(request)
        self.assertEqual(rows[-1]['state'], 'failed_pre_dispatch')
        self.assertEqual(rows[-1]['provider_attempt_count'], 0)
        self.assertFalse(rows[-1]['dispatch_intent_recorded'])
        self.assertIsNone(rows[-1]['verified'])
        self.assertEqual(self.transport.writes, 0)

    async def test_audit_failure_does_not_redispatch_and_receipt_replays(self):
        request = self.request()
        with patch.object(self.audit, 'write_batch', return_value=0):
            result = await self.call(request)
        self.assertEqual(result['state'], 'succeeded_verified')
        self.assertGreater(self.service.health()['audit_projection_failures'], 0)
        await self.service.reconcile(request.task_id)
        before = self.entries()
        await self.service.reconcile(request.task_id)
        self.assertEqual(self.entries(), before)
        self.assertEqual(before[-1]['state'], 'succeeded_verified')
        self.assertEqual(self.transport.writes, 1)

    async def test_cancellation_before_intent_is_audited_without_delivery(self):
        entered, resume = asyncio.Event(), asyncio.Event()
        async def block(tool):
            if tool == 'ha_call_service':
                entered.set()
                await resume.wait()
        self.transport.before = block
        request = self.request()
        pending = asyncio.create_task(self.call(request))
        await entered.wait()
        await self.service.close()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(self.entries()[-1]['state'], 'preflight')
        self.assertFalse(self.entries()[-1]['dispatch_intent_recorded'])
        # Shutdown preserves the unfinished record; owner-loss recovery settles it.
        self.clock.advance(121)
        await self.service.recover_once()
        rows = self.assert_safe(request)
        self.assertEqual(rows[-1]['state'], 'cancelled_pre_dispatch')
        self.assertFalse(rows[-1]['dispatch_intent_recorded'])
        self.assertEqual(rows[-1]['provider_attempt_count'], 0)
        self.assertEqual(self.transport.writes, 0)
        self.assertEqual(self.service.locks.records(), ())
        self.assertFalse(self.core.leases or self.core.commits)

    async def test_manual_review_is_recorded(self):
        self.transport.mode = 'ack_only'
        request = self.request()
        await self.call(request)
        self.clock.advance(181)
        await self.service.reconcile(request.task_id)
        rows = self.assert_safe(request)
        self.assertEqual(rows[-1]['state'], 'manual_review_required')
        self.assertIsNone(rows[-1]['verified'])
        self.assertEqual(self.transport.writes, 1)

class AuditProjectionTests(unittest.TestCase):
    def test_gateway_projection_refuses_unbounded_or_noncontract_context(self):
        from ha_mcp_engineering.fan.audit import request_summary
        from ha_mcp_engineering.request_context import RequestTelemetry
        telemetry = RequestTelemetry('synthetic-request')
        telemetry.audit_context.update(provider='untrusted', fallback='untrusted', raw='secret',
                                       operation_id='x'*5000, task_id='not-a-task',
                                       fan_outcome='arbitrary provider text', provider_attempt_count=True)
        self.assertEqual(request_summary(telemetry), {
            'operation_class': 'typed_fan', 'provider': 'upstream_typed_fan', 'fallback': 'none'})

    def test_runtime_configuration_connects_existing_audit_logger(self):
        from ha_mcp_engineering.fan.service import FanRuntime
        from tests.test_mcp_inbound_security import settings_for, SECRET
        from ha_mcp_engineering.audit import AuditLogger
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory)
            audit = AuditLogger(settings.audit_path, SECRET)
            runtime = FanRuntime()
            with patch.object(FanProvider, 'configured', return_value=None):
                runtime.configure(settings, Core(), None, audit=audit)
            self.assertIs(runtime.require().executions.audit, audit)
