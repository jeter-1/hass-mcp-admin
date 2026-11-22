"""HAMCP-443 D3 offline contracts: set equality against the authenticated baseline."""
import asyncio
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import core_evidence_workflow as w
import core_lane_proof as proof
import core_release_evidence as e
import created_object_contract_acceptance
import device_configuration_contract_acceptance
import helper_value_contract_acceptance
import marketplace_read_contract_acceptance
import security_metadata_contract_acceptance
import thread_matter_topology_a2_acceptance
import thread_matter_topology_contract_acceptance

JOURNAL = ROOT / 'upstream-trust/ha-core-release-registry.json'


def signed_entry(version='2026.11.0'):
    signed, _ = w.journal(JOURNAL.read_bytes())
    return next(x.to_mapping() for x in signed.accepted.entries if x.version == version)


def provenance():
    return e.read_json(ROOT / 'tests/fixtures/core_2026_11_0_lane_provenance.json')


def context(case):
    value = {'provenance': provenance(), 'fixture': case == 'fixture',
             'mode': 'main' if case == 'coverage' else 'derived',
             'predecessor': '2026.11.0' if case in ('coverage', 'release') else '2026.10.4',
             'proof': None if case == 'coverage' else {'predecessor_commit': provenance()['source_commit']}}
    if case == 'release':
        value['provenance'] = {**provenance(), 'version': '2026.12.0', 'source_commit': 'd' * 40,
                               'entry_id': 'core-2026.12.0-dddddddd'}
    return value


def refs_of(entry, group):
    wanted = e.groups()[group]
    return [r for r in entry['capabilities'] if r['capability_id'] in wanted]


