"""Offline controls for the required assembled-Core receipt, not Core execution."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'hass_mcp_engineering_beta')]
import automation_baseline_contract_acceptance as lane
from test_automation_baseline_capture import Client, Core, fixture, export
from test_alarmo_interval_observer import CORE, NOT_SLOTS, NOT_STRINGS, OTHER_CORE, SLOT_VERSIONS, cleanup_value, receipt
import alarmo_inspection_contract_acceptance as alarmo
from ha_mcp_engineering.audit_baseline import capture_contracts as c, compare_baselines, load_baseline
from ha_mcp_engineering.audit_baseline.capture_provider import BaselineCaptureProvider
from ha_mcp_engineering.audit_baseline.capture_service import BaselineCaptureService
from ha_mcp_engineering.request_context import begin_request, end_request

# The slot the committed window binds CORE to (rollback; never the optional candidate).
CORE_SLOT = next(slot for slot, version in SLOT_VERSIONS.items() if version == CORE)

spec = importlib.util.spec_from_file_location('baseline_disposable_fixture', ROOT / 'tests/fixtures/real_ha_device_migration/custom_components/beta23_device_fixture/baseline_fixture.py')
synthetic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(synthetic)


def data(changed=False):
    value = fixture(0)
    rows = synthetic.rows(changed) + [{'id': lane.PREFIX + 'unknown'}]
    for row in rows:
        identifier = row['id']
        entity = 'automation.' + identifier
        value['states'].append({'entity_id': entity, 'state': 'off' if row.get('initial_state') is False else 'on', 'attributes': {'id': identifier}})
        value['registry'].append({'entity_id': entity, 'unique_id': identifier, 'platform': 'automation', 'disabled_by': None})
        value['configs'][identifier] = None if identifier.endswith('unknown') else row
    return value


class DisposableReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        telemetry, self.token = begin_request()
        telemetry.caller_id = 'synthetic-disposable-offline'
        telemetry.core_dispatch_authorizer = lambda: True
        client = Client(data())
        service = BaselineCaptureService(BaselineCaptureProvider(client, Core()))
        before, _ = await export(service, 17)
        client.data = data(True)
        after, _ = await export(service, 17)
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory)/str(i) for i in range(2)]
            for path, baseline in zip(paths, (before, after)):
                path.write_bytes(c.canonical(baseline))
            counts = compare_baselines(*(load_baseline(p) for p in paths)).counts
        observations = [receipt(lane.EXPECTED_COMMANDS, CORE) for _ in range(2)]
        for item in observations: item['kind'] = 'control'
        self.value = {'result':'PASS','core':CORE,'support_slot':CORE_SLOT,'production_authority':False,
            'fixture_cleanup':True,'authority_transition':[20,21], 'continuation_reads':0,
            'nonadmin_refusal':'access_denied','missing_authority_reads':0,
            'predecessor_admission':{'acquired':False,'ha_requests':0,'reason':'authority_unavailable'},
            'recreated_anchor_differs':True,'restored_anchor_preserved':True,'baselines':[before,after],
            'baseline_sha256':[c.digest(before),c.digest(after)],
            'comparison_counts':counts,'observations':observations}

    def tearDown(self):
        end_request(self.token)

    async def test_reconstructs_actual_export_and_all_five_classifications(self):
        result = lane.verify_result(self.value, core_version=CORE, support_slot=CORE_SLOT)
        self.assertEqual(result['records_before'], 122)
        self.assertEqual(result['comparison_counts'], {'ADDED':1,'REMOVED':1,'CHANGED':1,'UNCHANGED':119,'UNKNOWN':1,'TOTAL':123})

    async def test_rejects_wrong_digest_order_comparison_or_missing_execution(self):
        for mutate in (
            lambda v: v['baseline_sha256'].__setitem__(0, '0'*64),
            lambda v: v['comparison_counts'].update(REMOVED=0),
            lambda v: v.update(baselines=list(reversed(v['baselines'])), baseline_sha256=list(reversed(v['baseline_sha256']))),
            lambda v: v.update(result='NOT_RUN'),
            lambda v: v.update(continuation_reads=1),
            lambda v: v.update(restored_anchor_preserved=False),
            lambda v: v.update(recreated_anchor_differs=False),
            lambda v: v.update(fixture_cleanup=False),
            lambda v: v.update(production_authority=True),
            lambda v: v.update(authority_transition=[19,20]),
            lambda v: v['predecessor_admission'].update(acquired=True),
            lambda v: v['predecessor_admission'].update(acquired=0),
            lambda v: v['predecessor_admission'].update(ha_requests=1),
            lambda v: v['predecessor_admission'].update(ha_requests=False),
            lambda v: v.update(observations=[]),
        ):
            value=copy.deepcopy(self.value);mutate(value)
            with self.assertRaises((AssertionError, ValueError, KeyError)):
                lane.verify_result(value, core_version=CORE, support_slot=CORE_SLOT)

    async def test_command_service_storage_and_coverage_fail_closed(self):
        for mutate in (
            lambda v: v['observations'][0]['events'].append({'kind':'service','origin':'command'}),
            lambda v: v['observations'][0]['events'].append({'kind':'command','origin':'background','command':'ping'}),
            lambda v: v['observations'][0]['events'][0].update(command='other'),
            lambda v: v['observations'][0]['events'].pop(),
            lambda v: v['observations'][0]['coverage'].update({'Store.async_save':False}),
            lambda v: v['observations'][0]['final_store_hashes'].update({'core.device_registry':'1'*64}),
        ):
            value=copy.deepcopy(self.value);mutate(value)
            with self.assertRaises((AssertionError, ValueError)):
                lane.verify_result(value, core_version=CORE, support_slot=CORE_SLOT)


    async def test_receipt_is_bound_to_the_caller_supplied_slot_core(self):
        for other in [v for v in SLOT_VERSIONS.values() if v != CORE] + list(NOT_SLOTS):
            with self.subTest(other=other), self.assertRaises((AssertionError, ValueError)):
                lane.verify_result(copy.deepcopy(self.value), core_version=other, support_slot=CORE_SLOT)
        for wrong in NOT_STRINGS:
            value = copy.deepcopy(self.value)
            value['core'] = wrong
            with self.subTest(core_version=wrong), self.assertRaises((AssertionError, ValueError)):
                lane.verify_result(value, core_version=wrong, support_slot=CORE_SLOT)
        with self.assertRaises(TypeError):
            lane.verify_result(copy.deepcopy(self.value))
        with self.assertRaises(TypeError):
            lane.verify_result(copy.deepcopy(self.value), core_version=CORE)
        for mutate in (lambda v: v.update(core=OTHER_CORE), lambda v: v.pop('core'),
                       lambda v: v['observations'][0].update(core_version=OTHER_CORE),
                       lambda v: v['observations'][1].update(core_version=OTHER_CORE)):
            value = copy.deepcopy(self.value); mutate(value)
            with self.subTest(value=value.get('core')), self.assertRaises((AssertionError, ValueError, KeyError)):
                lane.verify_result(value, core_version=CORE, support_slot=CORE_SLOT)

    async def test_receipt_follows_every_alarmo_slot(self):
        for slot, version in SLOT_VERSIONS.items():
            value = copy.deepcopy(self.value)
            value.update(core=version, support_slot=slot)
            value['observations'] = [receipt(lane.EXPECTED_COMMANDS, version) for _ in range(2)]
            for item in value['observations']: item['kind'] = 'control'
            with self.subTest(version=version):
                self.assertEqual(lane.verify_result(value, core_version=version, support_slot=slot)['result'], 'PASS')

    async def test_receipt_is_bound_to_the_exact_support_slot(self):
        # PR #242 review finding 3: the slot is checked, not only the Core.
        self.assertEqual(lane.verify_result(copy.deepcopy(self.value), core_version=CORE,
                                            support_slot=CORE_SLOT)['result'], 'PASS')
        wrong_slots = [slot for slot in ('current', 'candidate', 'rollback') if slot != CORE_SLOT]
        for wrong in [*wrong_slots, '', 'Rollback', ' ' + CORE_SLOT, CORE_SLOT + ' ', *NOT_STRINGS]:
            value = copy.deepcopy(self.value)
            value['support_slot'] = wrong
            with self.subTest(nested=wrong), self.assertRaises((AssertionError, ValueError)):
                lane.verify_result(value, core_version=CORE, support_slot=CORE_SLOT)
            with self.subTest(caller=wrong), self.assertRaises((AssertionError, ValueError)):
                lane.verify_result(copy.deepcopy(self.value), core_version=CORE, support_slot=wrong)
        value = copy.deepcopy(self.value)
        del value['support_slot']
        with self.assertRaises((AssertionError, ValueError, KeyError)):
            lane.verify_result(value, core_version=CORE, support_slot=CORE_SLOT)

    def cleanup(self, baseline, *, core=CORE, outer=None):
        """Run the real retained-receipt verifier with the real nested baseline verifier."""
        value = cleanup_value(core)
        value['automation_baseline'] = baseline
        if outer is not None:
            value['support_slot'] = outer
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'result.json', Path(directory) / 'cleanup.json'
            source.write_text(json.dumps(value))
            with patch('subprocess.run', return_value=SimpleNamespace(stdout=b'')):
                return alarmo.verify_disposable_cleanup(source, output, core)

    async def test_retained_receipt_refuses_wrong_or_missing_nested_baseline_slot(self):
        # Positive control: the exact nested slot passes the unpatched verifiers.
        result = self.cleanup(copy.deepcopy(self.value))
        self.assertEqual((result['result'], result['support_slot']), ('PASS', CORE_SLOT))
        self.assertEqual(result['required_automation_baseline']['result'], 'PASS')
        for wrong in [slot for slot in SLOT_VERSIONS if slot != CORE_SLOT] + ['candidate', 'other', None]:
            value = copy.deepcopy(self.value)
            value['support_slot'] = wrong
            with self.subTest(nested=wrong), self.assertRaises((AssertionError, ValueError)):
                self.cleanup(value)
        value = copy.deepcopy(self.value)
        del value['support_slot']
        with self.assertRaises((AssertionError, ValueError, KeyError)):
            self.cleanup(value)
        # The existing Core and outer-slot refusals still hold.
        value = copy.deepcopy(self.value)
        value['core'] = OTHER_CORE
        with self.assertRaises((AssertionError, ValueError)):
            self.cleanup(value)
        for outer in [slot for slot in ('current', 'candidate', 'rollback') if slot != CORE_SLOT]:
            with self.subTest(outer=outer), self.assertRaises(ValueError):
                self.cleanup(copy.deepcopy(self.value), outer=outer)

class FailureDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_predecessor_uses_real_acquisition_and_rejects_permissive_admission(self):
        import ast
        from types import SimpleNamespace

        source = ROOT / 'scripts/automation_baseline_contract_acceptance.py'
        tree = ast.parse(source.read_text())
        request = next(node for node in ast.walk(tree)
                       if isinstance(node, ast.AsyncFunctionDef) and node.name == 'request')
        telemetry = SimpleNamespace(caller_id=None, ha_request_count=0, core_dispatch_authorizer=None)
        for grants, hides_success in ((False, False), (True, False), (True, True)):
            with self.subTest(grants=grants, hides_success=hides_success):
                acquired = []
                class Runtime:
                    def acquire(self, requirements):
                        acquired.append(requirements)
                        return object() if grants else None
                    def consume(self, lease): return object()
                    def revalidate(self, lease, commits): return True
                    def finish(self, commits): return True
                class Tool:
                    async def run(self, arguments):
                        success = telemetry.core_dispatch_authorizer() and not hides_success
                        return json.dumps({'success': success, 'details': {'reason': 'authority_unavailable'}})
                namespace = dict(begin_request=lambda: (telemetry, None), end_request=lambda token: None,
                                 c=c, patch=patch, AUTOMATION_BASELINE_CAPTURE=SimpleNamespace(service=None),
                                 registered_tool=Tool, json=json)
                exec(compile(ast.Module(body=[request], type_ignores=[]), str(source), 'exec'), namespace)
                result = await namespace['request'](Runtime(), object(), {})
                self.assertEqual(acquired, [c.REQUIREMENTS])
                if grants:
                    with self.assertRaises(AssertionError): lane.verify_predecessor(*result)
                else:
                    self.assertEqual(lane.verify_predecessor(*result),
                                     {'acquired': False, 'ha_requests': 0, 'reason': 'authority_unavailable'})

    async def test_failure_retains_locations_without_exception_or_argument_content(self):
        private = "synthetic-do-not-retain"
        with patch.object(lane, "_run_disposable", side_effect=AssertionError(private)):
            with self.assertRaises(AssertionError) as found:
                await lane.run_disposable(private, expected_image=private, core_version=private)
        self.assertEqual(found.exception.contract_missing_key, "automation_baseline")
        diagnostic = found.exception.contract_diagnostic
        self.assertTrue(diagnostic["baseline_source_lines"])
        self.assertTrue(all(type(line) is int for line in diagnostic["baseline_source_lines"]))
        self.assertNotIn(private, json.dumps(diagnostic))


    async def test_lane_refuses_unknown_core_wrong_lane_image_or_remote_host_before_reads(self):
        from alarmo_inspection_contract_acceptance import slot_binding
        binding = slot_binding(CORE)
        local = SimpleNamespace(ha_url='http://127.0.0.1:8123')
        cases = [
            (local, binding['image'], OTHER_CORE, binding['lane']),
            (local, binding['image'], CORE, slot_binding(OTHER_CORE)['lane']),
            (local, binding['image'], CORE, None),
            (local, slot_binding(OTHER_CORE)['image'], CORE, binding['lane']),
            (local, binding['image'].split('@')[0], CORE, binding['lane']),
            (SimpleNamespace(ha_url='http://homeassistant.local:8123'), binding['image'], CORE, binding['lane']),
        ]
        for configured, image, version, lane_name in cases:
            environ = {k: v for k, v in os.environ.items() if k not in ('HA_CONTRACT_LANE', 'REAL_HA_ALARMO_IMAGE_RESULT')}
            if lane_name is not None:
                environ['HA_CONTRACT_LANE'] = lane_name
            with self.subTest(image=image, version=version, lane=lane_name, url=configured.ha_url), \
                    patch.dict(os.environ, environ, clear=True), \
                    self.assertRaisesRegex(ValueError, 'Exact owned disposable baseline lane required'):
                # No image receipt is configured, so passing the guard would raise KeyError instead.
                await lane._run_disposable(configured, expected_image=image, core_version=version)
        for unknown in [*NOT_SLOTS, *NOT_STRINGS]:
            environ = {k: v for k, v in os.environ.items() if k != 'REAL_HA_ALARMO_IMAGE_RESULT'}
            environ['HA_CONTRACT_LANE'] = binding['lane']
            with self.subTest(unknown=unknown), patch.dict(os.environ, environ, clear=True), \
                    self.assertRaisesRegex(ValueError, 'exact support-window Core slot'):
                await lane._run_disposable(local, expected_image=binding['image'], core_version=unknown)

class FixtureTests(unittest.TestCase):
    def test_fixture_is_inert_and_changes_only_three_selected_ids(self):
        before={r['id']:r for r in synthetic.rows()}
        after={r['id']:r for r in synthetic.rows(True)}
        self.assertEqual(len(before),121)
        self.assertEqual(set(before)-set(after),{lane.PREFIX+'001'})
        self.assertEqual(set(after)-set(before),{lane.PREFIX+'121'})
        self.assertEqual([key for key in before.keys() & after.keys() if before[key]!=after[key]],[lane.PREFIX+'000'])
        self.assertFalse(before[lane.PREFIX+'000']['initial_state'])
        for row in [*before.values(),*after.values()]:
            self.assertEqual(row['actions'],[{'delay':'00:00:00'}])
            self.assertEqual(row['conditions'],[{'condition':'template','value_template':'{{ false }}'}])
            self.assertEqual(row['triggers'],[{'trigger':'event','event_type':'native_baseline_never_fired'}])

    def test_existing_alarmo_predecessor_stays_nineteen_plus_one(self):
        from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
        # HAMCP-069A: the dormant entity metadata profile is outside both lanes.
        refs=[p.capability_id for p in CORE_RUNTIME_CAPABILITY_PROFILES
              if p.capability_id not in {c.CAPABILITY,'core.entity_registry_metadata_operation'}]
        self.assertEqual(len(refs),20)
        self.assertEqual(len([r for r in refs if r!='core.integration_inspection_metadata_read']),19)
