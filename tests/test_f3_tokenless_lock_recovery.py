"""Synthetic cardinality, acquisition-loss and exact orphan recovery proofs."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'hass_mcp_engineering_beta')]

from ha_mcp_engineering.f3.contracts import LockMode, LockRequest, LockScope
from ha_mcp_engineering.f3.executor import SharedOperationExecutor, SimulatedProcessLoss
from ha_mcp_engineering.f3.locks import DurableLockStore, LockStorageError, LockOwnershipError
from ha_mcp_engineering.f3.models import ExecutionIdentity, ExecutorTiming, LockTiming, LockOwner, MAX_LOCK_TOKENS
from ha_mcp_engineering.f3.persistence import DurableExecutionRepository, ExecutionStorageError
from ha_mcp_engineering.f3_configuration.sequence import prepare_configuration_sequence
from ha_mcp_engineering.f3_configuration.locks import lock_set_hash
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration, _SequenceLockAdapter
from tests.f3_synthetic_adapter import SyntheticOperationAdapter, SyntheticApprovalRecorder, prepared_dashboard_operation
from tests.f3_configuration_fixtures import SyntheticConfigurationGateway, proposal_for, adapter_for, valid_config
from tests.test_dev14_configuration_plans import ConfigurationPlanTestCase
from tests.test_f3_runtime_integration import _ExactFakeConfigurationGateway, _provider_identity

NOW = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
LOCK_TIMING = LockTiming(60, 10, 0)
EXECUTION_TIMING = ExecutorTiming(120, 60, 3, 3)


def identity(suffix='primary'):
    return ExecutionIdentity(f'task-{suffix}', f'plan-{suffix}', f'attempt-{suffix}',
                             f'request-{suffix}', f'owner-{suffix}')


def requests(count):
    return tuple(LockRequest(f'synthetic:resource_{i:03d}', (LockScope.RESOURCE,),
                             LockMode.EXCLUSIVE, ('synthetic_dependency',)) for i in range(count))


class SizedAdapter(SyntheticOperationAdapter):
    def __init__(self, count):
        super().__init__()
        self.count = count

    def lock_requests(self, operation):
        return requests(self.count)


class AcquisitionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def components(self, path, *, hook=None):
        locks = DurableLockStore(path)
        repo = DurableExecutionRepository(path)
        executor = SharedOperationExecutor(lock_store=locks, execution_repository=repo,
            lock_timing=LOCK_TIMING, executor_timing=EXECUTION_TIMING,
            now=lambda: NOW, fault_hook=hook)
        return locks, repo, executor

    async def test_supported_cardinality_executes_once_and_verifies(self):
        for count in (16, 17, 18, MAX_LOCK_TOKENS):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as path:
                locks, repo, executor = self.components(path)
                adapter, approval = SizedAdapter(count), SyntheticApprovalRecorder()
                result = await executor.execute(adapter=adapter, prepared=prepared_dashboard_operation(),
                    identity=identity(), approval_consumption=approval)
                self.assertEqual('succeeded_verified', result.outcome)
                self.assertEqual(count, len(repo.get(identity().task_id).lock_tokens))
                self.assertEqual(1, adapter.counters.dispatch_invocations)
                self.assertEqual(1, approval.consumptions)
                self.assertEqual((), locks.records())
                duplicate = await executor.execute(adapter=adapter, prepared=prepared_dashboard_operation(),
                    identity=identity(), approval_consumption=approval)
                self.assertTrue(duplicate.duplicate_execution)
                self.assertEqual(1, adapter.counters.dispatch_invocations)

    async def test_limit_refusal_precedes_acquisition_and_provider(self):
        with tempfile.TemporaryDirectory() as path:
            locks, repo, executor = self.components(path)
            adapter, approval = SizedAdapter(MAX_LOCK_TOKENS + 1), SyntheticApprovalRecorder()
            with patch.object(locks, 'acquire', side_effect=AssertionError('must not acquire')):
                result = await executor.execute(adapter=adapter, prepared=prepared_dashboard_operation(),
                    identity=identity(), approval_consumption=approval)
            self.assertEqual('failed_pre_dispatch', result.outcome)
            self.assertEqual(0, adapter.counters.preflight_invocations)
            self.assertEqual(0, adapter.counters.dispatch_invocations)
            self.assertEqual(0, approval.invocations)
            self.assertEqual((), locks.records())

    async def test_token_validation_or_io_failure_releases_exact_handle(self):
        for error in (ValueError('synthetic validation'), ExecutionStorageError('synthetic storage')):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as path:
                locks, repo, executor = self.components(path)
                adapter, approval = SizedAdapter(18), SyntheticApprovalRecorder()
                with patch.object(repo, 'record_locks', side_effect=error):
                    result = await executor.execute(adapter=adapter, prepared=prepared_dashboard_operation(),
                        identity=identity(), approval_consumption=approval)
                self.assertEqual('failed_pre_dispatch', result.outcome)
                self.assertIn('lock_cleanup_completed', result.diagnostic_codes)
                self.assertIn('lock_storage_failure', result.diagnostic_codes)
                self.assertEqual((), locks.records())
                self.assertEqual(0, adapter.counters.preflight_invocations)
                self.assertEqual(0, approval.invocations)
                self.assertEqual('failed_pre_dispatch', repo.get(identity().task_id).normalized_outcome)

    async def test_cleanup_failure_is_retained_and_reported(self):
        with tempfile.TemporaryDirectory() as path:
            locks, repo, executor = self.components(path)
            adapter, approval = SizedAdapter(18), SyntheticApprovalRecorder()
            with patch.object(repo, 'record_locks', side_effect=ExecutionStorageError('synthetic')):
                with patch.object(locks, 'release', side_effect=LockStorageError('synthetic')):
                    result = await executor.execute(adapter=adapter, prepared=prepared_dashboard_operation(),
                        identity=identity(), approval_consumption=approval)
            self.assertIn('lock_cleanup_failed', result.diagnostic_codes)
            self.assertEqual(18, len(locks.records()))
            record = repo.get(identity().task_id)
            self.assertTrue(any('lock_cleanup_failed' in event['diagnostic_codes'] for event in record.events))
            self.assertEqual(0, record.dispatch_count)
            self.assertEqual(0, approval.invocations)

    async def test_terminalization_failure_still_attempts_cleanup(self):
        with tempfile.TemporaryDirectory() as path:
            locks, repo, executor = self.components(path)
            original_write = repo._write_unlocked
            def failing_write(record):
                if record.terminal:
                    raise ExecutionStorageError('synthetic terminal write')
                return original_write(record)
            with patch.object(repo, 'record_locks', side_effect=ValueError('synthetic')):
                with patch.object(repo, '_write_unlocked', side_effect=failing_write):
                    with self.assertRaises(ExecutionStorageError):
                        await executor.execute(adapter=SizedAdapter(18), prepared=prepared_dashboard_operation(),
                            identity=identity(), approval_consumption=SyntheticApprovalRecorder())
            self.assertEqual((), locks.records())
            self.assertEqual(0, repo.get(identity().task_id).dispatch_count)

    async def test_cleanup_cannot_follow_a_lost_claim_or_durable_intent(self):
        from ha_mcp_engineering.f3.persistence import BlindRedispatchProhibited, ExecutionClaimLost
        with tempfile.TemporaryDirectory() as path:
            locks, repo, executor = self.components(path)
            prepared = prepared_dashboard_operation()
            claim = repo.claim(identity=identity(), prepared=prepared, timing=EXECUTION_TIMING, now=NOW)
            cleanup = unittest.mock.Mock()
            with self.assertRaises(ExecutionClaimLost):
                repo.fail_lock_acquisition(identity().task_id, owner_id='other',
                    claim_generation=claim.claim_generation, cleanup=cleanup, now=NOW)
            cleanup.assert_not_called()
        with tempfile.TemporaryDirectory() as path:
            locks, repo, executor = self.components(path)
            await executor.execute(adapter=SizedAdapter(3), prepared=prepared_dashboard_operation(),
                                   identity=identity(), approval_consumption=SyntheticApprovalRecorder())
            record = repo.get(identity().task_id)
            with self.assertRaises(BlindRedispatchProhibited):
                repo.fail_lock_acquisition(identity().task_id, owner_id=identity().owner_id,
                    claim_generation=record.claim_generation, cleanup=cleanup, now=NOW)
            cleanup.assert_not_called()
            self.assertEqual(1, repo.get(identity().task_id).dispatch_count)

    async def test_new_process_loss_boundary_retains_actual_old_shape(self):
        def loss(stage):
            if stage == 'after_lock_acquisition_before_token_persistence':
                raise SimulatedProcessLoss()
        with tempfile.TemporaryDirectory() as path:
            locks, repo, executor = self.components(path, hook=loss)
            adapter, approval = SizedAdapter(18), SyntheticApprovalRecorder()
            with self.assertRaises(SimulatedProcessLoss):
                await executor.execute(adapter=adapter, prepared=prepared_dashboard_operation(),
                    identity=identity(), approval_consumption=approval)
            restarted = DurableExecutionRepository(path)
            self.assertEqual([], restarted.get(identity().task_id).lock_tokens)
            self.assertEqual(18, len(DurableLockStore(path).records()))
            self.assertEqual(0, adapter.counters.preflight_invocations)
            self.assertEqual(0, approval.invocations)

    async def test_real_eight_operation_union_has_eighteen_locks_and_executes(self):
        operations, adapters = [], []
        for i, family in enumerate(['input_boolean'] * 4 + ['input_number'] * 2 + ['automation'] * 2):
            config = valid_config(family)
            if family.startswith('input_'):
                config['name'] = f'Synthetic {i}'
            target = f'{family}.synthetic_{i}' if family.startswith('input_') else f'synthetic_automation_{i}'
            if family == 'automation':
                config['trigger'] = [{'platform': 'state', 'entity_id': [
                    *(f'input_boolean.synthetic_{j}' for j in range(4)), 'input_boolean.synthetic_guard']}]
                config['condition'] = [{'condition': 'template', 'value_template': '{{ states(dynamic_entity) }}'}]
            proposal = replace(proposal_for(family, 'create', operation_id=f'step_{i}', order=i,
                                            proposed_config=config), target_id=target)
            adapter = adapter_for(family, 'create', SyntheticConfigurationGateway())
            operations.append(await adapter.prepare(proposal))
            adapters.append(adapter)
        sequence = prepare_configuration_sequence(operations)
        self.assertEqual(18, len(sequence.lock_requests))
        self.assertIn('helper_dependency:input_boolean_dynamic', [x.key for x in sequence.lock_requests])
        with tempfile.TemporaryDirectory() as path:
            locks, repo, executor = self.components(path)
            for i, (operation, adapter) in enumerate(zip(operations, adapters)):
                result = await executor.execute(adapter=_SequenceLockAdapter(adapter, sequence.lock_requests),
                    prepared=operation, identity=replace(identity(str(i)), plan_id=operation.plan_id),
                    approval_consumption=SyntheticApprovalRecorder())
                self.assertEqual('succeeded_verified', result.outcome)
                self.assertEqual(1, result.dispatch_count)
                self.assertEqual((), locks.records())


class TokenlessRuntimeTests(ConfigurationPlanTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.instant = self.service.now()
        self.service.now = lambda: self.instant
        self.runtime = self.new_runtime()
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once('startup')

    def new_runtime(self):
        return F3RuntimeIntegration(service=self.service, storage_root=str(self.root / 'plans'),
            configuration_gateway=_ExactFakeConfigurationGateway(self.gateway), backup_gateway=None,
            lifecycle_gateway=None, provider_identity_reader=_provider_identity, retention_days=90)

    async def tokenless(self, *, terminal=True):
        created = await self.create_automation_plan()
        await self.approve(created)
        plan = self.service._load(created['plan_id'])
        task, prepared, complete = await self.runtime._initialize(plan, created['plan_hash'])
        task = self.runtime._enter_public_preflight(task)
        declaration = self.runtime.children.declarations_for_task(task.task_id)[0]
        ident = ExecutionIdentity(declaration['child_id'], declaration['plan_id'], declaration['attempt_id'],
                                  declaration['request_id'], 'synthetic-orphan-owner')
        claim = self.runtime.children.claim(identity=ident, prepared=prepared[0],
                                           timing=EXECUTION_TIMING, now=self.instant)
        owner = LockOwner(ident.owner_id, ident.task_id, ident.plan_id, prepared[0].operation, ident.attempt_id)
        handle = self.runtime.locks.acquire_once(complete, owner=owner, timing=LOCK_TIMING, now=self.instant)
        if terminal:
            self.runtime.children.terminalize_pre_dispatch(ident.task_id, owner_id=ident.owner_id,
                claim_generation=claim.claim_generation, outcome='failed_pre_dispatch',
                diagnostic_codes=('lock_storage_failure',), now=self.instant)
            self.runtime._project(plan, task)
        self.instant += timedelta(seconds=121)
        return created, task, declaration, handle, claim

    async def test_exact_tokenless_terminal_recovery_and_restart_are_idempotent(self):
        created, task, declaration, handle, _ = await self.tokenless()
        self.assertEqual([], self.runtime.children.get(declaration['child_id']).lock_tokens)
        before = list(self.gateway.calls)
        self.runtime = self.new_runtime()
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once('startup')
        self.assertEqual((), self.runtime.locks.records())
        record = self.runtime.children.get(declaration['child_id'])
        self.assertEqual(len(handle.tokens), len(record.lock_tokens))
        self.assertEqual('failed_pre_dispatch', record.normalized_outcome)
        self.assertEqual(0, record.dispatch_count)
        self.assertEqual(before, self.gateway.calls)
        self.assertEqual([], self.runtime.reconciliation_items())
        await self.runtime.recover_once('periodic')
        self.assertEqual(0, self.runtime.health()['recovery_failures'])
        self.assertEqual(before, self.gateway.calls)

    async def test_exact_beta5_writer_fixture_recovers_without_rewriting_history(self):
        import hashlib
        import json
        import shutil
        fixture = ROOT / 'tests/fixtures/f3_lock_recovery/beta5'
        provenance = json.loads((fixture / 'provenance.json').read_text())
        self.assertEqual('34f96a90548f72d36f6f5a9a428920a3a7d14de9', provenance['source'])
        for relative, expected in provenance['files'].items():
            path = fixture / relative
            self.assertEqual(expected, hashlib.sha256(path.read_bytes()).hexdigest())
            target = self.root / 'plans' / path.relative_to(fixture / 'store')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        self.service.task_repository.rebuild_navigation_index()
        self.instant = datetime.fromisoformat(provenance['recover_at'])
        self.runtime = self.new_runtime()
        self.service.f3_runtime = self.runtime
        before = self.runtime.children.get(provenance['child_id'])
        self.assertEqual([], before.lock_tokens)
        self.assertEqual(provenance['retained_locks'], len(self.runtime.locks.records()))
        calls = list(self.gateway.calls)
        await self.runtime.recover_once('startup')
        after = self.runtime.children.get(provenance['child_id'])
        self.assertEqual('failed_pre_dispatch', after.normalized_outcome)
        self.assertEqual(before.events, after.events[:len(before.events)])
        self.assertEqual(before.prepared_operation_hash, after.prepared_operation_hash)
        self.assertEqual(0, after.dispatch_count)
        self.assertEqual(calls, self.gateway.calls)
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual('failed_pre_dispatch', self.service.get_execution_task(provenance['public_task_id'])['state'])
        self.assertFalse(self.service._public(self.service._load(provenance['plan_id']), include_configs=False)['apply_allowed'])
        await self.runtime.recover_once('periodic')
        self.assertEqual([], self.runtime.reconciliation_items())
        self.assertEqual(0, self.runtime.health()['recovery_failures'])

    async def test_binding_write_failure_retains_locks_and_reports_storage_retry(self):
        _, _, declaration, _, _ = await self.tokenless()
        retained = self.runtime.locks.records()
        with patch.object(self.runtime.children, '_write_unlocked', side_effect=ExecutionStorageError('synthetic private detail')):
            await self.runtime.recover_once('periodic')
        self.assertEqual(retained, self.runtime.locks.records())
        self.assertEqual([], self.runtime.children.get(declaration['child_id']).lock_tokens)
        diagnostics = self.runtime.reconciliation_items()[0]['lock_recovery']
        self.assertEqual('storage_error', diagnostics['last_failure_category'])
        self.assertNotIn('synthetic private detail', str(diagnostics))

    async def test_replacement_claim_racing_orphan_cancellation_is_preserved(self):
        from tests.test_f3_orphan_child_recovery import _PreparedStub
        _, _, declaration, _, claim = await self.tokenless(terminal=False)
        retained = self.runtime.locks.records()
        original = self.runtime.children.cancel
        def raced_cancel(*args, **kwargs):
            self.runtime.children.claim(
                identity=replace(claim.record.execution_identity(), owner_id='replacement'),
                prepared=_PreparedStub(declaration), timing=EXECUTION_TIMING, now=self.instant)
            return original(*args, **kwargs)
        with patch.object(self.runtime.children, 'cancel', side_effect=raced_cancel):
            self.assertEqual((False, False), self.runtime._reconcile_orphaned_child(declaration, now=self.instant))
        current = self.runtime.children.get(declaration['child_id'])
        self.assertEqual('replacement', current.identity['owner_id'])
        self.assertFalse(current.terminal)
        self.assertEqual(retained, self.runtime.locks.records())

    async def test_real_runtime_recording_failure_cleans_up_without_audit_reentry(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        with patch.object(self.runtime.children, 'record_locks', side_effect=ExecutionStorageError('synthetic private failure')):
            result = await self.service.apply(created['plan_id'], created['plan_hash'])
        self.assertEqual('failed_pre_dispatch', result['task_state'])
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual(0, sum(call[0] == 'write' for call in self.gateway.calls))
        declaration = self.runtime.children.declarations_for_task(result['task_id'])[0]
        record = self.runtime.children.get(declaration['child_id'])
        self.assertEqual(0, record.dispatch_count)
        self.assertTrue(any('lock_cleanup_completed' in event['diagnostic_codes'] for event in record.events))

    async def test_expired_retained_locks_are_visible_before_recovery(self):
        _, task, declaration, handle, _ = await self.tokenless()
        health = self.runtime.health()
        self.assertEqual(0, health['active_normal_lock_count'])
        self.assertEqual(len(handle.tokens), health['recovery_backlog']['retained_lock_count'])
        self.assertEqual(len(handle.tokens), health['recovery_backlog']['expired_lock_count'])
        public = self.service.get_execution_task(task.task_id)
        child = public['f3_children'][0]
        self.assertEqual('missing', child['lock_recovery']['token_persistence'])

    async def test_partial_union_fails_closed_with_actionable_diagnostics(self):
        _, _, declaration, handle, _ = await self.tokenless()
        self.runtime.locks.release(replace(handle, tokens=handle.tokens[:1]))
        retained = self.runtime.locks.records()
        await self.runtime.recover_once('periodic')
        self.assertEqual(retained, self.runtime.locks.records())
        item = self.runtime.reconciliation_items()[0]
        self.assertTrue(item['lock_recovery']['requires_manual_intervention'])
        self.assertEqual('manual_intervention_required', self.runtime.health()['status'])
        self.assertEqual('ownership_proof_unavailable', item['lock_recovery']['last_failure_category'])
        self.assertEqual([], self.runtime.children.get(declaration['child_id']).lock_tokens)

    async def test_conflict_hold_is_never_adopted_as_unrecorded_authority(self):
        _, _, declaration, handle, _ = await self.tokenless()
        DurableLockStore.promote_to_conflict_hold(self.runtime.locks, handle, reason_code='synthetic_hold')
        retained = self.runtime.locks.records()
        await self.runtime.recover_once('periodic')
        self.assertEqual(retained, self.runtime.locks.records())
        self.assertEqual([], self.runtime.children.get(declaration['child_id']).lock_tokens)

    async def test_process_loss_after_binding_recovers_exact_persisted_tokens(self):
        _, _, declaration, _, _ = await self.tokenless()
        def loss(stage):
            if stage == 'after_unrecorded_token_binding_before_release':
                raise SimulatedProcessLoss()
        self.runtime.locks._fault_hook = loss
        with self.assertRaises(SimulatedProcessLoss):
            self.runtime._release_orphaned_child_locks(declaration, self.runtime.children.get(declaration['child_id']))
        self.assertTrue(self.runtime.children.get(declaration['child_id']).lock_tokens)
        self.runtime = self.new_runtime()
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once('startup')
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual(0, self.runtime.children.get(declaration['child_id']).dispatch_count)

    async def test_live_claim_is_deferred_not_cancelled_or_released(self):
        _, _, declaration, handle, _ = await self.tokenless()
        self.instant -= timedelta(seconds=121)
        retained = self.runtime.locks.records()
        await self.runtime.recover_once('periodic')
        self.assertEqual(retained, self.runtime.locks.records())
        self.assertEqual(0, self.runtime.health()['recovery_failures'])
        self.assertTrue(self.runtime.reconciliation_items()[0]['lock_recovery']['waiting_for_claim_expiry'])


class UnrecordedProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DurableLockStore(self.temp.name)
        self.owner = LockOwner('owner', 'child', 'plan', 'capability', 'attempt')
        self.handle = self.store.acquire_once(requests(3), owner=self.owner, timing=LOCK_TIMING, now=NOW)
        self.snapshot = self.store.records()
        self.bound = []

    def settle(self, **overrides):
        args = dict(owner=self.owner, expected_records=self.snapshot,
                    complete_request_hash=lock_set_hash(requests(3)),
                    execution_created_at=NOW.isoformat(), execution_updated_at=NOW.isoformat(),
                    persist_tokens=self.bound.append, timing=LOCK_TIMING,
                    now=NOW + timedelta(seconds=121))
        args.update(overrides)
        return self.store.settle_unrecorded(**args)

    def test_every_owner_identity_field_is_required(self):
        for field in ('owner_id', 'task_id', 'plan_id', 'operation_id', 'attempt_id'):
            with self.subTest(field=field), self.assertRaises(LockOwnershipError):
                self.settle(owner=replace(self.owner, **{field: 'other'}))
        self.assertEqual(self.snapshot, self.store.records())
        self.assertEqual([], self.bound)

    def test_keys_modes_generations_reasons_and_complete_hash_are_bound(self):
        for field, value in (('key', 'synthetic:other'), ('mode', 'shared'),
                             ('generation', 999), ('evidence_references', ('different_reason',))):
            with self.subTest(field=field), self.assertRaises(LockOwnershipError):
                self.settle(expected_records=(replace(self.snapshot[0], **{field: value}), *self.snapshot[1:]))
        with self.assertRaises(LockOwnershipError):
            self.settle(complete_request_hash='0' * 64)
        with self.assertRaises(LockOwnershipError):
            self.settle(expected_records=self.snapshot[:2])
        self.assertEqual([], self.bound)
        self.assertEqual(self.snapshot, self.store.records())

    def test_active_or_renewed_records_do_not_become_recovery_authority(self):
        with self.assertRaises(LockOwnershipError):
            self.settle(now=NOW + timedelta(seconds=1))
        self.store.renew(self.handle, now=NOW + timedelta(seconds=1))
        with self.assertRaises(LockOwnershipError):
            self.settle(expected_records=self.store.records())
        self.assertEqual([], self.bound)
        self.assertEqual(3, len(self.store.records()))

    def test_concurrent_replacement_and_later_acquisition_are_preserved(self):
        self.store.release(self.handle)
        replacement = self.store.acquire_once(requests(3), owner=self.owner, timing=LOCK_TIMING,
                                             now=NOW + timedelta(seconds=1))
        self.assertGreater(replacement.tokens[0].generation, self.handle.tokens[-1].generation)
        current = self.store.records()
        with self.assertRaises(LockOwnershipError):
            self.settle()
        with self.assertRaises(LockOwnershipError):
            self.settle(expected_records=current)
        self.assertEqual(current, self.store.records())
        self.assertEqual([], self.bound)

    def test_binding_failure_preserves_all_locks(self):
        def fail(_):
            raise ExecutionStorageError('synthetic binding failure')
        with self.assertRaises(ExecutionStorageError):
            self.settle(persist_tokens=fail)
        self.assertEqual(self.snapshot, self.store.records())

    def test_lock_write_failure_keeps_original_tokens_for_retry(self):
        with patch.object(self.store, '_write_state', side_effect=LockStorageError('synthetic disk failure')):
            with self.assertRaises(LockStorageError):
                self.settle()
        self.assertEqual(self.snapshot, self.store.records())
        self.assertEqual(self.handle.tokens, self.bound[0].tokens)
        self.store.release(self.bound[0])
        self.assertEqual((), self.store.records())

    def test_unrelated_owner_survives_exact_recovery(self):
        other = self.store.acquire_once((LockRequest('synthetic:unrelated', (LockScope.RESOURCE,),
            LockMode.EXCLUSIVE, ('synthetic',)),), owner=replace(self.owner, task_id='other', owner_id='other'),
            timing=LOCK_TIMING, now=NOW)
        self.assertEqual(3, self.settle())
        self.assertEqual([other.tokens[0].generation], [r.generation for r in self.store.records()])

    def test_new_hold_blocks_exception_cleanup(self):
        self.store.promote_to_conflict_hold(self.handle, reason_code='synthetic_hold')
        with self.assertRaises(LockOwnershipError):
            self.store.release(self.handle, pre_dispatch_cleanup=True)
        self.assertTrue(all(r.conflict_hold for r in self.store.records()))