class Transition(unittest.TestCase):
    def setUp(self):
        self.b = signed_entry()

    def test_every_lane_group_is_baseline_minus_group_then_compiled(self):
        for group in e.LANE_GROUPS:
            with self.subTest(group=group):
                old, new = proof.transition(self.b, group, refs_of(self.b, group))
                self.assertEqual(len(old), 37); self.assertEqual(len(new), 38)
                self.assertEqual(old, [r for r in self.b['capabilities'] if r not in refs_of(self.b, group)])
                self.assertEqual(new, old + refs_of(self.b, group))

    def test_cross_group_predecessor_keeps_the_other_seq18_profiles(self):
        # F5: the derived predecessor carries the six other lane-group profiles.
        old, _ = proof.transition(self.b, 'helper_value', refs_of(self.b, 'helper_value'))
        ids = {r['capability_id'] for r in old}
        for group in set(e.LANE_GROUPS) - {'helper_value'}:
            self.assertTrue(set(e.groups()[group]) <= ids, group)

    def test_rule1_group_absent_from_baseline_refuses(self):
        b = copy.deepcopy(self.b)
        b['capabilities'] = [r for r in b['capabilities'] if r['capability_id'] != 'core.marketplace_read']
        with self.assertRaisesRegex(proof.ProofRefusal, 'group_not_in_baseline'):
            proof.verify_transition(b, 'marketplace_read', b['capabilities'],
                                    b['capabilities'] + refs_of(self.b, 'marketplace_read'))

    def test_rule2_predecessor_dropping_another_groups_profile_refuses(self):
        old, new = proof.transition(self.b, 'helper_value', refs_of(self.b, 'helper_value'))
        dropped = [r for r in old if r['capability_id'] != 'core.marketplace_read']  # the 32-cap-era shape
        with self.assertRaisesRegex(proof.ProofRefusal, 'predecessor_not_baseline_minus_group'):
            proof.verify_transition(self.b, 'helper_value', dropped, dropped + refs_of(self.b, 'helper_value'))

    def test_rule2_predecessor_wrongly_keeping_a_profile_refuses(self):
        old, _ = proof.transition(self.b, 'helper_value', refs_of(self.b, 'helper_value'))
        kept = old + refs_of(self.b, 'helper_value')  # the group's own profile not removed
        foreign = old + [{**refs_of(self.b, 'marketplace_read')[0], 'capability_id': 'core.not_signed'}]
        duplicated = old + refs_of(self.b, 'marketplace_read')  # another group's profile kept twice
        for wrong in (kept, foreign, duplicated):
            with self.subTest(n=len(wrong)), self.assertRaisesRegex(proof.ProofRefusal,
                                                                     'predecessor_not_baseline_minus_group'):
                proof.verify_transition(self.b, 'helper_value', wrong, wrong + refs_of(self.b, 'helper_value'))

    def test_rule2_signed_order_and_reference_level(self):
        old, _ = proof.transition(self.b, 'security_metadata', refs_of(self.b, 'security_metadata'))
        swapped = [old[1], old[0], *old[2:]]
        changed = copy.deepcopy(old); changed[0]['contract_fingerprint'] = 'sha256:' + '0' * 64
        for wrong in (swapped, changed):
            with self.subTest(), self.assertRaisesRegex(proof.ProofRefusal, 'predecessor_not_baseline_minus_group'):
                proof.verify_transition(self.b, 'security_metadata', wrong, wrong + refs_of(self.b, 'security_metadata'))

    def test_rule3_compiled_reference_must_equal_the_signed_one(self):
        compiled = copy.deepcopy(refs_of(self.b, 'helper_value'))
        compiled[0]['contract_fingerprint'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(proof.ProofRefusal, 'extended_not_predecessor_plus_compiled'):
            proof.transition(self.b, 'helper_value', compiled)

    def test_rule3_extended_must_be_predecessor_plus_exactly_the_group(self):
        old, new = proof.transition(self.b, 'helper_value', refs_of(self.b, 'helper_value'))
        extra = new + refs_of(self.b, 'marketplace_read')
        missing = list(old)
        reordered = [new[1], new[0], *new[2:]]
        for wrong in (extra, missing, reordered):
            with self.subTest(n=len(wrong)), self.assertRaisesRegex(proof.ProofRefusal,
                                                                     'extended_not_predecessor_plus_compiled'):
                proof.verify_transition(self.b, 'helper_value', old, wrong)

    def test_compiled_set_and_unknown_group_refuse(self):
        with self.assertRaisesRegex(proof.ProofRefusal, 'compiled_group_mismatch'):
            proof.transition(self.b, 'helper_value', refs_of(self.b, 'marketplace_read'))
        with self.assertRaisesRegex(proof.ProofRefusal, 'compiled_group_mismatch'):
            proof.transition(self.b, 'helper_value', refs_of(self.b, 'helper_value') * 2)
        for call in (lambda: proof.transition(self.b, 'general_extra', []),
                     lambda: proof.transition(self.b, 'general_extra', refs_of(self.b, 'helper_value')),
                     lambda: proof.verify_transition(self.b, 'general_extra', [], [])):
            with self.assertRaisesRegex(proof.ProofRefusal, 'unknown_group'):
                call()

    def test_duplicate_baseline_profile_refuses(self):
        b = copy.deepcopy(self.b); b['capabilities'].append(copy.deepcopy(b['capabilities'][0]))
        with self.assertRaisesRegex(proof.ProofRefusal, 'baseline_duplicate_profile'):
            proof.verify_transition(b, 'helper_value', [], [])

    def test_canonical_equality_rejects_type_confusion(self):
        self.assertFalse(proof.same({'a': True}, {'a': 1}))
        self.assertFalse(proof.same([1], [1.0]))

    def test_release_entry_keeps_semantics_under_context_provenance(self):
        ctx = context('release')
        entry = proof.release_entry(self.b, ctx)
        self.assertEqual(entry['capabilities'], self.b['capabilities'])
        self.assertEqual({k: entry[k] for k in ctx['provenance']}, ctx['provenance'])


class Baseline(unittest.TestCase):
    def prepared(self, tmp, baseline, raw=None):
        directory = Path(tmp)
        (directory / 'predecessor.json').write_bytes(JOURNAL.read_bytes() if raw is None else raw)
        w.write(directory / 'baseline.json', baseline)
        return directory

    def test_each_case_admits_its_signed_baseline(self):
        for case in ('coverage', 'fixture', 'release'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                directory = self.prepared(tmp, signed_entry())
                self.assertEqual(proof.authenticated_baseline(context(case), directory), signed_entry())

    def test_unsigned_or_tampered_baseline_refuses(self):
        tampered = signed_entry(); tampered['capabilities'] = tampered['capabilities'][:-1]
        for value in (tampered, {**signed_entry(), 'entry_id': 'forged'}):
            with self.subTest(), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(proof.ProofRefusal, 'baseline_not_signed'):
                    proof.authenticated_baseline(context('coverage'), self.prepared(tmp, value))

    def test_tampered_journal_refuses(self):
        raw = JOURNAL.read_bytes().replace(b'2026.11.0-reviewed-v1', b'2026.11.0-reviewed-v2', 1)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(RuntimeError, 'registry_invalid_signature'):
            proof.authenticated_baseline(context('coverage'), self.prepared(tmp, signed_entry(), raw))

    def test_wrong_version_provenance_or_commit_refuses(self):
        cases = [('coverage', signed_entry('2026.10.3'), 'baseline_version_mismatch'),
                 ('release', signed_entry('2026.10.3'), 'baseline_version_mismatch')]
        for case, baseline, code in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(proof.ProofRefusal, code):
                    proof.authenticated_baseline(context(case), self.prepared(tmp, baseline))
        for case in ('coverage', 'fixture'):
            ctx = context(case); ctx['provenance']['source_tree'] = 'e' * 40
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(proof.ProofRefusal, 'baseline_provenance_mismatch'):
                    proof.authenticated_baseline(ctx, self.prepared(tmp, signed_entry()))
        ctx = context('release'); ctx['proof']['predecessor_commit'] = 'f' * 40
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(proof.ProofRefusal, 'baseline_commit_mismatch'):
                proof.authenticated_baseline(ctx, self.prepared(tmp, signed_entry()))

    def test_default_directory_is_the_bound_context_artifact(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.prepared(tmp, signed_entry())
            with patch.dict(os.environ, {e.CONTEXT_ENV: str(directory / 'coverage.json')}):
                self.assertEqual(proof.authenticated_baseline(context('coverage')), signed_entry())


class Reached(Exception):
    pass


class AcceptanceCiMode(unittest.TestCase):
    """Every lane's CI mode starts from transition(): rules 1-3 on the authenticated entry with exactly
    its group's compiled reference, before any client (``configured`` is None here, so any earlier use
    of it fails differently)."""

    A2_CALLBACKS = dict.fromkeys(('anchor', 'expectations', 'read_stub_record', 'read_devices',
                                  'stub_control', 'stub_state', 'reload_matter'))
    CASES = [(helper_value_contract_acceptance, 'helper_value', {}),
             (marketplace_read_contract_acceptance, 'marketplace_read', {}),
             (security_metadata_contract_acceptance, 'security_metadata', {}),
             (device_configuration_contract_acceptance, 'device_configuration_entity',
              dict.fromkeys(('stub_url', 'stub_hashes', 'upstream_version'))),
             (thread_matter_topology_contract_acceptance, 'thread_matter_topology', {}),
             (thread_matter_topology_a2_acceptance, 'thread_matter_topology', A2_CALLBACKS)]
    LANES = ('scene_create_delete', 'created_helper_delete')

    def test_every_ci_acceptance_proves_its_transition_before_any_client(self):
        entry = {'version': '2026.11.0', 'capabilities': []}
        for module, group, extra in self.CASES:
            reference = (thread_matter_topology_contract_acceptance.reference
                         if module is thread_matter_topology_a2_acceptance else module.reference)
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(module, 'transition', side_effect=Reached) as transition:
                with self.assertRaises(Reached):
                    asyncio.run(module.run(None, entry, Path(tmp), mode='ci', **extra))
                transition.assert_called_once_with(entry, group, [reference()])
        for lane in self.LANES:
            module = created_object_contract_acceptance
            with self.subTest(lane=lane), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(module, 'transition', side_effect=Reached) as transition:
                with self.assertRaises(Reached):
                    asyncio.run(module.run(lane, None, entry, Path(tmp), mode='ci'))
                transition.assert_called_once_with(entry, lane, [module.reference(lane)])

    def test_an_unknown_mode_refuses_before_the_transition(self):
        runs = [(module.__name__,
                 lambda tmp, module=module, extra=extra: module.run(None, {}, tmp, mode='main', **extra), module)
                for module, _, extra in self.CASES]
        module = created_object_contract_acceptance
        runs += [(lane, lambda tmp, lane=lane: module.run(lane, None, {}, tmp, mode='main'), module)
                 for lane in self.LANES]
        for name, call, module in runs:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(module, 'transition', side_effect=Reached) as transition:
                with self.assertRaisesRegex(ValueError, 'lane_mode_unknown'):
                    asyncio.run(call(Path(tmp)))
                transition.assert_not_called()

    def test_a2_refuses_an_unreviewed_anchor_right_after_the_transition(self):
        # D5: A2's identifier regex is keyed to the anchor, checked before any Core command.
        module = thread_matter_topology_a2_acceptance
        entry = {'version': '2026.11.0', 'capabilities': []}
        callbacks = {**self.A2_CALLBACKS, 'anchor': '2026.10.0', 'expectations': {'canaries': ()}}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(module, 'transition', return_value=([], [])) as transition:
            with self.assertRaisesRegex(ValueError, 'lane_anchor_unreviewed'):
                asyncio.run(module.run(None, entry, Path(tmp), mode='ci', **callbacks))
        transition.assert_called_once_with(entry, 'thread_matter_topology',
                                           [thread_matter_topology_contract_acceptance.reference()])


if __name__ == '__main__':
    unittest.main()
