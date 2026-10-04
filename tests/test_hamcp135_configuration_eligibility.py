"""Offline proof, approval and refusal tests with sanitized configuration fixtures."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

from tests.test_dev14_configuration_plans import ConfigurationPlanTestCase
from ha_mcp_engineering.governance.configuration_eligibility import bound_proof, derive_proof
from ha_mcp_engineering.governance.policy import policy_snapshot_matches
from ha_mcp_engineering.governance.resources import compare_resource_verification

FIXTURES = Path(__file__).parent / 'fixtures/hamcp135_configuration'


def fixture(full=True):
    return json.loads((FIXTURES / ('caller_owned_retry.json' if full else 'minimal_retry_removal.json')).read_text())['members']


class EligibilityTests(ConfigurationPlanTestCase):
    async def create_retry_plan(self, full=True, mutate=None):
        members = fixture(full)
        if mutate:
            mutate(members)
        operations = []
        for name, member in members.items():
            self.gateway.configs[(member['resource_type'],member['target_id'])] = copy.deepcopy(member['baseline'])
            operations.append({'operation_id':name,'resource_type':member['resource_type'], 'action':'update',
                               'target_id':member['target_id'], 'proposed_config':member['candidate'],
                               'depends_on':[] if not operations else [operations[-1]['operation_id']]})
        return await self.service.create_configuration_plan(title='Synthetic retry transfer', description='Offline test', operations=operations)

    async def test_minimal_and_full_proofs(self):
        for full in (False, True):
            with self.subTest(full=full):
                created = await self.create_retry_plan(full)
                plan = self.repository.get(created['plan_id'])
                self.assertIsNotNone(bound_proof(plan.operations))
                self.assertTrue(plan.risk.apply_allowed)
                self.assertEqual(plan.policy_decision.policy_class.value, 'elevated_admin')
                self.assertEqual([a.value for a in plan.policy_decision.required_acknowledgements], ['plan_approval'])
                self.assertTrue(policy_snapshot_matches(plan))
                for op in plan.operations:
                    comparison = compare_resource_verification(op.resource_type, op.proposed_config, op.proposed_config)
                    self.assertTrue(comparison.normalization_valid, comparison)
                    self.assertTrue(comparison.semantic_match, comparison)

    async def test_unmarked_plan_not_upgraded(self):
        with patch('ha_mcp_engineering.governance.service.derive_proof', return_value=None):
            created = await self.create_retry_plan(False)
        plan = self.repository.get(created['plan_id'])
        self.assertIsNotNone(derive_proof(plan.operations))
        self.assertIsNone(bound_proof(plan.operations))
        self.assertEqual(plan.policy_decision.policy_class.value,'prohibited')
        self.assertTrue(policy_snapshot_matches(plan))

    async def test_every_positive_gate_and_response_shape_is_bound(self):
        created=await self.create_retry_plan()
        original=self.repository.get(created['plan_id']).operations
        proof=derive_proof(original)
        self.assertEqual(proof['positive_call_gates'],11)
        def reject(mutator):
            ops=copy.deepcopy(original); mutator(ops)
            # Recompute ordinary proposal bindings so refusal proves grammar,
            # not merely an outdated configuration hash.
            from ha_mcp_engineering.governance.resources import normalize_resource_config
            from ha_mcp_engineering.governance.normalize import stable_hash
            for op in ops:
                op.normalized_proposed_config=normalize_resource_config(op.resource_type,op.proposed_config)
                op.proposed_config_hash=stable_hash(op.normalized_proposed_config)
            self.assertIsNone(derive_proof(ops))
        from ha_mcp_engineering.governance.configuration_eligibility import at
        for site in proof['sites']:
            index=next(i for i,op in enumerate(original) if op.operation_id==site['operation_id'])
            path=tuple(site['path']);start=path[-1]
            for gate_offset in (1,2):
                with self.subTest(site=site['operation_id'],gate=gate_offset):
                    reject(lambda ops: at(ops[index].proposed_config,path[:-1])[start+gate_offset]['choose'][0].update(conditions=[]))
            for predicate in range(len(at(original[index].proposed_config,path[:-1])[start+1]['choose'][0]['conditions'])):
                for offset in (1,2):
                    with self.subTest(site=site['operation_id'],predicate=predicate,offset=offset):
                        def weaken(ops):
                            gate=at(ops[index].proposed_config,path[:-1])[start+offset]['choose'][0]
                            policy=gate['conditions'] if offset==1 else gate['conditions'][1]['conditions']
                            policy.pop(predicate)
                        reject(weaken)
            reject(lambda ops: at(ops[index].proposed_config,path[:-1])[start].update(variables={}))
            reject(lambda ops: at(ops[index].proposed_config,path[:-1])[start+2]['choose'][0]['conditions'][0].update(value_template='{{ true }}'))
            reject(lambda ops: at(ops[index].proposed_config,path[:-1])[start+1]['choose'][0]['sequence'][0].update(action='script.turn_on'))
            reject(lambda ops: at(ops[index].proposed_config,path[:-1])[start+2]['default'].append({'action':'script.fixture_close'}))
        reject(lambda ops: ops[0].proposed_config['sequence'][0]['choose'][1].update(conditions=[]))
        reject(lambda ops: ops[0].proposed_config['sequence'][0]['default'].append({'action':'cover.close_cover','target':{'entity_id':'cover.garage_fixture'}}))
        reject(lambda ops: ops[0].proposed_config['sequence'][0]['choose'][1]['sequence'][1].update(timeout='00:00:31'))
        reject(lambda ops: ops[0].proposed_config.update(mode='parallel'))
        reject(lambda ops: ops[2].proposed_config.update(mode='single'))

    async def test_members_dependencies_hashes_and_marker_tampering(self):
        c=await self.create_retry_plan(); original=self.repository.get(c['plan_id'])
        for mutation in (
            lambda p:p.operations.pop(),
            lambda p:p.operations.reverse(),
            lambda p:p.operations[2].depends_on.clear(),
            lambda p:setattr(p.operations[0],'target_id','wrong_script'),
            lambda p:p.operations[0].risk.evidence[-1]['proof'].update(digest='0'*64),
            lambda p:p.operations[0].normalized_proposed_config.update(mode='parallel'),
            lambda p:p.operations[0].current_config.update(description='changed'),
            lambda p:p.operations[1].risk.evidence.pop(),
        ):
            p=copy.deepcopy(original);mutation(p)
            self.assertIsNone(bound_proof(p.operations))
            self.assertFalse(policy_snapshot_matches(p))
        self.assertIsNone(derive_proof(original.operations[:1]))

    async def test_inverse_not_eligible_and_minimal_first_path_is_exact(self):
        c=await self.create_retry_plan(False);original=self.repository.get(c['plan_id']).operations
        inverse=copy.deepcopy(original)
        inverse[0].current_config,inverse[0].proposed_config=inverse[0].proposed_config,inverse[0].current_config
        self.assertIsNone(derive_proof(inverse))
        for i in (0,1):
            ops=copy.deepcopy(original);ops[0].proposed_config['sequence'][i]={'stop':'changed'}
            self.assertIsNone(derive_proof(ops))

    async def test_bounds_unsupported_templates_and_client_override_refuse(self):
        from ha_mcp_engineering.errors import GovernanceError
        c=await self.create_retry_plan();ops=self.repository.get(c['plan_id']).operations
        for value in ('x'*400001, '{{ states("sensor.unreviewed") }}'):
            modified=copy.deepcopy(ops);modified[0].proposed_config['description']=value
            self.assertIsNone(derive_proof(modified))
        with self.assertRaises(GovernanceError):
            await self.service.create_configuration_plan(title='test',description='test',operations=[{
                'operation_id':'x','resource_type':'script','action':'update','target_id':'fixture_close',
                'proposed_config':fixture(False)['script']['candidate'],'eligibility_override':True}])

    async def test_preserved_unreviewed_effect_or_dynamic_call_is_not_a_proof(self):
        created=await self.create_retry_plan()
        operations=self.repository.get(created['plan_id']).operations
        from ha_mcp_engineering.governance.resources import normalize_resource_config,resource_fingerprint
        from ha_mcp_engineering.governance.normalize import stable_hash
        for service in ('cover.open_cover','{{ dynamic_service }}','script.turn_on'):
            ops=copy.deepcopy(operations)
            for config in (ops[3].current_config,ops[3].proposed_config):
                config['actions'][0]['default'].append({'action':service})
            op=ops[3]
            op.normalized_current_config=normalize_resource_config('automation',op.current_config)
            op.normalized_proposed_config=normalize_resource_config('automation',op.proposed_config)
            op.current_state_fingerprint=resource_fingerprint('automation',op.current_config)
            op.proposed_config_hash=stable_hash(op.normalized_proposed_config)
            self.assertIsNone(derive_proof(ops))
