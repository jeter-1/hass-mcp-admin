"""HAMCP-371 S7: real-Core receipts, the output-key contract and their replay.

The S6 lane recorded every read against a disposable Home Assistant Core with
ha-mcp 9.1.2 and 9.2.0 (profile A without and profile B with the ha_mcp_tools
component). The receipts are committed under docs/evidence/hamcp371/s6/ and are
never edited; tests/fixtures/ha_mcp_9_2_0/ holds the reviewed output-key
contract generated from them, its provenance and the hashes it binds.

- Integrity: every bound receipt and index matches its hash, and the generator
  reproduces the committed contract byte for byte.
- Contract: Engineering-added names never collide with an upstream key, policy
  drops and unreviewed 9.2.0 keys are never admitted, ruled keys are admitted.
- 9.1.2 replay: each real-Core receipt replayed through the real gateway on the
  shipped 9.1.2 route sends the recorded wire arguments and succeeds, except the
  designed failures, which fail closed without upstream text.
- 9.2.0 replay (ruling 2, items 7-15, N29): these fail by design until S8 to
  S10 land the 9.2.0 capture, admission and closed key sets (step table S7).

Offline and deterministic; no live Home Assistant, no network.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hass_mcp_engineering_beta'), str(ROOT / 'tests')]
from mcp.server.fastmcp import FastMCP
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
from ha_mcp_engineering.request_context import begin_request, end_request
from test_ha_mcp_production_readmission import _GatewayTransport, _settings
import test_upstream_8_5_compatibility as previous

FIXTURES = ROOT / 'tests/fixtures/ha_mcp_9_2_0'
CONTRACT = json.loads((FIXTURES / 'output-key-contract.json').read_text(encoding='utf-8'))
PROVENANCE = json.loads((FIXTURES / 'provenance.json').read_text(encoding='utf-8'))
SUMS = json.loads((FIXTURES / 'SHA256SUMS.json').read_text(encoding='utf-8'))
GENERATOR_PATH = ROOT / CONTRACT['generator']
_spec = importlib.util.spec_from_file_location('hamcp371_output_key_generator', GENERATOR_PATH)
generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generator)

READS = CONTRACT['reads']
ADDED = frozenset(CONTRACT['rules']['engineering_added'])
REPLACED = CONTRACT['rules']['replaced_warning_code']
CALL_DIRS = ('calls', 'calls-after-core-restart')
# Designed or environment failures recorded identically on 9.1.2 and 9.2.0.
# r27-apps: the bare Core has no Supervisor (inference; SR records the body).
EXPECTED_FAILURES = {
    'r27-apps': 'upstream_error',
    'p4-absent-automation': 'automation_not_found',
    'p4-absent-state': 'entity_not_found',
    'p4-absent-entity': 'entity_not_found',
}
INJECTED = 'hamcp371_injected_key'
CANARY = 'hamcp371 unlisted upstream warning canary'
NON_ASCII = 'HAMCP371 groupe éü 雪'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def receipt(path):
    """A committed receipt, after its sha256 is checked against SHA256SUMS.json."""
    full = ROOT / path
    if sha(full) != SUMS[path]:
        raise AssertionError(f'receipt hash drift: {path}')
    return json.loads(full.read_text(encoding='utf-8'))


def public_calls(set_name, sub='calls', kinds=('public',)):
    base = CONTRACT['receipt_sets'][set_name] + f'/{sub}'
    index = receipt(f'{base}/_index.json')
    for call in index['calls']:
        if call['kind'] in kinds:
            yield call, receipt(f'{base}/{call["file"]}')


def all_keys(value, acc=None):
    """Every dict key at any depth, open containers included."""
    acc = set() if acc is None else acc
    if isinstance(value, dict):
        for k, x in value.items():
            acc.add(k)
            all_keys(x, acc)
    elif isinstance(value, list):
        for x in value:
            all_keys(x, acc)
    return acc


class _Transport(_GatewayTransport):
    def __init__(self, tools, version):
        super().__init__(tools, version=version)
        self.sent = []
        self.override = None

    async def execute_read(self, tool, arguments, **kwargs):
        exchange = await super().execute_read(tool, arguments, **kwargs)
        self.sent.append((tool, deepcopy(arguments)))
        return replace(exchange, call_result=deepcopy(self.override))


async def _replay(version, cases, tools=None):
    """[(public tool, public arguments, upstream result)] -> [(output, sent)].

    ``tools`` defaults to the stock registry capture (no optional tools).
    """
    if tools is None:
        tools = previous.capture(version)['tools']
    transport = _Transport(tools, version)
    gateway = UpstreamReadGateway()
    gateway.configure(
        replace(_settings('synthetic-no-key'), ha_mcp_release_registry_enabled=False),
        transport=transport,
    )
    await gateway.initialize(FastMCP('hamcp371-replay'))
    registry = gateway._registered_tool_registry.snapshot()
    outputs = []
    for tool, arguments, result in cases:
        transport.override = result
        transport.sent = []
        _telemetry, token = begin_request('hamcp371-replay')
        try:
            out = json.loads(await registry[tool].run(deepcopy(arguments)))
        finally:
            end_request(token)
        outputs.append((out, list(transport.sent)))
    return outputs


def replay(version, cases, tools=None):
    return asyncio.run(_replay(version, cases, tools))


class ReceiptIntegrity(unittest.TestCase):

    def test_every_bound_file_matches_its_hash_and_its_run_sums(self):
        self.assertGreater(len(SUMS), 150)
        self.assertTrue(any('/yaml-ordinary-user/' in path for path in SUMS))
        for path, digest in SUMS.items():
            with self.subTest(path=path):
                self.assertEqual(sha(ROOT / path), digest)
                run_dir, rel = path.split('/out/', 1)
                self.assertEqual(generator.run_sums(run_dir.rsplit('/', 1)[1])['out/' + rel], digest)

    def test_provenance_binds_each_successful_receipt(self):
        fixtures = PROVENANCE['fixtures']
        self.assertEqual(PROVENANCE['fixture_file'], 'tests/fixtures/ha_mcp_9_2_0/output-key-contract.json')
        self.assertGreater(len(fixtures), 100)
        for name, entry in fixtures.items():
            with self.subTest(name=name):
                self.assertEqual(entry['class'], 'C')
                self.assertEqual(entry['pointers'], ['/exchange/response/result'])
                self.assertEqual(SUMS[entry['receipt']], entry['sha256'])
                self.assertFalse(receipt(entry['receipt'])['exchange']['response']['result'].get('isError'))

    def test_source_files_are_the_reviewed_revisions(self):
        for path, digest in PROVENANCE['source_files'].items():
            with self.subTest(path=path):
                self.assertEqual(sha(ROOT / path), digest)

    def test_generator_reproduces_the_committed_fixture(self):
        for name, text in generator.outputs().items():
            with self.subTest(name=name):
                self.assertEqual((FIXTURES / name).read_text(encoding='utf-8'), text)

    def test_a_changed_receipt_is_refused(self):
        path = next(iter(SUMS))
        with self.assertRaises(AssertionError):
            original = SUMS[path]
            try:
                SUMS[path] = '0' * 64
                receipt(path)
            finally:
                SUMS[path] = original


class OutputKeyContract(unittest.TestCase):

    def test_twenty_seven_reads_numbered_once(self):
        self.assertEqual(sorted(r['number'] for r in READS.values()), list(range(1, 28)))

    def test_engineering_added_names_never_name_an_upstream_key(self):
        reserved = ADDED | {REPLACED}
        for path in SUMS:
            if path.endswith('_index.json'):
                continue
            with self.subTest(path=path):
                result = receipt(path)['exchange']['response']['result']
                self.assertFalse(all_keys(result) & reserved)
                self.assertNotIn(REPLACED, json.dumps(result))

    def test_policy_drops_and_added_names_are_never_admitted(self):
        for tool, row in READS.items():
            with self.subTest(tool=tool):
                self.assertFalse(set(row['admitted']) & set(row['policy_drops']))
                self.assertFalse(set(row['admitted']) & ADDED)

    def test_every_baseline_key_of_a_closed_read_is_admitted_with_its_source(self):
        for tool, row in READS.items():
            if row['projection'] != 'closed':
                continue
            for name in row['observed']['9.1.2']:
                if name in row['policy_drops'] or name in row['new_in_9_2_0']:
                    continue
                with self.subTest(tool=tool, name=name):
                    self.assertTrue(row['admitted'][name].startswith('receipt 9.1.2 profile'))

    def test_ruled_admissions(self):
        ruled = {
            'ha_get_zone': ('entity_icon', 'persons', 'device_trackers'),
            'ha_search': ('name',), 'ha_get_overview': ('name',),
        }
        for tool, names in ruled.items():
            for name in names:
                with self.subTest(tool=tool, name=name):
                    entry = READS[tool]['new_in_9_2_0'][name]
                    self.assertEqual(entry['status'], 'admitted')
                    self.assertTrue(entry['source'].startswith('research G ruling'))
                    self.assertIn(name, READS[tool]['admitted'])

    def test_unreviewed_9_2_0_keys_stay_withheld(self):
        pending = {
            'ha_list_services': ('response', 'optional', 'collapsed', 'filter', 'integration'),
            'ha_config_list_helpers': ('read_only', 'secret', 'later_forms'),
            'ha_get_integration': ('read_only', 'secret', 'later_forms'),
        }
        for tool, names in pending.items():
            for name in names:
                with self.subTest(tool=tool, name=name):
                    self.assertEqual(READS[tool]['new_in_9_2_0'][name]['status'], 'withheld_pending_review')
                    self.assertNotIn(name, READS[tool]['admitted'])
        for tool, row in READS.items():
            for name, entry in row['new_in_9_2_0'].items():
                with self.subTest(tool=tool, name=name):
                    self.assertEqual(entry['status'] == 'admitted', name in row['admitted'])

    def test_recorded_removals(self):
        self.assertEqual(READS['ha_list_services']['removed_in_9_2_0'], ['type'])
        self.assertEqual(READS['ha_get_zone']['removed_in_9_2_0'], ['friendly_name'])
        self.assertEqual(READS['ha_get_overview']['removed_in_9_2_0'], [])

    def test_device_row_records_the_per_occurrence_count(self):
        self.assertTrue(any('per occurrence' in note for note in READS['ha_get_device']['notes']))

    def test_names_skip_map_keys_open_containers_and_policy_drops(self):
        payload = {
            'services': {'light.turn_on': {'name': 'x', 'fields': {'brightness': {'selector': {'number': {}}}}}},
            'config': {'hamcp371_open': 1},
        }
        found = generator.names(payload, 'ha_list_services')
        self.assertEqual(found, {'services', 'name', 'fields', 'selector', 'config'})
        dropped = generator.names({'related': {'hamcp371_inner': 1}}, 'ha_config_get_automation')
        self.assertEqual(dropped, {'related'})
        # Without the map entry the data keys would leak in as names.
        self.assertIn('light.turn_on', generator.names(payload, 'ha_eval_template'))


class RealCoreReplay912(unittest.TestCase):

    def test_receipts_replay_on_the_shipped_route(self):
        for set_name in ('A/9.1.2', 'B/9.1.2'):
            for sub in CALL_DIRS:
                calls = list(public_calls(set_name, sub))
                outputs = replay('9.1.2', [
                    (c['public_tool'], c['public_arguments'], r['exchange']['response']['result'])
                    for c, r in calls
                ])
                for (call, rec), (out, sent) in zip(calls, outputs):
                    with self.subTest(set=set_name, sub=sub, call=call['id']):
                        self.assertEqual(sent, [(call['upstream_tool'], call['wire_arguments'])])
                        expected = EXPECTED_FAILURES.get(call['id'])
                        if expected is None:
                            self.assertIs(out['success'], True, out.get('details'))
                        else:
                            self.assertIs(out['success'], False)
                            self.assertEqual(out['details']['failure_category'], expected)
                            error = json.loads(rec['exchange']['response']['result']['content'][0]['text'])['error']
                            self.assertNotIn(error['message'], json.dumps(out))
                            self.assertNotIn(error['code'], json.dumps(out))

    def test_every_profile_b_read_has_a_receipt(self):
        tools = {c['public_tool'] for c, _ in public_calls('B/9.1.2')}
        self.assertEqual(tools & set(READS), set(READS))


# --- 9.2.0: fails by design until S8 (capture, admission) and S10 (key sets) ---

def _withheld_names(row, upstream):
    if row['projection'] == 'rebuild':
        return set(row['withheld_by_rebuild']) & upstream
    return upstream - set(row['admitted']) - set(row['policy_drops'])


def _first_row(content, tool):
    """A dict nested below the top level that the key walk descends into."""
    opened, maps, drops = generator.spec(tool)
    stack = [(content, 0)]
    while stack:
        value, depth = stack.pop(0)
        if isinstance(value, dict):
            if depth and value is not content:
                return value
            for k, x in value.items():
                if k not in opened and k not in drops and k not in maps:
                    stack.append((x, depth + 1))
        elif isinstance(value, list):
            stack.extend((x, depth + 1) for x in value)
    return None


def _first_open(content, tool):
    opened = generator.spec(tool)[0]
    stack = [content]
    while stack:
        value = stack.pop(0)
        if isinstance(value, dict):
            for k, x in value.items():
                if k in opened and isinstance(x, dict):
                    return x
                stack.append(x)
        elif isinstance(value, list):
            stack.extend(value)
    return None


def _cases_920(sets=('A/9.2.0', 'B/9.2.0')):
    for set_name in sets:
        for sub in CALL_DIRS:
            for call, rec in public_calls(set_name, sub):
                if call['id'] in EXPECTED_FAILURES or call['public_tool'] not in READS:
                    continue
                yield set_name, call, rec['exchange']['response']['result']


def _with(result, content):
    copy = deepcopy(result)
    copy['structuredContent'] = content
    copy['content'] = [{'type': 'text', 'text': json.dumps(content)}]
    return copy


class RealCoreReplay920(unittest.TestCase):

    def test_receipts_replay_with_the_recorded_wire_arguments(self):
        for set_name in ('A/9.2.0', 'B/9.2.0'):
            calls = list(public_calls(set_name))
            outputs = replay('9.2.0', [
                (c['public_tool'], c['public_arguments'], r['exchange']['response']['result'])
                for c, r in calls
            ])
            for (call, _rec), (out, sent) in zip(calls, outputs):
                with self.subTest(set=set_name, call=call['id']):
                    self.assertEqual(sent, [(call['upstream_tool'], call['wire_arguments'])])
                    expected = EXPECTED_FAILURES.get(call['id'])
                    if expected is None:
                        self.assertIs(out['success'], True, out.get('details'))
                    else:
                        self.assertEqual(out['details']['failure_category'], expected)

    def test_admitted_keys_and_withheld_counts(self):
        cases = list(_cases_920())
        outputs = replay('9.2.0', [(c['public_tool'], c['public_arguments'], r) for _, c, r in cases])
        for (set_name, call, result), (out, _sent) in zip(cases, outputs):
            tool = call['public_tool']
            row = READS[tool]
            with self.subTest(set=set_name, call=call['id']):
                data = out['data']
                upstream = generator.names(result['structuredContent'], tool)
                withheld = _withheld_names(row, upstream)
                if tool == 'ha_get_device':
                    self.assertGreaterEqual(data['fields_withheld'], len(withheld))
                else:
                    self.assertEqual(data['fields_withheld'], min(len(withheld), CONTRACT['rules']['count_cap']))
                self.assertEqual(data['fields_dropped_by_policy'], len(upstream & set(row['policy_drops'])))
                if row['projection'] == 'closed':
                    client = generator.names(data, tool) - ADDED
                    self.assertLessEqual(client, set(row['admitted']))
                    self.assertLessEqual(upstream & set(row['admitted']), client)

    def test_an_injected_key_is_withheld_and_counted_once_per_name(self):
        seen = set()
        cases = []
        for set_name, call, result in _cases_920(('B/9.2.0',)):
            content = result['structuredContent']
            if call['public_tool'] in seen or not isinstance(content, dict):
                continue
            seen.add(call['public_tool'])
            one = deepcopy(content)
            one[INJECTED] = 'x'
            two = deepcopy(one)
            nested = _first_row(two, call['public_tool'])
            if nested is not None:
                nested[INJECTED] = 'y'
            for variant in (content, one, two):
                cases.append((call, nested is not None, _with(result, variant)))
        outputs = replay('9.2.0', [(c['public_tool'], c['public_arguments'], r) for c, _, r in cases])
        for i in range(0, len(cases), 3):
            call, has_nested = cases[i][0], cases[i][1]
            base, one, two = (outputs[i + j][0] for j in range(3))
            with self.subTest(tool=call['public_tool']):
                self.assertNotIn(INJECTED, json.dumps(one))
                self.assertNotIn(INJECTED, json.dumps(two))
                self.assertEqual(one['data']['fields_withheld'], base['data']['fields_withheld'] + 1)
                if has_nested:
                    step = 2 if call['public_tool'] == 'ha_get_device' else 1
                    self.assertEqual(two['data']['fields_withheld'], base['data']['fields_withheld'] + step)

    def test_a_key_inside_an_open_container_passes_uncounted(self):
        cases = []
        for set_name, call, result in _cases_920(('B/9.2.0',)):
            content = deepcopy(result['structuredContent'])
            target = _first_open(content, call['public_tool'])
            if target is None:
                continue
            target[INJECTED] = 'open'
            cases.append((call, result, _with(result, content)))
        self.assertTrue(cases)
        outputs = replay('9.2.0', [(c['public_tool'], c['public_arguments'], r)
                                   for c, base, injected in cases for r in (base, injected)])
        for i, (call, _base, _injected) in enumerate(cases):
            base, injected = outputs[2 * i][0], outputs[2 * i + 1][0]
            with self.subTest(call=call['id']):
                self.assertIn(INJECTED, json.dumps(injected))
                self.assertEqual(injected['data']['fields_withheld'], base['data']['fields_withheld'])

    def test_an_unlisted_upstream_warning_is_replaced_and_counted(self):
        cases = []
        for set_name, call, result in _cases_920(('A/9.2.0',)):
            tool = call['public_tool']
            content = result['structuredContent']
            if 'warnings' not in READS[tool]['admitted'] or not isinstance(content, dict):
                continue
            injected = deepcopy(content)
            injected['warnings'] = list(injected.get('warnings', [])) + [CANARY]
            cases.append((call, _with(result, injected)))
        self.assertTrue(cases)
        outputs = replay('9.2.0', [(c['public_tool'], c['public_arguments'], r) for c, r in cases])
        for (call, _), (out, _sent) in zip(cases, outputs):
            with self.subTest(call=call['id']):
                self.assertNotIn(CANARY, json.dumps(out))
                self.assertIn(REPLACED, out['data']['warnings'])
                self.assertGreaterEqual(out['data']['upstream_warnings_replaced'], 1)

    def test_history_withholds_an_unknown_key_instead_of_refusing(self):
        cases = []
        for set_name, call, result in _cases_920(('B/9.2.0',)):
            if call['public_tool'] == 'ha_get_history':
                injected = deepcopy(result['structuredContent'])
                injected[INJECTED] = 'x'
                cases.append((call, _with(result, injected)))
        self.assertTrue(cases)
        for (call, _), (out, _sent) in zip(cases, replay('9.2.0', [
                (c['public_tool'], c['public_arguments'], r) for c, r in cases])):
            with self.subTest(call=call['id']):
                self.assertIs(out['success'], True)
                self.assertGreaterEqual(out['data']['fields_withheld'], 1)
                self.assertNotIn(INJECTED, json.dumps(out))

    def test_zone_and_services_changes(self):
        cases = [(c, r) for s, c, r in _cases_920(('B/9.2.0',))
                 if c['public_tool'] in ('ha_get_zone', 'ha_list_services', 'ha_search')]
        outputs = replay('9.2.0', [(c['public_tool'], c['public_arguments'], r) for c, r in cases])
        for (call, result), (out, _sent) in zip(cases, outputs):
            text = json.dumps(out['data'])
            upstream = generator.names(result['structuredContent'], call['public_tool'])
            with self.subTest(call=call['id']):
                if call['public_tool'] == 'ha_get_zone':
                    zones = out['data'].get('zones') or [out['data'].get('zone')]
                    for zone in zones:
                        self.assertEqual(zone.get('friendly_name'), zone.get('name'))
                    for name in ('persons', 'device_trackers') if 'persons' in upstream else ():
                        self.assertIn(f'"{name}"', text)
                elif call['public_tool'] == 'ha_list_services':
                    self.assertNotIn('type', generator.names(out['data'], 'ha_list_services'))
                    for name in ('response', 'optional'):
                        self.assertNotIn(name, generator.names(out['data'], 'ha_list_services') - ADDED)
                elif 'name' in upstream:
                    self.assertIn('name', generator.names(out['data'], 'ha_search'))


class YamlReadN29(unittest.TestCase):
    """N29 conditions (a) and (b) on the 9.2.0 YAML read (profile B receipts)."""

    def _catalog(self):
        # The stock capture does not advertise the optional YAML read, so the
        # replay serves the exact filesystem-on 9.2.0 catalog the YAML reader
        # binds (upstream_yaml_reader._CAPTURES), after its sha256 is checked.
        from ha_mcp_engineering.providers import upstream_yaml_reader
        _source, path, digest = upstream_yaml_reader._CAPTURES['9.2.0']
        self.assertEqual(sha(ROOT / path), digest)
        return json.loads((ROOT / path).read_text(encoding='utf-8'))['tools']

    def _yaml(self, sub):
        calls = list(public_calls('B/9.2.0', sub, kinds=('yaml',)))
        return calls, replay('9.2.0', [
            (c['public_tool'], c['public_arguments'], r['exchange']['response']['result'])
            for c, r in calls
        ], self._catalog())

    def test_administrator_read_succeeds_with_non_ascii_content(self):
        calls, outputs = self._yaml('calls')
        by_id = {c['id']: (c, out, sent) for (c, _r), (out, sent) in zip(calls, outputs)}
        call, out, sent = by_id['y-group']
        self.assertEqual(sent, [(call['upstream_tool'], call['wire_arguments'])])
        self.assertIs(out['success'], True)
        self.assertIn(NON_ASCII, json.dumps(out, ensure_ascii=False))

    def test_ordinary_user_is_refused_with_no_partial_content(self):
        calls, outputs = self._yaml('yaml-ordinary-user')
        self.assertEqual({c['id'] for c, _ in calls}, {'y-group', 'y-absent-key'})
        for (call, rec), (out, _sent) in zip(calls, outputs):
            with self.subTest(call=call['id']):
                upstream_text = rec['exchange']['response']['result']['content'][0]['text']
                self.assertIn('admin-only', upstream_text)
                self.assertIs(out['success'], False)
                self.assertIsInstance(out['details']['failure_category'], str)
                rendered = json.dumps(out, ensure_ascii=False)
                self.assertNotIn('admin-only', rendered)
                self.assertNotIn('get_caller_token', rendered)
                self.assertNotIn(NON_ASCII, rendered)


if __name__ == '__main__':
    unittest.main()
