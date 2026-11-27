"""HAMCP-319 Phase 2 ENT parity: ledger rows 13, 14 and 17-24 (design section 7).

8.5-shaped public calls run through the real UpstreamReadGateway against
synthetic transports. Rows 13-24 run on ha-mcp 9.2.0 against class S templates
transcribed from the pinned 9.2.0 source
(tests/fixtures/ha_mcp_9_2_0/entities/provenance.json); every success asserts
the 9.2.0 key-set counts. HAMCP-437 I2a-D retired the 9.1.0 and 8.5.0 rows:
the 9.1.0 pending cases are restated in EntitiesUpstreamWarnings92Tests
(ANSWER 20261122-0042-01 item 6, 20261122-0118-01 item 4). The section 8.3 F3
replays read the committed Phase 1 9.1.0 receipts by JSON pointer after their
sha256 is checked against SHA256SUMS.json and project them through the shipped
9.2.0 binding as frozen inputs (ANSWER 20261122-0907-01 conditions; see
FROZEN_LABEL). The platform rows run on 9.2.0. Offline and deterministic; no
live Home Assistant, no network.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hass_mcp_engineering_beta'), str(ROOT / 'tests')]
from mcp.server.fastmcp import FastMCP
from ha_mcp_engineering.fan.contracts import FanRefusal, checked_state as fan_checked_state
from ha_mcp_engineering.helper_value.contracts import (
    HelperRefusal, checked_state as helper_checked_state,
)
from ha_mcp_engineering.power.contracts import checked_state as power_checked_state
from ha_mcp_engineering.providers import upstream_reads_9_1 as reads_9_1
from ha_mcp_engineering.providers import upstream_reads_9_1_entities as ent
from ha_mcp_engineering.providers import upstream_reads_9_2 as reads_9_2
from ha_mcp_engineering.providers import upstream_reads_9_2_entities as ent_92
from ha_mcp_engineering.providers import upstream_reads_9_2_keys as keys
from ha_mcp_engineering.providers.upstream_read_gateway import (
    REDACTION_WARNING, UpstreamReadGateway,
)
from ha_mcp_engineering.ha_core_readmission.routes import INTERNAL_ONLY_DELEGATED_READS
from ha_mcp_engineering.providers import upstream_read_gateway as gateway_module
from ha_mcp_engineering.search import subcalls
from ha_mcp_engineering.tools import search as native_search
from ha_mcp_engineering.request_context import begin_request, end_request
from test_ha_mcp_production_readmission import _GatewayTransport, _settings
import support.ha_mcp_captures as previous  # HAMCP-437: helpers moved from the retired 8.5 module
# Module imports, not ``from ... import``: a TestCase bound at module level is
# discovered again under this module's id (duplicate_discovered_test_id).
from ha_mcp_engineering.providers import upstream_blueprint as blueprint_adapter
from ha_mcp_engineering.providers import upstream_platform_reads as platform_adapter
import test_hamcp129_platform_reads as platform_tests

FIXTURES_92 = ROOT / 'tests/fixtures/ha_mcp_9_2_0/entities'
RESPONSES_92 = json.loads((FIXTURES_92 / 'responses.json').read_text(encoding='utf-8'))
PROVENANCE_92 = json.loads((FIXTURES_92 / 'provenance.json').read_text(encoding='utf-8'))
V92 = RESPONSES_92['v92']
# Read by the F3 replays (receipt provenance), FixtureProvenanceTests and the
# 8.5-shaped call cases (V85).
FIXTURES = ROOT / 'tests/fixtures/ha_mcp_9_1_0/entities'
RESPONSES = json.loads((FIXTURES / 'responses.json').read_text(encoding='utf-8'))
PROVENANCE = json.loads((FIXTURES / 'provenance.json').read_text(encoding='utf-8'))
V91 = RESPONSES['v91']
V85 = RESPONSES['v85']
PHASE1 = ROOT / 'docs/evidence/hamcp319/phase1'
# ANSWER 20261122-0931-01 case 2: the 8.5.0 public surface, generated at 310c6a0e
# by tests/fixtures/hamcp319/generate_ent_public_surface.py (provenance.json).
SURFACE_85_FILE = ROOT / 'tests/fixtures/hamcp319/ent-public-surface-8.5.0.json'
SURFACE_85 = json.loads(SURFACE_85_FILE.read_text(encoding='utf-8'))
SURFACE_85_LABEL = ('Engineering-published surface generated from the 8.5.0 row at 310c6a0e; '
                    'not upstream evidence')
SUMS = json.loads((PHASE1 / 'SHA256SUMS.json').read_text(encoding='utf-8'))

BP = 'home-assistant-best-practices'
ACK_KEY = 'I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-00000000'
ENT_TOOLS = (
    'ha_get_entity', 'ha_get_entity_exposure', 'ha_get_overview',
    'ha_get_skill_guide', 'ha_get_state', 'ha_get_todo', 'ha_get_zone',
    'ha_list_floors_areas', 'ha_list_services', 'ha_search',
)
# Every warning the gateway may attach to these rows is a fixed Engineering string.
ENGINEERING_WARNINGS = frozenset({
    ent.UNVERIFIED, ent.UPSTREAM_WARNINGS, ent.UPSTREAM_PARTIAL, ent.SEARCH_PARTIAL,
    ent.VISIBLE_SCOPE, ent.STATE_WITHHELD, ent.STATE_INCOMPLETE,
    ent.ENTITY_INCOMPLETE, REDACTION_WARNING,
    'The untrusted upstream response was safely bounded.',
    'The upstream search reported partial coverage.',
})
DASHBOARD_URL = 'http://upstream:9583/synthetic-upstream-secret/catalog/full'
# The 9.2.0 peer: the host "upstream" is a redaction token that would mask the
# 9.2.0 upstream_warnings_replaced key.
PEER_URL = 'http://synthetic-host-319:9583/synthetic-ent-secret-319/mcp'
COUNTS = ('fields_withheld', 'fields_dropped_by_policy', 'upstream_warnings_replaced')
NONE_WITHHELD = dict.fromkeys(COUNTS, 0)
HIDDEN_ID = 'hamcp319_hidden'
# /before_toggle context id: present only on the hidden entity's record.
HIDDEN_CONTEXT = '01M6Q7H0FKV71KHRNF8PES6H3K'
UPSTREAM_CANARY = 'synthetic-upstream-canary-text'
FROZEN_LABEL = ('9.1.0 upstream receipt, frozen input, projected through the shipped 9.2.0 '
                'binding; not evidence of 9.2.0 upstream output')


def wrap(payload):
    return {
        'content': [{'type': 'text', 'text': json.dumps(payload)}],
        'isError': False,
        'structuredContent': deepcopy(payload),
    }


def error_result(code, message=UPSTREAM_CANARY):
    body = {'success': False, 'error': {'code': code, 'message': message,
                                        'details': UPSTREAM_CANARY}}
    return {'content': [{'type': 'text', 'text': json.dumps(body)}], 'isError': True}


def receipt(name, pointer):
    """Phase 1 record by JSON pointer, after sha256 and provenance checks."""

    raw = (PHASE1 / 'receipts' / name).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SUMS[f'receipts/{name}']:
        raise AssertionError(f'receipt hash drift: {name}')
    declared = PROVENANCE['fixtures'][f'receipt/{name[:-5]}']
    if (declared['class'] != 'C' or declared['sha256'] != digest
            or pointer not in declared['pointers']
            or declared['receipt'] != f'docs/evidence/hamcp319/phase1/receipts/{name}'):
        raise AssertionError(f'receipt provenance mismatch: {name}{pointer}')
    value = json.loads(raw)
    for part in pointer.lstrip('/').split('/'):
        value = value[part.replace('~1', '/').replace('~0', '~')]
    return deepcopy(value)


class Transport(_GatewayTransport):
    def __init__(self, tools, version):
        super().__init__(tools, version=version)
        self.sent = []
        self.override = None

    async def execute_read(self, tool, arguments, **kwargs):
        exchange = await super().execute_read(tool, arguments, **kwargs)
        self.sent.append((tool, deepcopy(arguments)))
        return replace(exchange, call_result=deepcopy(self.override))


class ParityBase(unittest.IsolatedAsyncioTestCase):
    version = '9.1.0'

    async def asyncSetUp(self):
        self.transport = Transport(previous.capture(self.version)['tools'], self.version)
        settings = replace(_settings('synthetic-no-key'), ha_mcp_release_registry_enabled=False)
        if self.version == '9.1.0':
            settings = replace(settings, upstream_dashboard_mcp_url=DASHBOARD_URL)
        elif self.version == '9.2.0':
            settings = replace(settings, upstream_dashboard_mcp_url=PEER_URL)
        self.gateway = UpstreamReadGateway()
        self.gateway.configure(settings, transport=self.transport)
        self.server = FastMCP('synthetic-hamcp319-ent')
        await self.gateway.initialize(self.server)

    async def call(self, name, args, upstream=None):
        self.transport.override = upstream
        self.transport.sent.clear()
        telemetry, token = begin_request('synthetic-hamcp319-ent')
        try:
            if name in INTERNAL_ONLY_DELEGATED_READS:
                # HAMCP-501 W6 (G1, as relayed by the architect): the delegated
                # ha_search is internal-only. Its Row 25 binding is reached
                # only as a native search sub-call page: the page reader's
                # execute, with a page capture, inside a request that carries
                # a Core lease (a synthetic authorizer here).
                telemetry.core_dispatch_authorizer = lambda: True
                route = self.gateway._internal[name]
                return json.loads(await self.gateway.execute(
                    exposed_name=name, arguments=deepcopy(args),
                    reviewed_schema=route.observed_tool['inputSchema'], policy_entry=route.entry,
                    admission_generation=route.generation,
                    contract_fingerprint=route.contract_fingerprint,
                    search_page=gateway_module._SearchPageCapture('helpers')))
            tool = self.gateway._registered_tool_registry.snapshot()[name]
            return json.loads(await tool.run(deepcopy(args)))
        finally:
            end_request(token)

    # -- assertions ---------------------------------------------------------
    def assert_sent(self, name, args):
        self.assertEqual(self.transport.sent, [(name, args)])

    def assert_fixed_warnings(self, result):
        self.assertIsInstance(result['warnings'], list)
        allowed = ENGINEERING_WARNINGS | ({keys.REPLACED_PARTIAL} if self.version == '9.2.0' else set())
        self.assertLessEqual(set(result['warnings']), allowed, result['warnings'])

    def assert_counts(self, result, counts=NONE_WITHHELD):
        """Pops and checks the 9.2.0 key-set counts; 9.1.0 has none."""

        data = result['data']
        if self.version == '9.2.0':
            self.assertEqual({key: data.pop(key) for key in COUNTS}, counts, result)
        else:
            self.assertFalse(set(COUNTS) & set(data), result)

    def assert_success(self, result, name, *, partial=False, counts=NONE_WITHHELD):
        self.assertTrue(result['success'], result)
        self.assertEqual(result['metadata']['upstream_tool'], name)
        self.assertEqual(result['metadata']['upstream_version'], self.version)
        self.assertFalse(result['metadata']['fallback_occurred'])
        self.assertEqual(result['metadata']['completeness'], 'partial' if partial else 'complete')
        self.assert_fixed_warnings(result)
        self.assert_counts(result, counts)

    def assert_no_hidden_value(self, result):
        text = json.dumps(result)
        self.assertNotIn(HIDDEN_ID, text)
        self.assertNotIn(HIDDEN_CONTEXT, text)

    def assert_refused_unsent(self, result, category='argument_validation'):
        self.assertFalse(result['success'], result)
        self.assertEqual(result['details']['failure_category'], category)
        self.assertFalse(result['metadata']['upstream_dispatch_occurred'])
        self.assertEqual(self.transport.sent, [])
        self.assertNotIn('data', result)

    def assert_failed_dispatched(self, result, category):
        self.assertFalse(result['success'], result)
        self.assertEqual(result['details']['failure_category'], category)
        self.assertTrue(result['metadata']['upstream_dispatch_occurred'])
        self.assertNotIn('data', result)
        self.assertEqual(len(self.transport.sent), 1)
        self.assertNotIn(UPSTREAM_CANARY, json.dumps(result))

    async def assert_unknown_key_refused(self, name, args):
        result = await self.call(name, {**args, 'synthetic_unreviewed': 1}, wrap({'success': True}))
        self.assert_refused_unsent(result)

    async def assert_thread_credential_attribute_redacted(self, template):
        """Section 6.5 backstop on a generic attribute (ha_get_state): Thread
        credential keys are redacted by category; released neighbors pass."""
        template = deepcopy(template)
        canaries = {
            'network_key': 'CANARY-attr-network-key',
            'Active-Dataset-TLVs': 'CANARY-attr-active-dataset',
            'pskc': 'CANARY-attr-pskc',
            'dataset_tlvs': {'raw': 'CANARY-attr-dataset-tlvs'},
        }
        template['data']['attributes'].update(canaries, channel=15, network_name='synthetic-net')
        args = {'entity_id': 'light.synthetic_lamp'}
        result = await self.call('ha_get_state', args, wrap(template))
        # A redaction is disclosed: the result is useful but marked partial.
        self.assert_success(result, 'ha_get_state', partial=True)
        self.assert_sent('ha_get_state', args)
        attributes = result['data']['data']['attributes']
        for key in canaries:
            self.assertEqual(attributes[key], '[REDACTED:thread_credential]')
        self.assertEqual(attributes['channel'], 15)
        self.assertEqual(attributes['network_name'], 'synthetic-net')
        self.assertEqual(attributes['brightness'], template['data']['attributes']['brightness'])
        self.assertEqual(result['warnings'], [REDACTION_WARNING])
        self.assertNotIn('CANARY-attr', json.dumps(result))

    async def assert_upstream_error_fails(self, name, args, code='SERVICE_CALL_FAILED'):
        result = await self.call(name, args, error_result(code))
        self.assertFalse(result['success'], result)
        self.assertTrue(result['metadata']['upstream_dispatch_occurred'])
        self.assertNotIn('data', result)
        self.assertNotIn(UPSTREAM_CANARY, json.dumps(result))
        self.assertFalse(result['metadata']['fallback_occurred'])
        return result

    async def assert_invalid_response(self, name, args, payload):
        result = await self.call(name, args, wrap(payload))
        self.assert_failed_dispatched(result, 'invalid_response')


# ---------------------------------------------------------------------------
# 9.2.0
# ---------------------------------------------------------------------------
class Parity92Tests(ParityBase):
    """Rows 13-24 on 9.2.0. A key outside a read's reviewed key set is withheld
    and counted (upstream_reads_9_2_keys.py); each success asserts the counts."""

    version = '9.2.0'

    # Row 13 ha_get_entity -----------------------------------------------------
    async def test_92_parity_row_13_entity(self):
        args = {'entity_id': 'light.synthetic_lamp'}
        template = V92['ha_get_entity']['single']
        result = await self.call('ha_get_entity', args, wrap(template))
        self.assert_success(result, 'ha_get_entity')
        self.assert_sent('ha_get_entity', args)  # registry read only; no mutation sent
        self.assertEqual(result['data'], template)
        entry = result['data']['entity_entry']
        self.assertIs(entry['uses_device_name'], False)  # additive
        self.assertLessEqual(set(V85['ha_get_entity']['single']['entity_entry']), set(entry))
        self.assertEqual(result['warnings'], [])

    async def test_92_parity_row_13_entity_partial_disclosed(self):
        args = {'entity_id': ['light.synthetic_lamp', 'light.synthetic_absent']}
        template = V92['ha_get_entity']['bulk_partial']
        result = await self.call('ha_get_entity', args, wrap(template))
        # count, suggestions and the per-error text are outside the key set.
        self.assert_success(result, 'ha_get_entity', partial=True,
                            counts=NONE_WITHHELD | {'fields_withheld': 3})
        self.assert_sent('ha_get_entity', args)
        expected = {key: deepcopy(template[key]) for key in ('success', 'entity_entries')}
        expected['errors'] = [{'entity_id': 'light.synthetic_absent'}]
        self.assertEqual(result['data'], expected)
        self.assertEqual(result['warnings'], [ent.ENTITY_INCOMPLETE])

    async def test_92_parity_row_13_entity_refusals(self):
        await self.assert_unknown_key_refused('ha_get_entity', {'entity_id': 'light.synthetic_lamp'})
        # Rule 9 error contract: a not-found or failed lookup is a bounded failure.
        result = await self.assert_upstream_error_fails('ha_get_entity', {'entity_id': 'light.synthetic_absent'})
        self.assertEqual(self.transport.sent, [('ha_get_entity', {'entity_id': 'light.synthetic_absent'})])
        self.assertNotIn('synthetic_absent', result['message'])
        await self.assert_invalid_response('ha_get_entity', {'entity_id': 'light.synthetic_lamp'}, {'success': True})
        await self.assert_invalid_response('ha_get_entity', {'entity_id': 'light.synthetic_lamp'},
                                           {'success': False, 'entity_entry': {}})
        await self.assert_invalid_response('ha_get_entity', {'entity_id': 'light.synthetic_lamp'},
                                           {'success': True, 'entity_entry': []})

    # Row 14 ha_get_entity_exposure --------------------------------------------
    async def test_92_parity_row_14_exposure(self):
        args = {'entity_id': 'light.synthetic_lamp'}
        template = V92['ha_get_entity_exposure']['single']
        result = await self.call('ha_get_entity_exposure', args, wrap(template))
        self.assert_success(result, 'ha_get_entity_exposure')
        self.assert_sent('ha_get_entity_exposure', args)  # no exposure write sent
        self.assertEqual(result['data'], template)
        self.assertEqual(result['warnings'], [])
        template = V92['ha_get_entity_exposure']['list']
        result = await self.call('ha_get_entity_exposure', {}, wrap(template))
        # count, filters_applied, summary, total_entities_with_settings and
        # summary's three keys are outside the key set.
        self.assert_success(result, 'ha_get_entity_exposure', counts=NONE_WITHHELD | {'fields_withheld': 7})
        self.assert_sent('ha_get_entity_exposure', {})
        self.assertEqual(result['data'], {key: template[key] for key in ('success', 'exposed_entities')})
        self.assertEqual(result['warnings'], [])

    async def test_92_parity_row_14_exposure_refusals(self):
        await self.assert_unknown_key_refused('ha_get_entity_exposure', {})
        await self.assert_upstream_error_fails('ha_get_entity_exposure', {'entity_id': 'light.synthetic_lamp'})
        await self.assert_invalid_response('ha_get_entity_exposure', {'entity_id': 'light.synthetic_lamp'},
                                           {'success': True, 'entity_id': 'light.synthetic_lamp', 'exposed_to': []})
        await self.assert_invalid_response('ha_get_entity_exposure', {}, {'success': True})

    # Row 17 ha_get_overview ----------------------------------------------------
    async def test_92_parity_row_17_overview(self):
        template = V92['ha_get_overview']['minimal']
        result = await self.call('ha_get_overview', {}, wrap(template))
        # mcp_client, tool_discovery and settings_url_hint are policy drops.
        self.assert_success(result, 'ha_get_overview', counts=NONE_WITHHELD | {'fields_dropped_by_policy': 3})
        self.assert_sent('ha_get_overview', {})
        data = result['data']
        for dropped in ('mcp_client', 'tool_discovery', 'settings_url', 'settings_url_hint'):
            self.assertNotIn(dropped, data)
        self.assertNotIn('synthetic-client', json.dumps(result))
        self.assertNotIn('/synthetic/settings', json.dumps(result))
        for kept in ('system_summary', 'domain_stats', 'area_analysis', 'ai_insights', 'ha_mcp_update'):
            self.assertEqual(data[kept], template[kept])
        self.assertEqual(set(data), set(template) - {'mcp_client', 'tool_discovery', 'settings_url_hint'})
        self.assertEqual(result['warnings'], [ent.VISIBLE_SCOPE])

    async def test_92_parity_row_17_overview_refusals(self):
        await self.assert_unknown_key_refused('ha_get_overview', {})
        await self.assert_upstream_error_fails('ha_get_overview', {})
        await self.assert_invalid_response('ha_get_overview', {}, {'success': True, 'domain_stats': []})
        await self.assert_invalid_response('ha_get_overview', {}, {'success': False})

    # Row 18 ha_get_skill_guide -------------------------------------------------
    async def test_92_skill_guide_positive(self):
        args = {'skill': BP, 'file': 'SKILL.md'}
        template = V92['ha_get_skill_guide']['file_skill_md']
        result = await self.call('ha_get_skill_guide', args, wrap(template))
        self.assertTrue(result['success'], result)
        self.assert_sent('ha_get_skill_guide', {'file': 'SKILL.md'})
        self.assert_counts(result, NONE_WITHHELD | {'fields_dropped_by_policy': 1})  # how_to_use
        data = result['data']
        self.assertEqual(set(data), {'success', 'skill', 'file', 'uri', 'content'})
        self.assertEqual(data['skill'], BP)
        self.assertEqual(data['file'], 'SKILL.md')
        self.assertEqual(data['uri'], f'skill://{BP}/SKILL.md')
        self.assertNotIn('how_to_use', data)  # file-only call form
        self.assertNotIn('skill_content_hint', data)  # never fabricated
        # Strict-BPS acknowledgement passes; the shared sanitizer redaction
        # is disclosed.
        self.assertTrue(data['content'].startswith(f'Acknowledgment key: {ACK_KEY} '))
        self.assertTrue(data['content'].endswith('# Synthetic best practices\n'))
        self.assertEqual(result['warnings'], [REDACTION_WARNING])
        self.assertEqual(result['metadata']['completeness'], 'partial')
        self.assert_fixed_warnings(result)

    async def test_92_parity_row_18_skill_guide_reference_file(self):
        args = {'skill': BP, 'file': 'references/synthetic.md'}
        template = V92['ha_get_skill_guide']['file_reference']
        result = await self.call('ha_get_skill_guide', args, wrap(template))
        self.assertTrue(result['success'], result)
        self.assert_sent('ha_get_skill_guide', {'file': 'references/synthetic.md'})
        self.assert_counts(result)
        self.assertEqual(result['data']['skill_content_hint'], template['skill_content_hint'])
        self.assertEqual(result['data']['uri'], template['uri'])
        self.assert_fixed_warnings(result)

    async def test_92_parity_row_18_skill_guide_unavailable_tiers(self):
        for args in ({}, {'skill': BP}):
            result = await self.call('ha_get_skill_guide', args, wrap(V85['ha_get_skill_guide']['tier1']))
            self.assert_refused_unsent(result, 'capability_unavailable')
            self.assertEqual(result['error_code'], 'unsupported_operation')
            self.assertEqual(result['message'], 'This skill guide request is unavailable on this upstream version.')
            # Never synthesized; no refusal text names the skill.
            self.assertNotIn(BP, json.dumps(result))
            self.assertNotIn('SKILL.md', json.dumps(result))

    async def test_92_parity_row_18_skill_guide_refusals(self):
        template = V92['ha_get_skill_guide']['file_skill_md']
        for args in ({'skill': 'synthetic-other-skill', 'file': 'SKILL.md'},
                     {'skill': 'synthetic-other-skill'},
                     {'file': 'SKILL.md'},
                     {'skill': BP, 'file': '../synthetic.md'},
                     {'skill': BP, 'file': '/etc/synthetic'},
                     {'skill': BP, 'file': 'SKILL.md', 'synthetic_unreviewed': 1}):
            result = await self.call('ha_get_skill_guide', args, wrap(template))
            self.assertFalse(result['success'], (args, result))
            self.assertFalse(result['metadata']['upstream_dispatch_occurred'], args)
            self.assertEqual(self.transport.sent, [], args)
            self.assertNotIn(BP, result['message'])
        args = {'skill': BP, 'file': 'SKILL.md'}
        for bad in ({**template, 'uri': f'skill://{BP}/references/other.md'},
                    {**template, 'file': 'references/other.md'},
                    {**template, 'content': None},
                    {**template, 'success': False}):
            await self.assert_invalid_response('ha_get_skill_guide', args, bad)
        await self.assert_upstream_error_fails('ha_get_skill_guide', args, 'RESOURCE_NOT_FOUND')

    # Row 19 ha_get_state -------------------------------------------------------
    async def test_92_parity_row_19_state(self):
        args = {'entity_id': 'light.synthetic_lamp'}
        template = V92['ha_get_state']['single']
        result = await self.call('ha_get_state', args, wrap(template))
        self.assert_success(result, 'ha_get_state')
        self.assert_sent('ha_get_state', args)
        self.assertEqual(result['data'], template)
        self.assertEqual(result['warnings'], [])

    async def test_92_thread_credential_generic_attribute_redacted(self):
        await self.assert_thread_credential_attribute_redacted(V92['ha_get_state']['single'])

    async def test_92_parity_row_19_state_bulk_partial_disclosed(self):
        args = {'entity_id': ['light.synthetic_lamp', 'light.synthetic_absent']}
        template = V92['ha_get_state']['bulk_partial']
        result = await self.call('ha_get_state', args, wrap(template))
        # error_count, suggestions and the per-error text are outside the key set.
        self.assert_success(result, 'ha_get_state', partial=True, counts=NONE_WITHHELD | {'fields_withheld': 3})
        expected = deepcopy(template)
        for key in ('error_count', 'suggestions'):
            del expected['data'][key]
        expected['data']['errors'] = [{'entity_id': 'light.synthetic_absent'}]
        self.assertEqual(result['data'], expected)
        self.assertEqual(result['warnings'], [ent.STATE_INCOMPLETE])
        # Zero readable states is success:false upstream: still disclosed, never empty success.
        empty = {'data': {'success': False, 'count': 0, 'states': {},
                          'errors': [{'entity_id': 'light.synthetic_absent', 'error': 'x'}]},
                 'metadata': template['metadata']}
        result = await self.call('ha_get_state', args, wrap(empty))
        self.assertEqual(result['metadata']['completeness'], 'partial')
        self.assertIn(ent.STATE_INCOMPLETE, result['warnings'])

    async def test_92_parity_row_19_state_refusals(self):
        await self.assert_unknown_key_refused('ha_get_state', {'entity_id': 'light.synthetic_lamp'})
        result = await self.call('ha_get_state', {'entity_id': 'light.synthetic_absent'},
                                 error_result('ENTITY_NOT_FOUND'))
        self.assertEqual(result['error_code'], 'entity_not_found')
        self.assertNotIn(UPSTREAM_CANARY, json.dumps(result))
        single = {'entity_id': 'light.synthetic_lamp'}
        await self.assert_invalid_response('ha_get_state', single, V92['ha_get_state']['single']['data'])
        await self.assert_invalid_response('ha_get_state', single,
                                           {'data': {**V92['ha_get_state']['single']['data'], 'attributes': []},
                                            'metadata': {}})
        await self.assert_invalid_response('ha_get_state', single, V92['ha_get_state']['bulk_partial'])
        await self.assert_invalid_response('ha_get_state', {'entity_id': ['light.synthetic_lamp']},
                                           V92['ha_get_state']['single'])

    # Row 20 ha_get_todo --------------------------------------------------------
    async def test_92_parity_row_20_todo(self):
        for args, case in (({}, 'lists'),
                           ({'entity_id': 'todo.synthetic_list', 'status': 'needs_action'}, 'items')):
            template = V92['ha_get_todo'][case]
            result = await self.call('ha_get_todo', args, wrap(template))
            self.assert_success(result, 'ha_get_todo')
            self.assert_sent('ha_get_todo', args)  # read only; scalar status unchanged
            self.assertEqual(result['data'], template)

    async def test_92_parity_row_20_todo_refusals(self):
        # Upstream also accepts a status list; the public scalar contract refuses it unsent.
        result = await self.call('ha_get_todo', {'entity_id': 'todo.synthetic_list', 'status': ['completed']},
                                 wrap(V92['ha_get_todo']['items']))
        self.assert_refused_unsent(result)
        await self.assert_unknown_key_refused('ha_get_todo', {})
        await self.assert_upstream_error_fails('ha_get_todo', {'entity_id': 'todo.synthetic_list'})
        await self.assert_invalid_response('ha_get_todo', {'entity_id': 'todo.synthetic_list'},
                                           {'success': True, 'entity_id': 'todo.synthetic_list', 'items': {}})

    # Row 21 ha_get_zone --------------------------------------------------------
    async def test_92_parity_row_21_zone(self):
        notes = [ent_92.ZONE_LOCATION_NOTE, ent_92.ZONE_ICON_NOTE]
        template = V92['ha_get_zone']['list_with_extra_rows']
        result = await self.call('ha_get_zone', {}, wrap(template))
        self.assert_success(result, 'ha_get_zone')
        self.assert_sent('ha_get_zone', {})
        zones = result['data']['zones']
        # Extra home/YAML rows kept; friendly_name restored from name.
        self.assertEqual(zones, [{**row, 'friendly_name': row['name']} for row in template['zones']])
        self.assertEqual(result['data'][ent_92.NOTES], notes)
        legacy = V85['ha_get_zone']['list']['zones'][0]
        matching = [z for z in zones if z['id'] == legacy['id']]
        self.assertEqual(len(matching), 1)
        self.assertEqual({k: matching[0][k] for k in legacy}, legacy)  # 8.5 row not dropped
        self.assertEqual(matching[0]['entity_id'], 'zone.synthetic_office')  # additive
        args = {'zone_id': 'synthetic_office'}
        template = V92['ha_get_zone']['single']
        result = await self.call('ha_get_zone', args, wrap(template))
        self.assert_success(result, 'ha_get_zone')
        self.assert_sent('ha_get_zone', args)
        zone = template['zone']
        self.assertEqual(result['data'], {**template, 'zone': {**zone, 'friendly_name': zone['name']},
                                          ent_92.NOTES: notes})

    async def test_92_parity_row_21_zone_refusals(self):
        await self.assert_unknown_key_refused('ha_get_zone', {})
        await self.assert_upstream_error_fails('ha_get_zone', {'zone_id': 'synthetic_absent'}, 'RESOURCE_NOT_FOUND')
        await self.assert_invalid_response('ha_get_zone', {}, {'success': True, 'zones': ['x']})
        await self.assert_invalid_response('ha_get_zone', {}, {'success': True})

    # Row 22 ha_list_floors_areas -----------------------------------------------
    async def test_92_parity_row_22_floors_areas(self):
        template = V92['ha_list_floors_areas']['all']
        result = await self.call('ha_list_floors_areas', {}, wrap(template))
        self.assert_success(result, 'ha_list_floors_areas')
        self.assert_sent('ha_list_floors_areas', {})
        self.assertEqual(result['data'], template)
        self.assertEqual(result['warnings'], [])

    async def test_92_parity_row_22_floors_areas_refusals(self):
        await self.assert_unknown_key_refused('ha_list_floors_areas', {})
        await self.assert_upstream_error_fails('ha_list_floors_areas', {})
        await self.assert_invalid_response('ha_list_floors_areas', {}, {'success': True, 'floors': {}})

    # Row 23 ha_list_services ---------------------------------------------------
    async def test_92_parity_row_23_services(self):
        args = {'domain': 'light'}
        template = V92['ha_list_services']['light']
        result = await self.call('ha_list_services', args, wrap(template))
        self.assert_success(result, 'ha_list_services')
        self.assert_sent('ha_list_services', args)
        self.assertEqual(result['data'], {**template, ent_92.NOTES: [ent_92.SERVICES_TYPE_NOTE]})

    async def test_92_parity_row_23_services_refusals(self):
        await self.assert_unknown_key_refused('ha_list_services', {})
        await self.assert_upstream_error_fails('ha_list_services', {'domain': 'light'})
        await self.assert_invalid_response('ha_list_services', {}, {'success': True, 'services': []})

    # Row 24 ha_search ----------------------------------------------------------
    # HAMCP-501 W6 (G1): the public ha_search is native (pinned in
    # tests.test_hamcp501_native_search_cutover). These Row 24 projection,
    # disclosure and refusal guards now run on the internal-only route, as a
    # sub-call page with the fixed page arguments (see ParityBase.call).
    PAGE = subcalls.page_arguments('helper', 'synthetic lamp', True, 0,
                                   subcalls.PAGE_SIZE_LADDER[0])

    async def test_92_parity_row_24_search_is_internal_only(self):
        # Never published, never a direct read: zero dispatch either way.
        registered = self.gateway._registered_tool_registry.snapshot()
        exposed = {tool.name for tool in await self.server.list_tools()}
        for name in ('ha_search', 'ha_mcp__ha_search'):
            self.assertNotIn(name, registered)
            self.assertNotIn(name, exposed)
        route = self.gateway._internal['ha_search']
        self.transport.override = wrap(V92['ha_search']['no_warnings'])
        self.transport.sent.clear()
        telemetry, token = begin_request('synthetic-hamcp319-ent')
        telemetry.core_dispatch_authorizer = lambda: True
        try:
            for args, page in ((self.PAGE, None), ({'query': 'synthetic lamp'}, 'helpers'),
                               (self.PAGE, 'dashboards'),
                               ({**self.PAGE, 'synthetic_unreviewed': 1}, 'helpers')):
                with self.subTest(args=sorted(args), page=page):
                    result = json.loads(await self.gateway.execute(
                        exposed_name='ha_search', arguments=deepcopy(args),
                        reviewed_schema=route.observed_tool['inputSchema'],
                        policy_entry=route.entry, admission_generation=route.generation,
                        contract_fingerprint=route.contract_fingerprint,
                        search_page=None if page is None else gateway_module._SearchPageCapture(page)))
                    self.assert_refused_unsent(result, 'prohibited_delegation')
        finally:
            end_request(token)

    async def test_92_parity_row_24_search(self):
        args = self.PAGE
        template = V92['ha_search']['no_warnings']
        self.assertNotIn('warnings', template)
        result = await self.call('ha_search', args, wrap(template))
        self.assert_sent('ha_search', args)
        self.assertTrue(result['success'], result)
        self.assert_counts(result)
        self.assertEqual(result['data'], {**template, 'warnings': []})  # normalized
        self.assertEqual(result['data']['entities'], template['entities'])
        # Visibility omissions are disclosed on every search.
        self.assertEqual(result['warnings'], [ent.VISIBLE_SCOPE])
        self.assertEqual(result['metadata']['completeness'], 'partial')
        self.assert_fixed_warnings(result)

    async def test_92_parity_row_24_search_partial_disclosed(self):
        template = V92['ha_search']['partial']
        result = await self.call('ha_search', self.PAGE, wrap(template))
        self.assertTrue(result['success'], result)
        # partial_reason is outside the key set; the unreviewed upstream
        # warning text is replaced by the fixed code and disclosed.
        self.assert_counts(result, NONE_WITHHELD | {'fields_withheld': 1, 'upstream_warnings_replaced': 1})
        self.assertEqual(result['metadata']['completeness'], 'partial')
        self.assertEqual(result['warnings'], [ent.VISIBLE_SCOPE, ent.SEARCH_PARTIAL, ent.UPSTREAM_WARNINGS,
                                              keys.REPLACED_PARTIAL])
        self.assertEqual(result['data']['warnings'], [keys.REPLACED_WARNING])
        self.assertNotIn('partial_reason', result['data'])
        self.assertNotIn('config backend timed out', json.dumps(result))
        self.assert_fixed_warnings(result)
        # The partial flag alone is disclosed (re-pinned from the retired 8.5.0 case).
        flagged = {**V92['ha_search']['no_warnings'], 'partial': True}
        result = await self.call('ha_search', self.PAGE, wrap(flagged))
        self.assertTrue(result['success'], result)
        self.assert_counts(result)
        self.assertEqual(result['metadata']['completeness'], 'partial')
        self.assertEqual(result['warnings'], [ent.VISIBLE_SCOPE, ent.SEARCH_PARTIAL])
        self.assert_fixed_warnings(result)

    async def test_92_parity_row_24_search_refusals(self):
        args = self.PAGE
        # An unknown key is not a sub-call page: refused before dispatch.
        result = await self.call('ha_search', {**args, 'synthetic_unreviewed': 1}, wrap({'success': True}))
        self.assert_refused_unsent(result, 'prohibited_delegation')
        # All-legs failure raises upstream: a failure, never an empty success.
        await self.assert_upstream_error_fails('ha_search', args, 'SERVICE_CALL_FAILED')
        await self.assert_invalid_response('ha_search', args, {**V92['ha_search']['no_warnings'], 'success': False})
        await self.assert_invalid_response('ha_search', args, {**V92['ha_search']['no_warnings'], 'partial': None})
        await self.assert_invalid_response('ha_search', args, {**V92['ha_search']['no_warnings'], 'entities': {}})
        await self.assert_invalid_response('ha_search', args, {**V92['ha_search']['no_warnings'], 'warnings': 'x'})
        # Bounded: an oversized result is refused, not truncated into a success.
        big = {**V92['ha_search']['no_warnings'],
               'entities': [{'entity_id': f'light.synthetic_{i}', 'friendly_name': 'x' * 400}
                            for i in range(200)]}
        result = await self.call('ha_search', args, wrap(big))
        self.assertFalse(result['success'], result)
        self.assertNotIn('data', result)

    # Projection unit checks ---------------------------------------------------
    def test_92_parity_rows_unrecognized_shapes_raise(self):
        # 9.2.0 composes the key set with the 9.1 projection or the 9.2.0 override.
        self.assertEqual(set(ent.PROJECTIONS), set(ENT_TOOLS))
        self.assertLessEqual(set(ent_92.PROJECTIONS), set(ENT_TOOLS))
        self.assertLessEqual(set(ENT_TOOLS), set(keys.ROWS))
        for name in ENT_TOOLS:
            project, completeness = reads_9_2._projection(name)
            args = {'skill': BP, 'file': 'SKILL.md'} if name == 'ha_get_skill_guide' else {}
            for payload in (None, [], 'x', {'success': 'true'}, {'success': False}):
                with self.assertRaises(ValueError, msg=(name, payload)) as caught:
                    project(payload, args)
                self.assertEqual(str(caught.exception), 'invalid_response')
            partial, warnings = completeness('x')
            self.assertTrue(partial, name)
            self.assertLessEqual(set(warnings), ENGINEERING_WARNINGS)

    def test_92_parity_rows_policy_bound_to_adapter(self):
        policy = json.loads((ROOT / 'hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy_9_2_0.json')
                            .read_text(encoding='utf-8'))
        rows = {e['upstream_name']: e for e in policy['tools']}
        for name in ENT_TOOLS:
            self.assertEqual(rows[name]['argument_restrictions'], [reads_9_2.ADAPTER], name)
            self.assertEqual(rows[name]['classification'], 'automatic_read', name)
            self.assertEqual(rows[name]['exposed_name'], name)
        self.assertEqual(reads_9_2.ADAPTER, 'ha-mcp-9.2.0-public-reads-v1')
        # The public skill-guide description states the file-only behavior
        # (inventory and file-list are unavailable), not 8.6's.
        guide = rows['ha_get_skill_guide']['description']
        self.assertNotIn('8.6', guide)
        self.assertIn('unavailable', guide)
        self.assertIn('SKILL.md', guide)

    async def test_92_skill_guide_description_states_file_only(self):
        """Architect ruling 2026-11-07 item 3: the skill-guide description
        states that the upstream is file-only, with the inventory and
        file-list tiers unavailable. Pinned exactly, both in the reviewed 9.2.0
        policy and on the exposed MCP tool."""

        expected = (
            "Read a bundled Home Assistant skill document: supply skill and a file "
            "path relative to that skill (start with SKILL.md). On this upstream "
            "version the skill inventory (no arguments) and file-list (skill only) "
            "modes are unavailable and return an unavailable refusal.")
        policy = json.loads((ROOT / 'hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy_9_2_0.json')
                            .read_text(encoding='utf-8'))
        rows = {e['upstream_name']: e for e in policy['tools']}
        self.assertEqual(rows['ha_get_skill_guide']['description'], expected)
        exposed = {t.name: t for t in await self.server.list_tools()}
        guide = exposed['ha_get_skill_guide'].description
        self.assertEqual(guide, expected)
        # File-only: the one served mode names both required arguments ...
        self.assertIn('supply skill and a file path', guide)
        # ... and both other tiers are named as unavailable.
        for tier in ('skill inventory (no arguments)', 'file-list (skill only)'):
            self.assertIn(tier, guide)
        self.assertIn('modes are unavailable', guide)
        # HAMCP-437 I2a-D: the "8.5 description is not reused" comparison read
        # the retired 8.5.0 policy; the exact pin above covers the 9.2.0 text.

    async def test_92_parity_rows_public_surface_matches_the_8_5_snapshot(self):
        """SURFACE_85_LABEL: the 8.5.0 side is Engineering's own published
        surface, generated at 310c6a0e before the 8.5.0 row retired (HAMCP-437
        I2a-D, ANSWER 20261122-0931-01 case 2)."""

        provenance = json.loads((SURFACE_85_FILE.parent / 'provenance.json').read_text(encoding='utf-8'))
        self.assertEqual(hashlib.sha256(SURFACE_85_FILE.read_bytes()).hexdigest(), provenance['sha256'])
        self.assertEqual((provenance['label'], provenance['source_commit']),
                         (SURFACE_85_LABEL, '310c6a0e6b5b0abf9ea20d849d97ddaaf14c7933'))
        self.assertEqual(SURFACE_85['label'], SURFACE_85_LABEL)
        ours = {t.name: t for t in await self.server.list_tools() if t.name in ENT_TOOLS}
        theirs = {t['name']: t for t in SURFACE_85['tools']}
        # HAMCP-501 W6 (G1): the gateway no longer publishes ha_search; the
        # public ha_search is the native tool, compared below.
        self.assertEqual(set(ours), set(ENT_TOOLS) - INTERNAL_ONLY_DELEGATED_READS)
        self.assertEqual(set(theirs), set(ENT_TOOLS))
        native = native_search.registered_tool()
        # Kept: the 8.5 schema with one appended search_types sentence, and
        # the annotations. Improved: an Engineering-owned, complete description
        # (the 8.5 text is cut at its 500-character bound).
        schema = deepcopy(theirs['ha_search']['inputSchema'])
        search_types = schema['properties']['search_types']
        search_types['description'] = f"{search_types['description']} {native_search.SEARCH_TYPES_SENTENCE}"
        self.assertEqual(native.parameters, schema)
        self.assertEqual(native.annotations.model_dump(mode='json'), theirs['ha_search']['annotations'])
        self.assertEqual(len(theirs['ha_search']['description']), 500)
        self.assertNotEqual(native.description, theirs['ha_search']['description'])
        for name in sorted(ours):
            self.assertEqual(ours[name].inputSchema, theirs[name]['inputSchema'], name)
            self.assertEqual(ours[name].annotations.model_dump(mode='json'), theirs[name]['annotations'], name)
            if name != 'ha_get_skill_guide':
                self.assertEqual(ours[name].description, theirs[name]['description'], name)


# HAMCP-437 I2a-D retired EntitiesPending91Tests with the 9.1.0 row (ANSWER
# 20261122-0042-01 item 6, 20261122-0118-01 item 4). Each case maps to its
# 9.2.0 restatement in EntitiesUpstreamWarnings92Tests:
# * row 17 overview partial -> test_row17_overview_services_warning_replaced_stays_partial
#   and test_row17_overview_warnings_only_reports_partial;
# * row 20 todo unavailable -> test_row20_todo_unavailable_warning_reports_partial;
# * row 21 zone gaps -> test_row21_zone_gaps_warning_reports_partial;
# * row 22 floors/areas warnings -> test_row22_floors_area_fields_warning_reports_partial.


# ---------------------------------------------------------------------------
# 9.2.0 upstream warnings, HAMCP-464 (ruling 0132-01)
# ---------------------------------------------------------------------------
class EntitiesUpstreamWarnings92Tests(ParityBase):
    """The four retired 9.1.0 pending outcomes restated on 9.2.0, plus
    single-read state and a warnings-only overview. These rows admit a
    top-level ``warnings`` with no reviewed text (ruling 0132-01): every
    upstream warning is replaced by the fixed code and counted, and the read
    reports partial with the upstream-warnings disclosure."""

    version = '9.2.0'
    REPLACED = NONE_WITHHELD | {'upstream_warnings_replaced': 1}

    def assert_replaced(self, result, *hidden):
        self.assertEqual(result['data']['warnings'], [keys.REPLACED_WARNING])
        text = json.dumps(result)
        for upstream in hidden:
            self.assertNotIn(upstream, text)

    async def test_row17_overview_services_warning_replaced_stays_partial(self):
        template = V92['ha_get_overview']['services_unavailable_partial']
        result = await self.call('ha_get_overview', {}, wrap(template))
        self.assert_success(result, 'ha_get_overview', partial=True,
                            counts=self.REPLACED | {'fields_dropped_by_policy': 3})
        self.assert_sent('ha_get_overview', {})
        self.assertEqual(result['warnings'], [ent.VISIBLE_SCOPE, ent.UPSTREAM_PARTIAL,
                                              ent.UPSTREAM_WARNINGS, keys.REPLACED_PARTIAL])
        self.assertTrue(result['data']['partial'])
        self.assertEqual(result['data']['system_summary'], template['system_summary'])
        self.assert_replaced(result, 'Services unavailable')

    async def test_row17_overview_warnings_only_reports_partial(self):
        # In-memory derivation: upstream warns without setting partial
        # (search/overview.py:239 at 51e3aac7).
        template = {**V92['ha_get_overview']['minimal'],
                    'warnings': ['notifications omitted: include_notifications=False']}
        self.assertNotIn('partial', template)
        result = await self.call('ha_get_overview', {}, wrap(template))
        self.assert_success(result, 'ha_get_overview', partial=True,
                            counts=self.REPLACED | {'fields_dropped_by_policy': 3})
        self.assertEqual(result['warnings'], [ent.VISIBLE_SCOPE, ent.UPSTREAM_WARNINGS,
                                              keys.REPLACED_PARTIAL])
        self.assertNotIn('partial', result['data'])
        self.assert_replaced(result, 'notifications omitted')

    async def test_row19_state_single_warning_reports_partial(self):
        # In-memory derivation: single-read state warns beside data and
        # metadata (search/state.py:138,144 at 51e3aac7).
        args = {'entity_id': 'light.synthetic_lamp'}
        template = {**V92['ha_get_state']['single'],
                    'warnings': ["attribute_keys was ignored because 'attributes' is not in fields="]}
        result = await self.call('ha_get_state', args, wrap(template))
        self.assert_success(result, 'ha_get_state', partial=True, counts=self.REPLACED)
        self.assert_sent('ha_get_state', args)
        self.assertEqual(result['warnings'], [ent.UPSTREAM_WARNINGS, keys.REPLACED_PARTIAL])
        self.assertEqual(result['data']['data'], template['data'])
        self.assert_replaced(result, 'attribute_keys was ignored')

    async def test_row20_todo_unavailable_warning_reports_partial(self):
        args = {'entity_id': 'todo.synthetic_list'}
        template = V92['ha_get_todo']['unavailable_items']
        result = await self.call('ha_get_todo', args, wrap(template))
        self.assert_success(result, 'ha_get_todo', partial=True, counts=self.REPLACED)
        self.assert_sent('ha_get_todo', args)
        self.assertEqual(result['warnings'], [ent.UPSTREAM_WARNINGS, keys.REPLACED_PARTIAL])
        self.assertEqual(result['data'], {**template, 'warnings': [keys.REPLACED_WARNING]})
        self.assert_replaced(result, 'is unavailable')

    async def test_row21_zone_gaps_warning_reports_partial(self):
        template = V92['ha_get_zone']['list_with_gaps']
        result = await self.call('ha_get_zone', {}, wrap(template))
        self.assert_success(result, 'ha_get_zone', partial=True, counts=self.REPLACED)
        self.assert_sent('ha_get_zone', {})
        self.assertEqual(result['warnings'], [ent.UPSTREAM_WARNINGS, keys.REPLACED_PARTIAL])
        self.assertEqual(result['data']['zones'],
                         [{**row, 'friendly_name': row['name']} for row in template['zones']])
        # No occupancy rows came back, so only the icon note applies.
        self.assertEqual(result['data'][ent_92.NOTES], [ent_92.ZONE_ICON_NOTE])
        self.assert_replaced(result, 'persons) is missing')

    async def test_row22_floors_area_fields_warning_reports_partial(self):
        args = {'area_fields': ['synthetic_typo']}
        template = V92['ha_list_floors_areas']['area_fields_unmatched']
        result = await self.call('ha_list_floors_areas', args, wrap(template))
        self.assert_success(result, 'ha_list_floors_areas', partial=True, counts=self.REPLACED)
        self.assert_sent('ha_list_floors_areas', args)
        self.assertEqual(result['warnings'], [ent.UPSTREAM_WARNINGS, keys.REPLACED_PARTIAL])
        self.assertEqual(result['data'], {**template, 'warnings': [keys.REPLACED_WARNING]})
        self.assert_replaced(result, 'matched no record keys')


# ---------------------------------------------------------------------------
# Section 8.3 F3 replays of Phase 1 receipts (class C)
# ---------------------------------------------------------------------------
class F3ReplayTests(ParityBase):
    """FROZEN_LABEL: each case replays a committed 9.1.0 receipt through the
    9.2.0 binding with the same outcome and reason code it had on 9.1.0
    (HAMCP-437 I2a-D, ANSWER 20261122-0907-01 conditions)."""

    version = '9.2.0'

    async def test_91_f3_hidden_id_refusal(self):
        canary = receipt('visibility.json', '/before_toggle')
        self.assertEqual(canary['structuredContent']['data']['context']['id'], HIDDEN_CONTEXT)
        for name, pointer in (('visibility.json', '/after_list_then_toggle_hidden'),
                              ('visibility-registry.json', '/area_hidden_state'),
                              ('visibility-registry.json', '/label_hidden_state'),
                              ('extended.json', '/last_good_hidden_refusal')):
            record = receipt(name, pointer)
            self.assertIn(HIDDEN_ID, json.dumps(record))  # the upstream text names it
            result = await self.call('ha_get_state', {'entity_id': f'input_boolean.{HIDDEN_ID}'}, record)
            self.assertFalse(result['success'], pointer)
            self.assertEqual(result['error_code'], 'entity_not_found', pointer)
            self.assertNotIn('data', result)
            self.assert_no_hidden_value(result)
            self.assertNotIn('editable', json.dumps(result))

    async def test_91_f3_empty_hidden_search(self):
        for name, pointer, query in (('visibility.json', '/search_hidden', 'hamcp319_hidden'),
                                     ('visibility-registry.json', '/area_alias_search', 'HAMCP319 hidden alias')):
            record = receipt(name, pointer)
            self.assertEqual(record['structuredContent']['entities'], [])
            self.assertIs(record['structuredContent']['partial'], False)
            # HAMCP-501 W6 (G1): the frozen receipt replays through the
            # internal-only route as a sub-call page (config-only arguments);
            # native entity search's hidden handling is the W3 engine's.
            args = subcalls.page_arguments('helper', query, True, 0, subcalls.PAGE_SIZE_LADDER[0])
            result = await self.call('ha_search', args, record)
            self.assert_sent('ha_search', args)
            self.assertTrue(result['success'], result)
            self.assertEqual(result['data']['entities'], [])
            self.assertIsInstance(result['data']['warnings'], list)
            # An empty visible result is disclosed as visibility-scoped, never as complete.
            self.assertEqual(result['metadata']['completeness'], 'partial')
            self.assertEqual(result['warnings'][0], ent.VISIBLE_SCOPE)
            self.assert_fixed_warnings(result)
            self.assertNotIn(HIDDEN_CONTEXT, json.dumps(result))

    async def test_91_f3_malformed_or_unavailable_visibility_is_failure(self):
        for name, pointer in (('alternate.json', '/cold_invalid_visibility'),
                              ('more-edges.json', '/registry_unavailable_read')):
            record = receipt(name, pointer)
            self.assertTrue(record['isError'])
            result = await self.call('ha_get_state', {'entity_id': 'input_boolean.hamcp319_visible'}, record)
            self.assertFalse(result['success'], pointer)
            self.assertTrue(result['metadata']['upstream_dispatch_occurred'])
            self.assertNotIn('data', result)
            self.assertNotIn('enforce mode', json.dumps(result))
            self.assert_no_hidden_value(result)

    async def test_91_f3_overview_and_guide_stay_useful(self):
        record = receipt('visibility.json', '/overview_domain')
        result = await self.call('ha_get_overview', {}, record)
        self.assertTrue(result['success'], result)
        self.assertNotIn('mcp_client', result['data'])
        self.assertNotIn('python-httpx', json.dumps(result))
        self.assertEqual(result['data']['domain_stats'], record['structuredContent']['domain_stats'])
        self.assertIn(ent.VISIBLE_SCOPE, result['warnings'])
        self.assert_no_hidden_value(result)
        for name, pointer in (('visibility.json', '/guide_exemption'),
                              ('more-edges.json', '/registry_unavailable_guide')):
            record = receipt(name, pointer)
            args = {'skill': BP, 'file': 'SKILL.md'}
            result = await self.call('ha_get_skill_guide', args, record)
            self.assert_sent('ha_get_skill_guide', {'file': 'SKILL.md'})
            self.assertTrue(result['success'], (pointer, result))
            self.assertEqual(result['data']['skill'], BP)
            self.assertNotIn('how_to_use', result['data'])
            self.assertIn('name: home-assistant-best-practices', result['data']['content'])
            self.assert_fixed_warnings(result)


class F3NestedOmission92Tests(ParityBase):
    """FROZEN_LABEL (HAMCP-437 I2a-D, ANSWER 20261122-0931-01 case 1): through
    the 9.2.0 binding this receipt keeps success, partial, the useful state and
    the absent attributes. The expected warnings changed by ruling 0132-01
    (HAMCP-464): the upstream visibility warning is replaced by the fixed
    upstream-warnings code and counted, instead of STATE_WITHHELD."""

    version = '9.2.0'

    async def test_91_f3_nested_visible_omission(self):
        for name, pointer in (('visibility.json', '/visible_nested_hidden'),
                              ('visibility.json', '/invalid_visibility_config'),
                              ('extended.json', '/last_good_visible_read'),
                              ('visibility-registry.json', '/area_visible_state')):
            record = receipt(name, pointer)
            upstream = record['structuredContent']
            self.assertNotIn('attributes', upstream['data'])
            args = {'entity_id': 'input_boolean.hamcp319_visible'}
            result = await self.call('ha_get_state', args, record)
            self.assert_sent('ha_get_state', args)
            self.assertTrue(result['success'], (pointer, result))
            state = result['data']['data']
            self.assertEqual(state['state'], 'off')  # useful data stays available
            self.assertEqual(state['entity_id'], 'input_boolean.hamcp319_visible')
            self.assertNotIn('attributes', state)  # never fabricated as {}
            self.assertEqual(result['metadata']['completeness'], 'partial', pointer)
            self.assertEqual(result['warnings'], [ent.UPSTREAM_WARNINGS, keys.REPLACED_PARTIAL], pointer)
            self.assertEqual(result['data']['upstream_warnings_replaced'], 1, pointer)
            self.assert_fixed_warnings(result)
            self.assert_no_hidden_value(result)
            # Typed actions refuse on incomplete state; attribute-free contracts stay useful.
            with self.assertRaises(FanRefusal) as caught:
                fan_checked_state({**state, 'entity_id': 'fan.hamcp319_visible'}, 'fan.hamcp319_visible')
            self.assertEqual(caught.exception.details['category'], 'target_state_unavailable')
            derived = {**state, 'entity_id': 'input_select.hamcp319_visible'}  # in-memory test derivation
            with self.assertRaises(HelperRefusal) as caught:
                helper_checked_state(derived, 'input_select.hamcp319_visible')
            self.assertEqual(caught.exception.details['category'], 'helper_attributes_unproven')
            self.assertEqual(helper_checked_state(state, state['entity_id'])['state'], 'off')
            self.assertEqual(power_checked_state(state, state['entity_id'])['state'], 'off')


# Ledger rows 25-28 (design section 7): the compiled blueprint list/get wrapper
# and the three platform wrappers, on the shipped 9.2.0 binding.
#
# HAMCP-437 I2a-D retired the 8.5.0 and 9.1.0 rows (architect ruling
# 2026-11-07 item 1 evidence classes no longer apply) and moved the tests to
# 9.2.0:
# * row 25 replays the same frozen recordings as the 9.1.0 test did
#   (tests/fixtures/ha_mcp_8_5_0/read-results.json standalone-call-04/05 and
#   tests/fixtures/ha_mcp_8_6_0/read-results.json standalone[11]) through the
#   9.2.0 binding, with the same outcome. They are frozen inputs, not
#   evidence of 9.2.0 upstream output; the 9.2.0 receipt proof is
#   tests/test_hamcp371_blueprint_9_2.py.
# * rows 26-28 check the published schema and the refusal set on 9.2.0. The
#   generated 8.6.0 result replays retire: through the 9.2.0 key layer two of
#   them add the upstream_warnings_replaced completeness reason, so the outcome
#   is not the same. The committed 9.2.0 receipts are replayed by
#   test_hamcp129_platform_reads.test_committed_920_receipts_project_through_the_allowlists.
READ_85 = json.loads((ROOT / 'tests/fixtures/ha_mcp_8_5_0/read-results.json').read_text(encoding='utf-8'))
READ_86 = json.loads((ROOT / 'tests/fixtures/ha_mcp_8_6_0/read-results.json').read_text(encoding='utf-8'))
BLUEPRINT = 'ha_get_blueprint'
PLATFORM_ROW = {26: platform_adapter.APP, 27: platform_adapter.INTEGRATION, 28: platform_adapter.HEALTH}
BLUEPRINT_REFUSALS = (
    {'action': 'delete'}, {'action': 'import'}, {'url': 'https://example.invalid/x.yaml'},
    {'path': '../secrets.yaml'}, {'path': 'a/../b.yaml'}, {'path': 'https://example.invalid/x.yaml'},
    {'path': 'a/*.yaml'}, {'path': 'a/b.txt'}, {'path': ''}, {'domain': 'template'},
    {'domain': 'automation', 'path': 'x.yaml', 'config': {}},
)


class ParityPlatformRowsTests(unittest.IsolatedAsyncioTestCase):
    def _blueprint_cases(self, version):
        captured_list = READ_85['standalone-call-04.json']
        captured_get = READ_85['standalone-call-05.json']
        cases = [({}, captured_list), ({'path': 'assessment/read.yaml'}, captured_get)]
        replay = READ_86['standalone'][11]
        self.assertEqual((replay['public_name'], replay['upstream_name']), (BLUEPRINT, 'ha_manage_blueprints'))
        cases.insert(0, ({'domain': 'automation'}, {'arguments': replay['arguments'], 'result': replay['result']}))
        return cases

    async def _row_25(self, version):
        gateway, transport = await platform_tests.make_gateway(version)
        tool = gateway._registered_tool_registry.snapshot()[BLUEPRINT]
        self.assertEqual(tool.parameters, blueprint_adapter.PUBLIC_SCHEMA)
        self.assertIs(tool.annotations.readOnlyHint, True)
        self.assertNotIn('ha_manage_blueprints', gateway._registered_tool_registry.snapshot())
        for public, record in self._blueprint_cases(version):
            with self.subTest(version=version, public=public):
                transport.sent.clear()
                transport.respond = lambda name, args, record=record: deepcopy(record['result'])
                result = await platform_tests.call(gateway, BLUEPRINT, public)
                self.assertTrue(result['success'], result)
                self.assertFalse(result['metadata']['fallback_occurred'])
                self.assertEqual(result['metadata']['upstream_version'], version)
                self.assertEqual(transport.sent, [('ha_manage_blueprints', record['arguments'])])
                expected = record['result']['structuredContent']['data']
                if 'blueprints' in expected:
                    self.assertEqual([item['path'] for item in result['data']['data']['blueprints']],
                                     [item['path'] for item in expected['blueprints']])
                else:
                    self.assertEqual(result['data']['data']['path'], expected['path'])
        transport.sent.clear()
        for arguments in BLUEPRINT_REFUSALS:
            with self.subTest(version=version, refusal=arguments):
                platform_tests.refused(self, await platform_tests.call(gateway, BLUEPRINT, arguments))
        self.assertEqual(transport.sent, [])

    async def _platform_row(self, version, row):
        tool_name = PLATFORM_ROW[row]
        gateway, transport = await platform_tests.make_gateway(version)
        published = gateway._registered_tool_registry.snapshot()[tool_name]
        self.assertEqual(published.parameters, platform_adapter.PUBLIC_SCHEMAS[tool_name])
        self.assertIs(published.annotations.readOnlyHint, True)
        for arguments in platform_tests.REFUSALS[tool_name]:
            with self.subTest(version=version, refusal=arguments):
                result = await platform_tests.call(gateway, tool_name, arguments)
                platform_tests.refused(self, result)
                self.assertIn(result['details']['failure_category'], platform_tests.REFUSED, result)
        self.assertEqual(transport.sent, [])

    # HAMCP-437 I2a-D: test_91_parity_row_25..28 become test_92_parity_row_25..28;
    # test_85_parity_row_25..28 retire with the 8.5.0 row.
    async def test_92_parity_row_25_blueprint(self):
        await self._row_25('9.2.0')

    async def test_92_parity_row_26_app(self):
        await self._platform_row('9.2.0', 26)

    async def test_92_parity_row_27_integration(self):
        await self._platform_row('9.2.0', 27)

    async def test_92_parity_row_28_system_health(self):
        await self._platform_row('9.2.0', 28)


class FixtureProvenanceTests(unittest.TestCase):
    def test_every_template_has_provenance(self):
        declared = PROVENANCE['fixtures']
        for version, block in RESPONSES.items():
            for tool, cases in block.items():
                self.assertIn(tool, ENT_TOOLS)
                for case in cases:
                    entry = declared[f'{version}/{tool}/{case}']
                    self.assertEqual(entry['class'], 'S')
                    self.assertTrue(entry['sources'])
        self.assertEqual(PROVENANCE['upstream']['source_commit'], reads_9_1.SOURCE)
        self.assertEqual(PROVENANCE['frozen_input_label'], FROZEN_LABEL)
        for key, entry in declared.items():
            if entry['class'] == 'C':
                name = entry['receipt'].rsplit('/', 1)[1]
                self.assertEqual(entry['sha256'], SUMS[f'receipts/{name}'], key)


    def test_92_every_template_has_provenance(self):
        declared = PROVENANCE_92['fixtures']
        self.assertEqual(set(RESPONSES_92), {'v92'})
        seen = set()
        for tool, cases in V92.items():
            self.assertIn(tool, ENT_TOOLS)
            for case in cases:
                key = f'v92/{tool}/{case}'
                seen.add(key)
                entry = declared[key]
                self.assertEqual(entry['class'], 'S', key)
                self.assertTrue(entry['sources'], key)
                for source in entry['sources']:
                    # Every citation names a hashed 9.2.0 source file.
                    self.assertIn(source.split(':', 1)[0], PROVENANCE_92['source_files'], key)
        self.assertEqual(set(declared), seen)
        self.assertEqual(set(V92), set(ENT_TOOLS))
        self.assertEqual(PROVENANCE_92['upstream']['version'], '9.2.0')
        self.assertEqual(PROVENANCE_92['upstream']['source_commit'], reads_9_2.SOURCE)
        for path, digest in PROVENANCE_92['source_files'].items():
            self.assertRegex(digest, r'^[0-9a-f]{64}$', path)

if __name__ == '__main__':
    unittest.main()
