"""HAMCP-155 review 1 (PR #304 at c615658): the independent reviewer's probes, kept unchanged.

The probe body below is byte-for-byte the reviewer's ``reviewer_probes.py``
from line 2 onward (the review bundle's SHA256.json binds it). The only
mechanical adaptation is this docstring and the path prelude, which puts the
repository root and ``scripts/`` on ``sys.path`` in place of the reviewer's
PYTHONPATH (snapshot root, tests and runtime).
"""
from pathlib import Path as _Path
import sys as _sys

_ROOT = _Path(__file__).resolve().parents[1]
_sys.path[:0] = [str(_ROOT), str(_ROOT / "scripts"), str(_ROOT / "hass_mcp_engineering_beta")]
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import asyncio
import unittest
from tests.hamcp155_fixtures import ConfigurationDeleteServiceTestCase, automation_config, script_config
from tests import test_hamcp155_configuration_delete_execution as execution
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
from tests.hamcp069a_entity_fixtures import _provider_identity
import configuration_delete_contract_acceptance as lane

class RuntimeProbes(ConfigurationDeleteServiceTestCase):
    async def test_fresh_composition_without_core_refuses_before_any_ha_read(self):
        self.add('automation', 'review_no_core')
        created = await self.approved('automation', 'review_no_core')
        self.runtime = F3RuntimeIntegration(service=self.service, storage_root=str(self.root/'plans'), configuration_gateway=self.resource_gateway, backup_gateway=None, lifecycle_gateway=None, provider_identity_reader=_provider_identity, retention_days=90, core_runtime=None)
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once('startup')
        self.config_rest.requests.clear(); self.ws.commands.clear()
        from ha_mcp_engineering.errors import GovernanceError
        try:
            result = await self.apply(created)
        except GovernanceError as exc:
            self.assertEqual(exc.details.get('reason_code'),'pending_signed_registry_admission')
        else:
            self.assertEqual(result['task_state'], 'failed_pre_dispatch')
        self.assertEqual(self.config_rest.requests, [])
        self.assertEqual(self.ws.commands, [])
        self.assertEqual(self.config_rest.writes(), [])

    async def test_duplicate_registry_identity_refuses(self):
        self.add('automation','review_duplicate')
        duplicate=deepcopy(self.ws.entries['automation.review_duplicate'])
        duplicate.update(id='anotherregistryid', entity_id='automation.review_duplicate_other')
        self.ws.entries['automation.review_duplicate_other']=duplicate
        from ha_mcp_engineering.errors import GovernanceError
        with self.assertRaises(GovernanceError):
            await self.create_one('automation','review_duplicate')
        self.assertEqual(self.repository.list(),[])
        self.assertEqual(self.config_rest.writes(),[])

    async def test_lane_recreate_write_receipt_matches_actual_requests(self):
        self.add('automation','review_receipt')
        observed=lane.ObservedRest(self.config_rest)
        self.resource_gateway.rest_client=observed
        created=await self.approved('automation','review_receipt')
        deleted=await self.apply(created)
        self.assertEqual(deleted['task_state'],'succeeded_verified')
        rolled=await self.service.rollback_change(created['plan_id'])
        recreate=self.service.get_plan(rolled['rollback_plan_id'])
        await self.approve(recreate)
        observed.writes.clear()
        result=await self.apply(recreate)
        self.assertEqual(result['task_state'],'succeeded_verified')
        self.assertEqual(observed.writes,['POST'],self.config_rest.requests)

    async def test_unadmitted_retained_plan_does_not_persist_execution_state(self):
        self.add('automation','review_persist')
        created=await self.approved('automation','review_persist')
        self.core.withdraw()
        self.config_rest.requests.clear();self.ws.commands.clear()
        def files():
            return {str(p.relative_to(self.root/'plans')):p.read_bytes() for p in (self.root/'plans').rglob('*') if p.is_file()}
        before=files()
        from ha_mcp_engineering.errors import GovernanceError
        try:await self.apply(created)
        except GovernanceError:pass
        after=files()
        self.assertEqual(self.config_rest.requests,[])
        self.assertEqual(self.ws.commands,[])
        changed=sorted(k for k in set(before)|set(after) if before.get(k)!=after.get(k))
        self.assertEqual(changed,[], 'Unadmitted apply persisted these plan/task files')

    async def test_called_scripts_always_elevate(self):
        actions = [{'action':'script.secure_house'}, {'action':'script.turn_on','target':{'entity_id':'script.secure_house'}}, {'parallel':[{'action':'lock.lock','target':{'entity_id':'lock.front_door'}}]}]
        for n, action in enumerate(actions):
            target='review_call_'+str(n)
            config=automation_config(target); config['actions']=[action]
            self.add('automation', target, config)
            created=await self.create_one('automation',target)
            self.assertEqual(created['policy_decision']['policy_class'],'elevated_admin')
            self.assertTrue(execution.disclosure(created)['consequence_statements'])

    async def test_legacy_spelling_recreates_exactly(self):
        target='review_legacy'
        config=automation_config(target)
        config['trigger']=config.pop('triggers');config['condition']=config.pop('conditions');config['action']=config.pop('actions')
        self.add('automation',target,config)
        await execution.EndToEndTests.assert_delete_then_recreate(self,'automation',target,config)

    async def test_final_read_retirement_prevents_delete(self):
        self.add('automation','review_retire')
        created=await self.approved('automation','review_retire')
        start=self.config_rest.config_gets[('automation','review_retire')]
        def retire(kind,target,n):
            if n==start+2:self.core.withdraw()
        self.config_rest.after_config_get=retire
        result=await self.apply(created)
        self.assertNotEqual(result['task_state'],'succeeded_verified')
        self.assertEqual(self.config_rest.writes(),[])

class LaneProbes(unittest.IsolatedAsyncioTestCase):
    async def test_readback_uses_registry_entity_id(self):
        """Expected invariant: state is read at Core's actual registered entity id."""
        target='hamcp155_auto_review'
        entity='automation.hamcp155_hamcp155_auto_review'
        class Rest:
            def __init__(self):self.paths=[]
            async def call(self,method,path):
                path=str(path); self.paths.append(path)
                if path.startswith('/config/'):return 200, lane.automation_config(target,{'event':'review'})
                return (200,{}) if path=='/states/'+entity else (404,None)
        class WS:
            async def ok(self,command):
                return [{'platform':'automation','unique_id':target,'id':'regreview','entity_id':entity}]
        rest=Rest()
        readback,_=await lane.independent_readback(rest,WS(),'automation',target)
        self.assertEqual(readback['state_status'],200,rest.paths)

    async def test_readback_does_not_claim_absence_for_lingering_renamed_entity(self):
        target='hamcp155_auto_review'; entity='automation.hamcp155_hamcp155_auto_review'
        class Rest:
            async def call(self,method,path):
                return (200,{}) if str(path)=='/states/'+entity else (404,None)
        class WS:
            async def ok(self,command):return []
        try:
            readback,_=await lane.independent_readback(Rest(),WS(),'automation',target)
        except (ValueError, AssertionError):
            return  # Refusal is correct when no captured entity identity is available.
        # A verifier needs the entity id captured before deletion; guessing it from the config id is unsafe.
        self.assertFalse(lane._absent(readback),readback)

if __name__=='__main__':unittest.main(verbosity=2)
