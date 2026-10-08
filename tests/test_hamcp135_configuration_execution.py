"""Governed synthetic writes and whole-bundle execution integrity."""
from tests import test_hamcp135_configuration_eligibility as eligibility_fixtures
from tests.test_dev14_configuration_plans import ConfigurationPlanTestCase
from tests.test_f3_runtime_integration import _ExactFakeConfigurationGateway, _provider_identity
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
from ha_mcp_engineering.errors import ErrorCode, GovernanceError


class ExecutionTests(ConfigurationPlanTestCase):
    create_retry_plan = eligibility_fixtures.EligibilityTests.create_retry_plan
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.runtime = F3RuntimeIntegration(service=self.service, storage_root=str(self.root/'plans'),
            configuration_gateway=_ExactFakeConfigurationGateway(self.gateway), backup_gateway=None,
            lifecycle_gateway=None,provider_identity_reader=_provider_identity,retention_days=90)
        self.service.f3_runtime=self.runtime
        await self.runtime.recover_once('startup')

    async def test_absent_f3_refuses_without_legacy_task_or_approval_consumption(self):
        self.service.f3_runtime = None
        for full in (False, True):
            with self.subTest(full=full):
                created = await self.create_retry_plan(full)
                await self.approve(created)
                approved = self.repository.get(created['plan_id']).approval
                self.gateway.calls.clear()
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.apply(created['plan_id'], created['plan_hash'])
                self.assertEqual(raised.exception.code, ErrorCode.UNSUPPORTED_CHANGE_OPERATION)
                self.assertEqual(self.gateway.calls, [])
                self.assertIsNone(self.service.task_repository.get_for_plan(created['plan_id']))
                self.assertEqual(self.repository.get(created['plan_id']).approval, approved)
                self.assertIn('change_apply_rejected', self.audit_path.read_text())

    async def test_both_transformations_apply_and_duplicate_never_resends(self):
        for full in (False, True):
            with self.subTest(full=full):
                created=await self.create_retry_plan(full)
                await self.approve(created)
                self.gateway.calls.clear()
                result=await self.service.apply(created['plan_id'],created['plan_hash'])
                self.assertEqual(result['task_state'],'succeeded_verified',result['execution_task']['verification_summary'])
                writes=[c for c in self.gateway.calls if c[0]=='write']
                self.assertEqual(len(writes),5 if full else 1)
                result2=await self.service.apply(created['plan_id'],created['plan_hash'])
                self.assertEqual(result2['task_id'],result['task_id'])
                self.assertEqual(writes,[c for c in self.gateway.calls if c[0]=='write'])

    async def test_any_member_drift_before_first_write_blocks_all_writes(self):
        for name in eligibility_fixtures.fixture():
            with self.subTest(member=name):
                created=await self.create_retry_plan();await self.approve(created)
                member=eligibility_fixtures.fixture()[name]
                self.gateway.configs[(member['resource_type'],member['target_id'])]['description']='drift'
                self.gateway.calls.clear()
                result=await self.service.apply(created['plan_id'],created['plan_hash'])
                self.assertEqual(result['task_state'],'failed_pre_dispatch')
                self.assertFalse(any(c[0]=='write' for c in self.gateway.calls))
        # A garage-local refusal must leave valid unrelated configuration work usable.
        other=await self.create_automation_plan();await self.approve(other)
        result=await self.service.apply(other['plan_id'],other['plan_hash'])
        self.assertEqual(result['task_state'],'succeeded_verified')

    async def test_all_prefixes_and_final_bundle_detect_external_drift(self):
        original_write=self.gateway.write
        original_read=self.gateway.read
        for prefix in range(1,6):
            for changed in ('script','bedtime'):
                if prefix==5 and changed=='bedtime':
                    continue  # current-child mismatch is separately covered
                with self.subTest(prefix=prefix,changed=changed):
                    created=await self.create_retry_plan();await self.approve(created)
                    count=0
                    pending_drift=False
                    async def drift_read(resource,target):
                        nonlocal pending_drift
                        result=await original_read(resource,target)
                        if pending_drift and target=='fixture_close':
                            self.gateway.configs[(resource,target)]['description']='external drift'
                            pending_drift=False
                        return result
                    self.gateway.read=drift_read
                    async def drift(action,resource,target,config):
                        nonlocal count, pending_drift
                        result=await original_write(action,resource,target,config); count+=1
                        if count==prefix:
                            member=eligibility_fixtures.fixture()[changed]
                            if prefix==1 and changed=='script':
                                # Change after its authoritative successful read,
                                # so this exercises next-child prefix protection.
                                pending_drift=True
                            else:
                                self.gateway.configs[(member['resource_type'],member['target_id'])]['description']='external drift'
                        return result
                    self.gateway.write=drift
                    self.gateway.calls.clear()
                    result=await self.service.apply(created['plan_id'],created['plan_hash'])
                    self.assertNotEqual(result['task_state'],'succeeded_verified')
                    self.assertEqual(len([c for c in self.gateway.calls if c[0]=='write']),prefix)
                    task=self.service.task_repository.get(result['task_id'])
                    codes=[code for d in self.runtime.children.declarations_for_task(task.task_id)
                           if self.runtime.children.get(d['child_id']) is not None
                           for e in self.runtime.children.get(d['child_id']).to_dict()['events'] for code in e['diagnostic_codes']]
                    self.assertIn('retry_bundle_state_mismatch',codes)
                    self.gateway.write=original_write
                    self.gateway.read=original_read

    async def test_read_unavailable_is_local_and_uncertain_write_is_not_resent(self):
        created=await self.create_retry_plan();await self.approve(created)
        original_read=self.gateway.read
        async def fail(resource,target):
            if target=='fixture_bedtime': raise RuntimeError('synthetic read unavailable')
            return await original_read(resource,target)
        self.gateway.read=fail
        self.gateway.calls.clear()
        result=await self.service.apply(created['plan_id'],created['plan_hash'])
        self.assertEqual(result['task_state'],'failed_pre_dispatch')
        self.assertFalse(any(c[0]=='write' for c in self.gateway.calls))
        self.gateway.read=original_read
        created=await self.create_retry_plan();await self.approve(created)
        self.gateway.fail_after_write_target=('script','fixture_close')
        self.gateway.calls.clear()
        first=await self.service.apply(created['plan_id'],created['plan_hash'])
        for _ in range(2):
            await self.runtime.recover_once('test')
            await self.service.apply(created['plan_id'],created['plan_hash'])
        script_writes=[c for c in self.gateway.calls if c[:4]==('write','update','script','fixture_close')]
        self.assertEqual(len(script_writes),1)
        task=self.service.task_repository.get(first['task_id'])
        self.assertEqual(task.state.value,'succeeded_verified')

    async def test_invalid_configuration_check_refuses_without_dispatch(self):
        for validation in ({}, {'result':'invalid','errors':['synthetic invalid']}):
            created=await self.create_retry_plan(False);await self.approve(created)
            self.gateway.validation_result=validation
            self.gateway.calls.clear()
            result=await self.service.apply(created['plan_id'],created['plan_hash'])
            self.assertEqual(result['task_state'],'failed_pre_dispatch')
            self.assertFalse(any(c[0]=='write' for c in self.gateway.calls))

    async def test_inverse_restores_exact_script_only_with_new_elevated_approval(self):
        created=await self.create_retry_plan();await self.approve(created)
        result=await self.service.apply(created['plan_id'],created['plan_hash'])
        before=len([c for c in self.gateway.calls if c[0]=='write'])
        rollback=await self.runtime.create_rollback_plan(self.repository.get(created['plan_id']),created['plan_hash'])
        self.assertEqual(len([c for c in self.gateway.calls if c[0]=='write']),before)
        # Exact full-transfer evidence permits a fresh, separately approved inverse.
        self.assertEqual(rollback['status'],'rollback_plan_created')
        self.assertTrue(rollback['approval_required'])
        inverse=self.repository.get(rollback['rollback_plan_id'])
        self.assertEqual(inverse.policy_decision.policy_class.value,'elevated_admin')
        self.assertEqual(inverse.operations[-1].resource_type,'script')
        self.assertIn('original internal retry',inverse.description)

    async def test_partial_inverse_discloses_excluded_script_and_requires_new_approval(self):
        created=await self.create_retry_plan();await self.approve(created)
        await self.service.apply(created['plan_id'],created['plan_hash'])
        self.gateway.configs[('script','fixture_close')]['description']='external edit'
        self.gateway.calls.clear()
        result=await self.service.rollback_change(created['plan_id'],created['plan_hash'])
        self.assertEqual(result['status'],'rollback_partial_plan_created')
        self.assertTrue(result['approval_required'])
        self.assertIn('script',result['operations_excluded'])
        self.assertFalse(any(c[0]=='write' for c in self.gateway.calls))
        original=self.repository.get(created['plan_id'])
        self.assertFalse(original.rollback.available)
        again=await self.service.rollback_change(created['plan_id'],created['plan_hash'])
        self.assertEqual(again['status'],'rollback_partial_plan_created')
        inverse=self.repository.get(result['rollback_plan_id'])
        self.assertTrue(all(op.resource_type=='automation' for op in inverse.operations))
        self.assertEqual(inverse.approval.state.value,'required')
