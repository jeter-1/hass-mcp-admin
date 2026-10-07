"""Exact governed inverses: synthetic stores/configuration writes only."""
import copy
import asyncio
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch

from tests import test_hamcp135_configuration_eligibility as fixtures
from tests.test_dev14_configuration_plans import ConfigurationPlanTestCase
from tests.test_f3_runtime_integration import _ExactFakeConfigurationGateway, _provider_identity
from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
from ha_mcp_engineering.governance import configuration_rollback_eligibility as inverse
from ha_mcp_engineering.governance.policy import policy_snapshot_matches
from ha_mcp_engineering.governance.service import ChangeGovernanceService
from ha_mcp_engineering.governance.storage import ChangePlanRepository
from ha_mcp_engineering.audit import AuditLogger

HISTORICAL = Path(__file__).parent/'fixtures/hamcp135_rollback/beta16'


class RollbackTests(ConfigurationPlanTestCase):
    create_retry_plan = fixtures.EligibilityTests.create_retry_plan

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.restart_runtime()

    async def restart_runtime(self):
        self.runtime = F3RuntimeIntegration(
            service=self.service, storage_root=str(self.root/'plans'),
            configuration_gateway=_ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None, lifecycle_gateway=None,
            provider_identity_reader=_provider_identity, retention_days=90)
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once('startup')

    def writes(self):
        return [c for c in self.gateway.calls if c[0]=='write']

    async def seed(self):
        """Fresh disposable copy of records produced by the shipped writer."""
        shutil.rmtree(self.root/'plans')
        shutil.copytree(HISTORICAL/'store', self.root/'plans')
        self.repository = ChangePlanRepository(self.root/'plans')
        self.service = ChangeGovernanceService(self.repository, self.gateway,
            AuditLogger(str(self.audit_path), 'synthetic-rollback-audit'), now=self.service.now)
        for m in json.loads((HISTORICAL/'gateway.json').read_text()):
            self.gateway.configs[(m['resource_type'],m['target_id'])] = m['config']
        await self.restart_runtime()
        self.gateway.calls.clear()
        p=json.loads((HISTORICAL/'PROVENANCE.json').read_text())
        return {'plan_id':p['source_plan_id'],'plan_hash':p['source_plan_hash']}

    def drift(self, index):
        m=list(fixtures.fixture().values())[index]
        self.gateway.configs[(m['resource_type'],m['target_id'])]['description']='synthetic external drift'

    async def apply_request(self, request):
        return await self.service.apply(request['plan_id'],request['plan_hash'])

    async def forward(self, prefix=5, full=True):
        created=await self.create_retry_plan(full);await self.approve(created)
        members=list(fixtures.fixture(full).values())
        original=self.gateway.write
        count=0
        async def write(action, resource, target, config):
            nonlocal count
            result=await original(action,resource,target,config);count+=1
            if count==prefix and prefix<len(members):
                m=members[prefix]
                self.gateway.configs[(m['resource_type'],m['target_id'])]['description']='temporary forward stop'
            return result
        self.gateway.write=write
        if prefix==0:
            self.gateway.configs[('script','fixture_close')]['description']='temporary forward stop'
        try:
            result=await self.service.apply(created['plan_id'],created['plan_hash'])
        finally:
            self.gateway.write=original
        if prefix<len(members):
            m=members[prefix]
            self.gateway.configs[(m['resource_type'],m['target_id'])]=copy.deepcopy(m['baseline'])
        self.assertEqual(count,prefix)
        return created,result

    async def rollback(self, source):
        result=await self.service.rollback_change(source['plan_id'],source['plan_hash'])
        return result,{'plan_id':result['rollback_plan_id'],'plan_hash':result['plan_hash']}

    async def test_full_inverse_fresh_approval_exact_order_and_no_repeated_dispatch(self):
        source,forward=await self.forward()
        prior=len(self.writes())
        created,request=await self.rollback(source)
        self.assertEqual(created['status'],'rollback_plan_created')
        self.assertEqual(len(self.writes()),prior)
        plan=self.repository.get(request['plan_id'])
        proof=inverse.bound(plan)
        self.assertEqual([op.proposed_config for op in plan.operations],
                         [op.current_config for op in reversed(self.repository.get(source['plan_id']).operations)])
        self.assertEqual(proof['selected'],[4,3,2,1,0])
        self.assertEqual(plan.policy_decision.policy_class.value,'elevated_admin')
        self.assertEqual(plan.approval.state.value,'required')
        self.assertIn('original internal retry',plan.description)
        with self.assertRaises(GovernanceError):
            await self.service.apply(request['plan_id'],request['plan_hash'])
        self.assertEqual(len(self.writes()),prior)
        await self.approve(request)
        approved_again, approved_request = await self.rollback(source)
        self.assertEqual(approved_request, request)
        self.assertFalse(approved_again['approval_required'])
        self.gateway.calls.clear()
        applied=await self.service.apply(request['plan_id'],request['plan_hash'])
        self.assertEqual(applied['task_state'],'succeeded_verified',applied)
        expected=list(reversed(list(fixtures.fixture().values())))
        self.assertEqual([c[3] for c in self.writes()],[m['target_id'] for m in expected])
        for m in expected:
            self.assertEqual(self.gateway.configs[(m['resource_type'],m['target_id'])],dict(next(op.current_config for op in self.repository.get(source['plan_id']).operations if op.target_id==m['target_id']), id=m['target_id']))
        count=len(self.writes())
        duplicate=await self.service.apply(request['plan_id'],request['plan_hash'])
        self.assertEqual(duplicate['task_id'],applied['task_id'])
        again,req2=await self.rollback(source)
        self.assertEqual(req2,request)
        self.assertFalse(again['approval_required'])
        self.assertEqual(len(self.writes()),count)

    async def test_continuous_union_at_every_boundary_and_fenced_previous_handles(self):
        from ha_mcp_engineering.f3.locks import DurableLockStore, LockConflict, LockOwnershipError
        from tests.test_f3_lock_manager import _owner, _request, TIMING
        source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
        other=DurableLockStore(self.root/'plans')
        execute=self.runtime._execute_child;boundaries=[];handles=[]
        async def check(plan,task,d,*args,**kwargs):
            if d['operation_ordinal']:
                observed=other.records()
                self.assertTrue(observed)
                self.assertTrue({'script:fixture_close','automation:fixture_notification','automation:fixture_cleaner',
                                 'automation:fixture_away','automation:fixture_bedtime'} <= {r.key for r in observed})
                previous=self.runtime.children.declarations_for_task(task.task_id)[d['operation_ordinal']-1]
                self.assertEqual({r.task_id for r in observed},{previous['child_id']})
                for r in observed:
                    with self.assertRaises(LockConflict):
                        other.acquire_once((_request(r.key),),owner=_owner('competing-process'),timing=TIMING,now=self.service.now())
                handles.append(self.runtime._observed_lock_handle(observed,TIMING))
                boundaries.append(d['operation_ordinal'])
            result=await execute(plan,task,d,*args,**kwargs)
            for handle in handles:
                with self.assertRaises(LockOwnershipError):other.release(handle)
            return result
        with patch.object(self.runtime,'_execute_child',side_effect=check):
            result=await self.apply_request(request)
        self.assertEqual(result['task_state'],'succeeded_verified')
        self.assertEqual(boundaries,[1,2,3,4]);self.assertEqual(other.records(),())

    async def test_reconstruction_before_release_retains_and_live_lease_continues(self):
        from ha_mcp_engineering.f3.executor import SimulatedProcessLoss
        source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
        factory=self.runtime._executor
        def create(seconds):
            executor=factory(seconds)
            def stop(stage):
                if stage=='after_verified_result_before_lock_release':raise SimulatedProcessLoss(stage)
            executor._fault_hook=stop;return executor
        with patch.object(self.runtime,'_executor',side_effect=create):
            with self.assertRaises(SimulatedProcessLoss):await self.apply_request(request)
        original=self.runtime;before=original.locks.records()
        self.assertTrue(before);self.assertEqual(len(self.writes()),1)
        # A second integration over the same store must derive retention from
        # disk even though the first executor never reached its release hook.
        await self.restart_runtime()
        restored=self.runtime.locks.records()
        self.assertEqual({r.key for r in restored},{r.key for r in before})
        self.assertTrue(all(r.generation >= before[0].generation for r in restored))
        result=await self.apply_request(request)
        self.assertEqual(result['task_state'],'succeeded_verified')
        self.assertEqual(len(self.writes()),5);self.assertEqual(original.locks.records(),())

    async def test_expired_between_child_ownership_refuses_and_settles_partial(self):
        from ha_mcp_engineering.f3.executor import SimulatedProcessLoss
        source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
        execute=self.runtime._execute_child
        async def stop(plan,task,d,*args,**kwargs):
            if d['operation_ordinal']==1:raise SimulatedProcessLoss('retained predecessor')
            return await execute(plan,task,d,*args,**kwargs)
        with patch.object(self.runtime,'_execute_child',side_effect=stop):
            with self.assertRaises(SimulatedProcessLoss):await self.apply_request(request)
        self.assertTrue(self.runtime.locks.records());self.assertEqual(len(self.writes()),1)
        now=self.service.now;self.service.now=lambda:now()+timedelta(minutes=3)
        try:
            await self.restart_runtime()
            for _ in range(3):await self.runtime.recover_once('expired-retention')
            task=self.service.task_repository.get_for_plan(request['plan_id'])
            self.assertNotEqual(task.state.value,'succeeded_verified')
            self.assertEqual(len(self.writes()),1);self.assertEqual(self.runtime.locks.records(),())
        finally:self.service.now=now

    async def test_transfer_requires_exact_successor_claim_and_verified_predecessor(self):
        from dataclasses import replace
        from ha_mcp_engineering.f3.locks import LockOwnershipError
        from ha_mcp_engineering.f3.models import LockOwner
        from ha_mcp_engineering.f3_runtime.runtime import PRODUCTION_LOCK_TIMING
        source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
        factory=self.runtime._executor;count=0;checked=[]
        def create(seconds):
            nonlocal count
            count+=1;executor=factory(seconds)
            if count==2:
                def check(stage):
                    if stage!='before_lock_acquisition':return
                    task=self.service.task_repository.get_for_plan(request['plan_id'])
                    ds=self.runtime.children.declarations_for_task(task.task_id)
                    record=self.runtime.children.get(ds[1]['child_id'])
                    owner=LockOwner(record.identity['owner_id'],ds[1]['child_id'],request['plan_id'],
                                    record.operation,record.identity['attempt_id'])
                    requests=self.runtime._sequence_lock_cache[task.task_id]
                    before=self.runtime.locks.records()
                    for field in ('owner_id','operation_id','attempt_id'):
                        with self.assertRaises(LockOwnershipError):
                            self.runtime._inverse_lock_acquire(requests,owner=replace(owner,**{field:'wrong'}),
                                                               timing=PRODUCTION_LOCK_TIMING,now=self.service.now())
                    original_get=self.runtime.children.get
                    previous=original_get(ds[0]['child_id'])
                    bad=replace(previous,evidence=dict(previous.evidence,resulting_state_fingerprint='0'*64))
                    with patch.object(self.runtime.children,'get',side_effect=lambda child:
                            bad if child==ds[0]['child_id'] else original_get(child)):
                        with self.assertRaises(LockOwnershipError):
                            self.runtime._inverse_lock_acquire(requests,owner=owner,timing=PRODUCTION_LOCK_TIMING,
                                                               now=self.service.now())
                    self.assertEqual(self.runtime.locks.records(),before);checked.append(True)
                executor._fault_hook=check
            return executor
        with patch.object(self.runtime,'_executor',side_effect=create):result=await self.apply_request(request)
        self.assertEqual(checked,[True]);self.assertEqual(result['task_state'],'succeeded_verified')

    async def test_transfer_process_loss_before_tokens_and_before_preflight_settles_without_redispatch(self):
        from ha_mcp_engineering.f3.executor import SimulatedProcessLoss
        for stage in ('after_transfer_commit','after_lock_acquisition_before_token_persistence','after_lock_acquisition_before_preflight'):
            with self.subTest(stage=stage):
                source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
                factory=self.runtime._executor;count=0
                transfer=self.runtime.locks.transfer_complete
                def interrupted_transfer(*args,**kwargs):
                    def fault(point):
                        if point=='after_state_replace':raise SimulatedProcessLoss(stage)
                    if stage=='after_transfer_commit':self.runtime.locks._fault_hook=fault
                    try:return transfer(*args,**kwargs)
                    finally:self.runtime.locks._fault_hook=None
                def create(seconds):
                    nonlocal count
                    count+=1;executor=factory(seconds)
                    if count==2:
                        def stop(point):
                            if point==stage:raise SimulatedProcessLoss(stage)
                        executor._fault_hook=stop
                    return executor
                with patch.object(self.runtime,'_executor',side_effect=create), patch.object(
                        self.runtime.locks,'transfer_complete',side_effect=interrupted_transfer):
                    with self.assertRaises(SimulatedProcessLoss):await self.apply_request(request)
                locks=self.runtime.locks.records();self.assertTrue(locks)
                self.assertEqual(len(self.writes()),1)
                self.assertTrue(self.runtime.reconciliation_items())
                now=self.service.now;self.service.now=lambda:now()+timedelta(minutes=3)
                try:
                    await self.restart_runtime()
                    for _ in range(3):await self.runtime.recover_once('transfer-crash')
                    self.assertEqual(len(self.writes()),1)
                    self.assertEqual(self.runtime.locks.records(),())
                    self.assertTrue(self.runtime.readiness_state()['execution_ready'])
                finally:self.service.now=now

    async def test_transfer_durable_failure_preserves_prefix_and_cleanup_is_recoverable(self):
        source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
        original=self.runtime.locks.transfer_complete
        def fail(*args,**kwargs):
            def write_failure(stage):
                if stage=='before_state_replace':raise OSError('synthetic transfer storage failure')
            self.runtime.locks._fault_hook=write_failure
            try:return original(*args,**kwargs)
            finally:self.runtime.locks._fault_hook=None
        with patch.object(self.runtime.locks,'transfer_complete',side_effect=fail):
            result=await self.apply_request(request)
        self.assertNotEqual(result['task_state'],'succeeded_verified')
        self.assertEqual(len(self.writes()),1)
        self.assertFalse(self.runtime.readiness_state()['execution_ready'])
        await self.runtime.recover_once('transfer-failure')
        self.assertEqual(self.runtime.locks.records(),())
        self.assertEqual(len(self.writes()),1)

    async def test_successor_cancellation_and_held_predecessor_prevent_transfer(self):
        from ha_mcp_engineering.f3.locks import DurableLockStore
        from tests.test_f3_lock_manager import TIMING
        for reason in ('cancelled_successor','held_predecessor'):
            with self.subTest(reason=reason):
                source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
                acquire=self.runtime._inverse_lock_acquire
                def refuse(requests,*,owner,timing,now):
                    d=self.runtime.children.declaration(owner.task_id)
                    if d['operation_ordinal']==1:
                        if reason=='cancelled_successor':self.runtime.children.cancel(owner.task_id,now=now)
                        else:
                            store=DurableLockStore(self.root/'plans')
                            store.promote_to_conflict_hold(self.runtime._observed_lock_handle(store.records(),TIMING),
                                                          reason_code='synthetic_unresolved_ownership')
                    return acquire(requests,owner=owner,timing=timing,now=now)
                with patch.object(self.runtime,'_inverse_lock_acquire',side_effect=refuse):
                    result=await self.apply_request(request)
                self.assertNotEqual(result['task_state'],'succeeded_verified')
                self.assertEqual(len(self.writes()),1)
                if reason=='held_predecessor':
                    self.assertTrue(all(r.conflict_hold for r in self.runtime.locks.records()))
                    self.assertTrue(self.runtime.reconciliation_items())
                else:self.assertEqual(self.runtime.locks.records(),())
    async def test_every_verified_forward_prefix_and_minimal_negative(self):
        for prefix in range(6):
            with self.subTest(prefix=prefix):
                source,_=await self.forward(prefix)
                before=len(self.writes())
                if prefix==0:
                    with self.assertRaises(GovernanceError) as error:await self.rollback(source)
                    self.assertEqual(error.exception.code,ErrorCode.ROLLBACK_NOT_AVAILABLE)
                    self.assertEqual(len(self.writes()),before)
                    continue
                created,request=await self.rollback(source)
                self.assertEqual(len(created['operations_excluded']),5-prefix)
                await self.approve(request);self.gateway.calls.clear()
                applied=await self.service.apply(request['plan_id'],request['plan_hash'])
                self.assertEqual(applied['task_state'],'succeeded_verified')
                self.assertEqual(len(self.writes()),prefix)
                self.assertEqual(self.writes()[-1][3],'fixture_close')
                for m in fixtures.fixture().values():
                    self.assertEqual(self.gateway.configs[(m['resource_type'],m['target_id'])],dict(next(op.current_config for op in self.repository.get(source['plan_id']).operations if op.target_id==m['target_id']), id=m['target_id']))
        source,_=await self.forward(prefix=1,full=False)
        result,request=await self.rollback(source)
        self.assertEqual(result['status'],'rollback_unavailable')
        self.assertIsNone(inverse.bound(self.repository.get(request['plan_id'])))

    async def test_shipped_writer_cached_prohibited_and_historical_hashes_unchanged(self):
        for line in (HISTORICAL/'SHA256SUMS').read_text().splitlines():
            digest, name=line.split('  ',1)
            self.assertEqual(hashlib.sha256((HISTORICAL/name).read_bytes()).hexdigest(),digest)
        source=await self.seed()
        old_id=json.loads((HISTORICAL/'PROVENANCE.json').read_text())['cached_prohibited_inverse_id']
        path=self.root/'plans'/f'{old_id}.json'; before=path.read_bytes()
        _, request=await self.rollback(source)
        self.assertNotEqual(request['plan_id'],old_id)
        self.assertEqual(path.read_bytes(),before)
        old=self.service._load(old_id)
        self.assertEqual(old.policy_decision.policy_class.value,'prohibited')
        self.assertIsNone(inverse.bound(old))
        await self.approve(request)
        self.assertEqual((await self.apply_request(request))['task_state'],'succeeded_verified')
        self.assertEqual(path.read_bytes(),before)

    async def test_each_creation_drift_selects_only_safe_callers_and_reports_exclusions(self):
        for changed in range(5):
            with self.subTest(changed=changed):
                source=await self.seed();self.drift(changed)
                created,request=await self.rollback(source)
                proof=inverse.bound(self.repository.get(request['plan_id']))
                self.assertFalse(proof['complete']);self.assertNotIn(0,proof['selected'])
                self.assertNotIn(changed,proof['selected'])
                self.assertEqual(len(created['operations_to_restore'])+len(created['operations_excluded']),5)
                self.assertIn('script',created['operations_excluded'])
                await self.approve(request)
                result=await self.apply_request(request)
                self.assertEqual(result['task_state'],'succeeded_verified')
                self.assertFalse(any(c[2]=='script' for c in self.writes()))
                self.assertEqual(len(self.writes()),4 if changed==0 else 3)
                again,_=await self.rollback(source)
                self.assertEqual(again['status'],'rollback_partial_plan_created')

    async def test_each_member_drift_after_approval_preserves_approval_and_blocks_writes(self):
        for changed in range(5):
            with self.subTest(changed=changed):
                source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
                before=self.repository.get(request['plan_id']).approval
                self.drift(changed)
                result=await self.apply_request(request)
                self.assertEqual(result['task_state'],'failed_pre_dispatch')
                self.assertEqual(self.writes(),[])
                self.assertEqual(self.repository.get(request['plan_id']).approval,before)

    async def test_each_member_drift_between_children_and_at_final_verification(self):
        original=self.gateway.write
        for final in (False,True):
            for changed in range(5):
                with self.subTest(final=final,changed=changed):
                    source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
                    count=0
                    async def drift_write(*args):
                        nonlocal count
                        result=await original(*args);count+=1
                        if count==(5 if final else 1):self.drift(changed)
                        return result
                    self.gateway.write=drift_write
                    try:result=await self.apply_request(request)
                    finally:self.gateway.write=original
                    self.assertNotEqual(result['task_state'],'succeeded_verified')
                    self.assertEqual(len(self.writes()),5 if final else 1)
                    if not final:self.assertFalse(any(c[2]=='script' for c in self.writes()))

    async def test_public_context_and_copied_tampered_markers_cannot_grant_authority(self):
        source=await self.seed();_,request=await self.rollback(source)
        plan=self.repository.get(request['plan_id'])
        for mutate in (
            lambda p:setattr(p,'plan_id','f'*32),
            lambda p:p.operations[0].proposed_config.update(description='replacement'),
            lambda p:setattr(p.operations[0],'target_id','substitute'),
            lambda p:p.operations[-1].depends_on.clear(),
            lambda p:p.operations[0].risk.evidence[-1]['proof']['source'].update(task_id='wrong-task'),
            lambda p:p.operations[0].risk.evidence[-1]['proof'].update(selected=[0]),
            lambda p:p.operations[0].risk.evidence.pop(),
        ):
            candidate=copy.deepcopy(plan);mutate(candidate)
            self.assertIsNone(inverse.bound(candidate))
            self.assertFalse(policy_snapshot_matches(candidate))
        public=await self.service.create_configuration_plan(title='Forged inverse',description='Synthetic test',
            operations=[{'operation_id':op.operation_id,'resource_type':op.resource_type,'action':op.action,
                         'target_id':op.target_id,'proposed_config':op.proposed_config,'depends_on':op.depends_on}
                        for op in plan.operations],
            caller_context={'rollback_source_plan_id':source['plan_id'],'rollback_model':inverse.MODEL,
                            'proof':inverse.bound(plan)})
        unmarked=self.repository.get(public['plan_id'])
        self.assertIsNone(inverse.bound(unmarked))
        self.assertEqual(unmarked.policy_decision.policy_class.value,'prohibited')
        self.assertEqual(self.writes(),[])

    async def test_source_receipt_unknown_missing_wrong_binding_and_corruption_refuse_locally(self):
        source=await self.seed()
        plan=self.repository.get(source['plan_id'])
        task=self.service.task_repository.get_for_plan(source['plan_id'])
        declarations=self.runtime.children.declarations_for_task(task.task_id)
        get=self.runtime.children.get
        for changed in range(5):
            for kind in ('missing','unverified','wrong_resource','wrong_hash','no_intent'):
                with self.subTest(member=changed,kind=kind):
                    def altered(child_id):
                        record=get(child_id)
                        if child_id!=declarations[changed]['child_id']:return record
                        if kind=='missing':return None
                        if kind=='unverified':record.normalized_outcome='verification_required'
                        if kind=='wrong_resource':record.target['target_id']='wrong'
                        if kind=='wrong_hash':record.evidence['resulting_state_fingerprint']='0'*64
                        if kind=='no_intent':record.dispatch_intent=None;record.dispatch_count=0
                        return record
                    with patch.object(self.runtime.children,'get',side_effect=altered):
                        with self.assertRaises(GovernanceError):await self.rollback(source)
        runtime=self.runtime.children.runtime
        with patch.object(self.runtime.children,'runtime',side_effect=lambda child_id:dict(runtime(child_id),approval_consumption_reference=None)):
            with self.assertRaises(GovernanceError):await self.rollback(source)
        for field,value in (('plan_hash','0'*64),('plan_id','f'*32),('execution_request_id','wrong')):
            altered=copy.deepcopy(task);setattr(altered,field,value)
            with patch.object(self.service.task_repository,'get_for_plan',return_value=altered):
                with self.assertRaises(GovernanceError):self.runtime._rollback_source_snapshot(plan)
        receipt=self.runtime.children._path(declarations[0]["child_id"])
        receipt.write_text('{')
        with self.assertRaises(GovernanceError):await self.rollback(source)
        self.assertEqual(self.writes(),[])
        other=await self.create_automation_plan();await self.approve(other)
        self.assertEqual((await self.apply_request(other))['task_state'],'succeeded_verified')

    async def test_source_evidence_loss_after_approval_does_not_consume_or_fault_unrelated_work(self):
        source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
        before=self.repository.get(request['plan_id']).approval
        original=self.repository.get
        with patch.object(self.repository,'get',side_effect=lambda pid:None if pid==source['plan_id'] else original(pid)):
            with self.assertRaises(GovernanceError):await self.apply_request(request)
            other=await self.create_automation_plan()
        self.assertEqual(self.repository.get(request['plan_id']).approval,before)
        self.assertEqual(self.writes(),[])
        other=await self.create_automation_plan();await self.approve(other)
        self.assertEqual((await self.apply_request(other))['task_state'],'succeeded_verified')

    async def test_validation_identity_approval_hash_expiry_and_absent_f3_refuse(self):
        for refusal in ('validation','identity','hash','expiry','no_f3'):
            with self.subTest(refusal=refusal):
                source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
                before=self.repository.get(request['plan_id']).approval
                now=self.service.now
                if refusal=='validation':self.gateway.validation_result={'result':'invalid','errors':['synthetic']}
                if refusal=='identity':self.gateway.configs[('automation','fixture_bedtime')]['id']='wrong'
                if refusal=='hash':request['plan_hash']='0'*64
                if refusal=='expiry':self.service.now=lambda:now()+timedelta(days=3)
                if refusal=='no_f3':self.service.f3_runtime=None
                try:
                    try:result=await self.apply_request(request)
                    except GovernanceError:pass
                    else:self.assertEqual(result['task_state'],'failed_pre_dispatch')
                finally:self.service.now=now;self.gateway.validation_result={'result':'valid','errors':None}
                self.assertEqual(self.writes(),[])
                self.assertIsNone(self.repository.get(request['plan_id']).approval.consumed_at)

    async def test_complete_lock_union_prefix_and_pre_intent_cancellation(self):
        source,_=await self.forward(1);_,request=await self.rollback(source);await self.approve(request)
        plan=self.service._load(request['plan_id'])
        task,prepared,requests=await self.runtime._initialize(plan,request['plan_hash'])
        keys={r.key for r in requests}
        self.assertTrue({'script:fixture_close','automation:fixture_notification','automation:fixture_cleaner',
                         'automation:fixture_away','automation:fixture_bedtime'} <= keys)
        self.gateway.calls.clear()
        result=await self.service.cancel_execution_task(task.task_id)
        self.assertEqual(result['status'],'cancelled_pre_dispatch')
        await self.runtime.recover_once('test')
        with self.assertRaises(GovernanceError):await self.apply_request(request)
        self.assertEqual(self.writes(),[])

    async def test_lost_reply_and_cancellation_never_repeat_inverse_dispatch(self):
        source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
        self.gateway.fail_after_write_target=('automation','fixture_bedtime')
        result=await self.apply_request(request)
        for _ in range(2):
            await self.runtime.recover_once('test')
            await self.apply_request(request)
        writes=self.writes()
        self.assertEqual(len(writes),5)
        self.assertEqual(len(set(writes)),5)
        with self.assertRaises(GovernanceError):await self.service.cancel_execution_task(result['task_id'])
        self.assertEqual(self.writes(),writes)

    async def test_current_core_authority_refusal_and_conflicting_whole_bundle_lock(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from tests.test_f3_lock_manager import _owner, _request
        from ha_mcp_engineering.f3.models import LockTiming
        for reason in ('core_identity_or_generation','lock'):
            with self.subTest(reason=reason):
                source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
                before=self.repository.get(request['plan_id']).approval
                if reason=='core_identity_or_generation':
                    core=SimpleNamespace(reconcile_once=AsyncMock(),acquire_f3=Mock(return_value=None))
                    self.runtime.core_runtime=core
                else:
                    self.runtime.locks.acquire_once((_request('script:fixture_close'),),
                        owner=_owner('other'),timing=LockTiming(120,20,0,.05),now=self.service.now())
                result=await self.apply_request(request)
                self.assertNotEqual(result['task_state'],'succeeded_verified')
                self.assertEqual(self.writes(),[])
                self.assertEqual(self.repository.get(request['plan_id']).approval,before)
                if reason=='core_identity_or_generation':
                    core.reconcile_once.assert_awaited_once_with('mutation_pre_dispatch')
                    core.acquire_f3.assert_called_once()

    async def test_crash_after_intent_and_after_verified_child_preserves_at_most_once(self):
        from ha_mcp_engineering.f3.executor import SimulatedProcessLoss
        for stage in ('after_durable_intent_before_provider_invocation','after_verified_result_before_lock_release'):
            with self.subTest(stage=stage):
                source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
                executor=self.runtime._executor;injected=False
                def create(seconds):
                    result=executor(seconds)
                    def fault(point):
                        nonlocal injected
                        if point==stage and not injected:
                            injected=True;raise SimulatedProcessLoss(point)
                    result._fault_hook=fault
                    return result
                with patch.object(self.runtime,'_executor',side_effect=create):
                    try:await self.apply_request(request)
                    except SimulatedProcessLoss:pass
                self.assertTrue(injected)
                now=self.service.now;self.service.now=lambda:now()+timedelta(minutes=3)
                try:
                    await self.restart_runtime()
                    for _ in range(2):
                        await self.runtime.recover_once('test')
                        try:await self.apply_request(request)
                        except GovernanceError as exc:
                            self.assertEqual(exc.code,ErrorCode.DUPLICATE_APPLY_ATTEMPT)
                finally:self.service.now=now
                writes=self.writes()
                self.assertEqual(len(writes),len(set(writes)))
                if stage=='after_durable_intent_before_provider_invocation':
                    self.assertEqual(writes,[])
                    held=self.runtime.locks.records()
                    self.assertTrue(held)
                    self.assertTrue(all(item.conflict_hold for item in held))
                    self.assertTrue({'script:fixture_close','automation:fixture_notification','automation:fixture_cleaner',
                                     'automation:fixture_away','automation:fixture_bedtime'} <= {r.key for r in held})
                else:
                    self.assertEqual(len([w for w in writes if w[3]=='fixture_bedtime']),1)

    async def test_real_durable_child_failure_remains_fail_closed(self):
        source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
        from ha_mcp_engineering.f3.persistence import ExecutionStorageError
        with patch.object(self.runtime.children,'claim',side_effect=ExecutionStorageError('synthetic durable failure')):
            with self.assertRaises(ExecutionStorageError):await self.apply_request(request)
        self.assertEqual(self.writes(),[])
        self.assertFalse(self.runtime.readiness_state()['execution_ready'])

    async def test_concurrent_inverse_requests_reuse_one_unapproved_attempt(self):
        source=await self.seed()
        original=self.gateway.read
        async def interleaved(*args):
            await asyncio.sleep(.001)
            return await original(*args)
        self.gateway.read=interleaved
        try:results=await asyncio.gather(self.rollback(source),self.rollback(source))
        finally:self.gateway.read=original
        self.assertEqual(results[0][1],results[1][1])
        plan=self.repository.get(results[0][1]['plan_id'])
        self.assertEqual(plan.approval.state.value,'required')
        self.assertEqual(self.repository.get(source['plan_id']).rollback.request_id,plan.plan_id)
        self.assertEqual(self.writes(),[])

    async def test_link_before_plan_crash_recreates_only_unapproved_attempt(self):
        source=await self.seed()
        snapshot=self.runtime._rollback_source_snapshot(self.repository.get(source['plan_id']))
        target=inverse.proof_id(snapshot)
        original=self.service._record
        def fail(plan,*args,**kwargs):
            if plan.plan_id==target:raise OSError('synthetic inverse creation interrupted')
            return original(plan,*args,**kwargs)
        from ha_mcp_engineering.f3.persistence import ExecutionStorageError
        with patch.object(self.service,'_record',side_effect=fail):
            with self.assertRaises(ExecutionStorageError):await self.rollback(source)
        self.assertIsNone(self.repository.get(target))
        self.assertEqual(self.repository.get(source['plan_id']).rollback.request_id,target)
        _,request=await self.rollback(source)
        self.assertEqual(request['plan_id'],target)
        self.assertEqual(self.repository.get(target).approval.state.value,'required')
        self.assertEqual(self.writes(),[])

    async def test_source_loss_between_inverse_children_keeps_recovery_local(self):
        source=await self.seed();_,request=await self.rollback(source);await self.approve(request)
        original_get=self.repository.get;original_write=self.gateway.write;missing=False
        async def lose_source(*args):
            nonlocal missing
            result=await original_write(*args);missing=True
            return result
        self.gateway.write=lose_source
        try:
            with patch.object(self.repository,'get',side_effect=lambda pid:None if missing and pid==source['plan_id'] else original_get(pid)):
                with self.assertRaises(GovernanceError) as refused:await self.apply_request(request)
                self.assertEqual(refused.exception.code,ErrorCode.ROLLBACK_NOT_AVAILABLE)
                for _ in range(2):await self.runtime.recover_once('test')
                self.assertTrue(self.runtime.readiness_state()['execution_ready'])
                self.assertEqual(len(self.writes()),1)
        finally:self.gateway.write=original_write
        task=self.service.task_repository.get_for_plan(request['plan_id'])
        self.assertNotEqual(task.state.value,'succeeded_verified')
        with self.assertRaises(GovernanceError):await self.apply_request(request)
        self.assertEqual(len(self.writes()),1)
