"""Offline controls for the closed September/October 8.6 disposable campaign."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import yaml
from test_ha_mcp_850_candidate_lane import lane
import test_upstream_8_6_compatibility as compatibility

ROOT = Path(__file__).resolve().parents[1]
GUARD_ENV = dict(GITHUB_ACTIONS='true', GITHUB_REPOSITORY='jeter-1/hass-mcp-admin',
                 GITHUB_REF='refs/pull/999/merge', GITHUB_EVENT_NAME='pull_request',
                 GITHUB_JOB='exact-addon-runtime-acceptance', GITHUB_SHA='a' * 40,
                 GITHUB_RUN_ID='42', GITHUB_RUN_ATTEMPT='1')


class PairTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(GUARD_ENV)

    def test_all_eight_exact_pairs_have_distinct_owned_resources(self):
        identities = set()
        for core in ('2026.9.4', '2026.10.0'):
            for profile in ('reference', 'component'):
                for arch in ('amd64', 'arm64'):
                    identity = lane.execution_guard(self.env, arch, '8.6.0', core, profile)
                    identities.add(identity)
                    self.assertEqual(len(lane.resource_names(identity)), 4)
                    pins = lane.candidate_pins('8.6.0', core, profile)
                    self.assertIn(core + '@sha256:', pins['core_image'])
                    self.assertEqual(pins['profile'], profile)
                    for image in pins['images'][arch].values():
                        for name in ('manifest', 'configuration', 'index'):
                            self.assertRegex(image[name], r'^sha256:[a-f0-9]{64}$')
                        self.assertTrue(image['image'].endswith('@' + image['index']))
        self.assertEqual(len(identities), 8)

    def test_unreviewed_pairs_and_profiles_cannot_select_a_resource(self):
        cases = [('8.6.0', '2026.10.1', 'reference'), ('8.6.1', '2026.10.0', 'reference'),
                 ('8.6.0', '2026.9.3', 'reference'), ('8.6.0', '2026.10.0', 'legacy'),
                 ('8.5.0', '2026.10.0', 'component'), ('8.5.0', '2026.9.3', 'component'),
                 ('8.6.0', '2026.10.0', '../reference')]
        for version, core, profile in cases:
            with self.subTest(case=(version, core, profile)), self.assertRaises(lane.Refusal):
                lane.execution_guard(self.env, 'amd64', version, core, profile)

    def test_real_ci_guard_is_not_replaced_by_local_driver(self):
        for field, value in [('GITHUB_ACTIONS', 'false'), ('GITHUB_REF', 'refs/heads/compat/ha-mcp-8.6.0'),
                             ('GITHUB_REPOSITORY', 'other/repo'), ('GITHUB_JOB', 'validate')]:
            with self.subTest(field=field), self.assertRaises(lane.Refusal):
                lane.execution_guard({**self.env, field: value}, 'amd64', '8.6.0', '2026.10.0', 'reference')
        with self.assertRaises(lane.Refusal):
            lane.resource_names('h860c11r-1234-1-amd64')

    def test_reference_catalog_must_match_selected_profile_and_complete_identity(self):
        tools = compatibility.capture()['tools']
        lane.validate_860_catalog(tools, 'reference')
        lane.validate_860_catalog(compatibility.component_capture()['tools'], 'component')
        for profile, changed in [('component', tools), ('reference', tools[:-1]),
                                 ('reference', [*tools, {'name': 'unreviewed_write'}])]:
            with self.subTest(profile=profile, length=len(changed)), self.assertRaises(lane.Refusal):
                lane.validate_860_catalog(changed, profile)
        changed = deepcopy(tools)
        changed[0]['description'] += ' drift'
        with self.assertRaises(lane.Refusal):
            lane.validate_860_catalog(changed, 'reference')

    def test_october_keeps_september_authority_and_has_distinct_public_pin(self):
        september = lane.candidate_pins('8.6.0', '2026.9.4', 'reference')
        october = lane.candidate_pins('8.6.0', '2026.10.0', 'reference')
        self.assertEqual(september['images'], october['images'])
        self.assertNotEqual(september['core_image'], october['core_image'])
        self.assertTrue(october['core_image'].endswith('sha256:1b64d38f38d922bf9d59336451fd6453e1d614f934456af4ee3d2a51061be3a4'))


class AuthorityHelperTests(unittest.TestCase):
    @staticmethod
    def module(name):
        import importlib.util
        spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_dashboard_attestation_is_bound_to_final_compiled_evidence(self):
        helper = self.module('exact_8_4_1_dashboard_quarantine_acceptance')
        release = compatibility.load_reviewed_upstream_release_registry().by_version['8.6.0']
        expected = helper.EXACT_RELEASES['8.6.0']
        self.assertEqual(expected['entry_id'], release.entry_id)
        self.assertEqual(expected['source_commit'], release.source_commit)
        self.assertEqual(expected['image_index_digest'], release.image_index_digest)
        self.assertEqual(expected['attestation_fingerprint'], release.dashboard_attestation_fingerprint)
        self.assertEqual(helper.EXPECTED_CONSTRAINTS_FINGERPRINT, release.dashboard_compiled_constraints_fingerprint)

    def test_readmission_requires_exact_860_counts_and_zero_fallback(self):
        helper = self.module('exact_image_readmission_acceptance')
        health = {name: 0 for name in helper.ZERO_ADMISSION_COUNTERS}
        health.update(admission_status='admitted_exact', selected_compatibility_entry_id='ha-mcp-v8.6.0-7426fb16',
                      observed_protocol_version='2025-03-26', observed_advertised_tool_count=77,
                      reviewed_accounted_tool_count=77, reviewed_tool_accounting_valid=True,
                      exact_matched_automatic_read_count=25, dynamically_exposed_count=25, held_read_count=1)
        observation = dict(success=True, provider='upstream_read_gateway', upstream_version='8.6.0',
                           fallback='none', fallback_occurred=False, engineering_tool_count=helper.EXPECTED_ENGINEERING_LOCAL_TOOL_COUNT + 25,
                           engineering_local_tool_count=helper.EXPECTED_ENGINEERING_LOCAL_TOOL_COUNT,
                           gateway_health=health, held_tools_absent=True)
        self.assertTrue(helper.exact_readmission_observed(observation, expected_upstream_version='8.6.0'))
        for field, value in [('reviewed_accounted_tool_count', 78), ('dynamically_exposed_count', 24),
                             ('fallback_count', 1), ('held_read_count', 0),
                             ('selected_compatibility_entry_id', 'ha-mcp-v8.5.0-e1538bcd')]:
            with self.subTest(field=field):
                altered = {**observation, 'gateway_health': {**health, field: value}}
                self.assertFalse(helper.exact_readmission_observed(altered, expected_upstream_version='8.6.0'))


class DependencyTests(unittest.TestCase):
    """Synthetic wheel contents exercise the same bounded extractor, offline."""
    def contract(self, *, name='package/module.py', duplicate=False):
        bodies = {}
        distributions = []
        for index, (package, version) in enumerate([('ruamel-yaml', '0.19.1'), ('voluptuous-openapi', '0.4.1')]):
            member = name if index == 0 or duplicate else 'second/module.py'
            content = b'synthetic package bytes\n'
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, 'w') as archive:
                archive.writestr(member, content)
            body = raw.getvalue()
            url = f'https://files.pythonhosted.org/packages/synthetic{index}-py3-none-any.whl'
            bodies[url] = body
            distributions.append({'name': package, 'version': version,
                'wheel': {'url': url, 'size': len(body), 'hash': 'sha256:' + hashlib.sha256(body).hexdigest()},
                'members': {member: hashlib.sha256(content).hexdigest()}})
        return {'dependencies': distributions}, bodies

    def execute(self, contract, bodies, target):
        class Response(io.BytesIO):
            def __init__(self, url):
                self.url = url
                super().__init__(bodies[url])
            def geturl(self):
                return self.url
        with patch.object(lane.json, 'loads', return_value=contract), patch('urllib.request.urlopen', side_effect=lambda url, timeout: Response(url)):
            return lane.prepare_component_dependencies(target)

    def test_exact_members_extract_and_hash_before_use(self):
        contract, bodies = self.contract()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'dependencies'
            self.assertEqual(self.execute(contract, bodies, target), target)
            self.assertEqual((target / 'package/module.py').read_bytes(), b'synthetic package bytes\n')
            self.assertEqual(len(list(target.rglob('*.py'))), 2)

    def test_wrong_wheel_hash_and_extra_members_refuse(self):
        for defect in ('hash', 'members'):
            contract, bodies = self.contract()
            if defect == 'hash':
                contract['dependencies'][0]['wheel']['hash'] = 'sha256:' + '0' * 64
            else:
                contract['dependencies'][0]['members']['extra.py'] = '0' * 64
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as directory, self.assertRaises(lane.Refusal):
                self.execute(contract, bodies, Path(directory) / 'dependencies')

    def test_paths_collisions_hosts_and_unpinned_versions_refuse(self):
        for defect in ('traversal', 'absolute', 'collision', 'host', 'version'):
            contract, bodies = self.contract(name='../escape.py' if defect == 'traversal' else '/escape.py' if defect == 'absolute' else 'package/module.py', duplicate=defect == 'collision')
            if defect == 'host': contract['dependencies'][0]['wheel']['url'] = 'https://example.com/x.whl'
            if defect == 'version': contract['dependencies'][0]['version'] = 'latest'
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as directory, self.assertRaises(lane.Refusal):
                self.execute(contract, bodies, Path(directory) / 'dependencies')


class WorkflowTests(unittest.TestCase):
    def test_matrix_runs_both_architectures_core_versions_profiles_and_cleanup(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
        job = workflow['jobs']['exact-addon-runtime-acceptance']
        rows = [row for row in job['strategy']['matrix']['include'] if row['upstream_version'] == '8.6.0']
        self.assertEqual({(row['architecture'], row['candidate_core_version'], row['candidate_profile']) for row in rows},
                         {(arch, core, profile) for arch in ('amd64', 'arm64') for core in ('2026.9.4', '2026.10.0') for profile in ('reference', 'component')})
        self.assertEqual(len(rows), 8)
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        for step in job['steps']:
            if 'scripts/ha_mcp_850_container_acceptance.py' in step.get('run', ''):
                self.assertIn('--profile "$ASSESSMENT_PROFILE"', step['run'])
        private = next(step for step in job['steps'] if step.get('name') == 'Remove disposable private runner files')
        self.assertEqual(private['if'], 'always() && matrix.candidate_power == true')
        self.assertIn('2026.10.0:8.6.0) code=860c10', private['run'])
        self.assertIn('8.6.0:component) code="${code}c"', private['run'])
        for row in rows:
            identity = lane.execution_guard(GUARD_ENV, row['architecture'], '8.6.0', row['candidate_core_version'], row['candidate_profile'])
            self.assertTrue(identity.startswith('h' + row['candidate_resource_code'] + '-'))


if __name__ == '__main__':
    unittest.main()
