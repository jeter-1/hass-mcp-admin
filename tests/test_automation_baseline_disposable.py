"""Offline controls for the required assembled-Core receipt, not Core execution."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'hass_mcp_engineering_beta')]
import automation_baseline_contract_acceptance as lane
from test_automation_baseline_capture import Client, Core, fixture, export
from test_alarmo_interval_observer import receipt
from ha_mcp_engineering.audit_baseline import capture_contracts as c, compare_baselines, load_baseline
from ha_mcp_engineering.audit_baseline.capture_provider import BaselineCaptureProvider
from ha_mcp_engineering.audit_baseline.capture_service import BaselineCaptureService
from ha_mcp_engineering.request_context import begin_request, end_request

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
        observations = [receipt(['other','config_entries/get','other','other'] * 2) for _ in range(2)]
        for item in observations: item['kind'] = 'control'
        self.value = {'result':'PASS','core':'2026.9.4','production_authority':False,
            'fixture_cleanup':True,'authority_transition':[20,21], 'continuation_reads':0,
            'nonadmin_refusal':'access_denied','missing_authority_reads':0,
            'recreated_anchor_differs':True,'baselines':[before,after],
            'baseline_sha256':[c.digest(before),c.digest(after)],
            'comparison_counts':counts,'observations':observations}

    def tearDown(self):
        end_request(self.token)

    async def test_reconstructs_actual_export_and_all_five_classifications(self):
        result = lane.verify_result(self.value)
        self.assertEqual(result['records_before'], 122)
        self.assertEqual(result['comparison_counts'], {'ADDED':1,'REMOVED':1,'CHANGED':1,'UNCHANGED':119,'UNKNOWN':1,'TOTAL':123})

    async def test_rejects_wrong_digest_order_comparison_or_missing_execution(self):
        for mutate in (
            lambda v: v['baseline_sha256'].__setitem__(0, '0'*64),
            lambda v: v['comparison_counts'].update(REMOVED=0),
            lambda v: v.update(baselines=list(reversed(v['baselines'])), baseline_sha256=list(reversed(v['baseline_sha256']))),
            lambda v: v.update(result='NOT_RUN'),
            lambda v: v.update(continuation_reads=1),
            lambda v: v.update(fixture_cleanup=False),
            lambda v: v.update(production_authority=True),
            lambda v: v.update(authority_transition=[19,20]),
            lambda v: v.update(observations=[]),
        ):
            value=copy.deepcopy(self.value);mutate(value)
            with self.assertRaises((AssertionError, ValueError, KeyError)):
                lane.verify_result(value)

    async def test_command_service_storage_and_coverage_fail_closed(self):
        for mutate in (
            lambda v: v['observations'][0]['events'].append({'kind':'service','origin':'command'}),
            lambda v: v['observations'][0]['events'].pop(),
            lambda v: v['observations'][0]['coverage'].update({'Store.async_save':False}),
            lambda v: v['observations'][0]['final_store_hashes'].update({'core.device_registry':'1'*64}),
        ):
            value=copy.deepcopy(self.value);mutate(value)
            with self.assertRaises((AssertionError, ValueError)):
                lane.verify_result(value)


class FailureDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_retains_locations_without_exception_or_argument_content(self):
        private = "synthetic-do-not-retain"
        with patch.object(lane, "_run_disposable", side_effect=AssertionError(private)):
            with self.assertRaises(AssertionError) as found:
                await lane.run_disposable(private, expected_image=private)
        self.assertEqual(found.exception.contract_missing_key, "automation_baseline")
        diagnostic = found.exception.contract_diagnostic
        self.assertTrue(diagnostic["baseline_source_lines"])
        self.assertTrue(all(type(line) is int for line in diagnostic["baseline_source_lines"]))
        self.assertNotIn(private, json.dumps(diagnostic))


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
        refs=[p.capability_id for p in CORE_RUNTIME_CAPABILITY_PROFILES if p.capability_id!=c.CAPABILITY]
        self.assertEqual(len(refs),20)
        self.assertEqual(len([r for r in refs if r!='core.integration_inspection_metadata_read']),19)
