"""Independent PR314 delta probes. Synthetic Core only; assert required cleanup."""
import unittest
from unittest import mock
from datetime import timedelta
from tests.hamcp232_helper_options_fixtures import HelperOptionsServiceTestCase, STEP_PREFIX
from test_hamcp232_helper_options import TIME_CONSTANT_20
from ha_mcp_engineering.errors import HomeAssistantTimeoutError, HomeAssistantApiError, GovernanceError
from ha_mcp_engineering.f3_runtime.runtime import _dispatch_authority_expiry
import hashlib

class Cleanup(HelperOptionsServiceTestCase):
    def lose_open(self):
        original = self.options_rest.request
        async def request(method,path,body=None,**kwargs):
            result=await original(method,path,body,**kwargs)
            if method=='POST' and str(path)==STEP_PREFIX.rstrip('/'):
                raise HomeAssistantTimeoutError(details={})
            return result
        self.options_rest.request=request
        return original

    async def recover_and_assert_empty(self):
        await self.restart_runtime()
        for _ in range(3):
            await self.runtime.recover_once('independent_cleanup_probe')
        self.assertEqual(self.options_rest.flows, {}, 'Open flow survives restored connectivity, runtime restart and three recovery sweeps')

    async def test_lost_planning_open_is_cleaned_after_restart(self):
        self.add_filter()
        original=self.lose_open()
        with self.assertRaises(HomeAssistantTimeoutError):
            await self.create_options(TIME_CONSTANT_20)
        self.options_rest.request=original
        self.assertEqual(self.options_rest.submits(),[])
        await self.recover_and_assert_empty()

    async def test_lost_dispatch_open_is_cleaned_after_restart(self):
        self.add_filter()
        created=await self.approved_options(TIME_CONSTANT_20)
        original=self.lose_open()
        result=await self.apply(created)
        self.options_rest.request=original
        self.assertEqual(result['task_state'],'failed_pre_dispatch')
        self.assertEqual(self.options_rest.submits(),[])
        await self.recover_and_assert_empty()

    async def test_known_flow_failed_abort_is_retried_after_restart(self):
        self.add_filter()
        created=await self.approved_options(TIME_CONSTANT_20)
        planning_opens=len(self.options_rest.opens())
        self.options_rest.submit_failure=HomeAssistantTimeoutError(details={})
        failure=HomeAssistantApiError(details={'status':500,'provider_response_received':True})
        def fail_held(entry_id,flow_id,ordinal):
            self.options_rest.abort_failure=failure if ordinal==planning_opens+1 else None
        self.options_rest.after_open=fail_held
        result=await self.apply(created)
        self.assertIn('options_flow_orphaned',self.child_codes(result['task_id']))
        self.options_rest.abort_failure=None
        self.options_rest.after_open=None
        self.options_rest.submit_failure=None
        self.assertEqual(len(self.options_rest.submits()),1)
        await self.recover_and_assert_empty()

class Admission(HelperOptionsServiceTestCase):
    def store_bytes(self):
        return {str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
    async def test_three_inert_sweeps_then_expiry_ends_locally(self):
        self.add_filter()
        created=await self.approved_options(TIME_CONSTANT_20)
        plan=self.service._load(created['plan_id'])
        await self.runtime._initialize(plan,self.service.plan_hash(plan))
        self.core.withdraw()
        await self.restart_runtime()
        before,calls=self.store_bytes(),self.ha_calls()
        for _ in range(3):
            await self.runtime.recover_once('independent_admission_probe')
            self.assertEqual(self.store_bytes(),before)
            self.assertEqual(self.ha_calls(),calls)
        future=_dispatch_authority_expiry(plan)+timedelta(seconds=601)
        with mock.patch.object(self.service,'now',return_value=future):
            await self.runtime.recover_once('independent_expiry_probe')
        self.assertEqual(self.ha_calls(),calls)
        self.assertNotEqual(self.store_bytes(),before,'Expiry must settle the task locally')

if __name__=='__main__': unittest.main(verbosity=2)
