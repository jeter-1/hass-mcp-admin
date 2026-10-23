"""Independent final32 review probes; synthetic values only, no network."""
import copy
import json
import unittest
from urllib.parse import quote

import test_hamcp132_configuration_definitions as t
import test_pr294_corr2_extra as prior
from ha_mcp_engineering.configuration_definitions import credentials as c

NEEDLE = 'SYNTHfinalprobeQ9X'
URI = 'mysql://synthetic:' + NEEDLE + '@synthetic.invalid/db'

class FinalChecks(unittest.IsolatedAsyncioTestCase):
    async def report(self, values, integration):
        env = t.setup(values, t.owner_core())
        try:
            return await env.service.report((), (integration,))
        finally:
            t.end_request(env.token)

    async def test_control_character_passwords_through_real_reader_and_gateway(self):
        for point in (9, 10, 13):
            for rounds in range(3):
                with self.subTest(point=hex(point), encodings=rounds):
                    marker = chr(point)
                    for _ in range(rounds):
                        marker = quote(marker, safe='')
                    text = 'https://synthetic.invalid/?pass' + marker + 'word=' + NEEDLE
                    response, data, audit = await t.serve_through_gateway(
                        {'integrations': ['template']}, prior.reader_envelope(text), t.owner_core())
                    self.assertTrue(data['success'])
                    self.assertNotIn(NEEDLE, response, 'credential survives reader and final gateway output')
                    self.assertFalse(data['data']['complete'])

    async def test_unicode_categories_encoded_and_double_encoded(self):
        # Cc, Cf, Cs, Co, Cn, Zl, Zp and default-ignorable Mn/Lo.
        for point in (0x1F, 0x200E, 0xD800, 0xE123, 0x378, 0x2028, 0x2029, 0x034F, 0x115F, 0xFE0F):
            for rounds in range(3):
                with self.subTest(point=hex(point), encodings=rounds):
                    text = 'Probe' + chr(point) + 'tail'
                    for _ in range(rounds):
                        text = quote(text, safe='', errors='surrogatepass')
                    values = t.yaml(template=[{'sensor': [{'name': 'Probe', 'state': text}]}])
                    result = await self.report(values, 'template')
                    self.assertNotEqual(result['definitions'][0]['fields']['state'], text)
                    self.assertFalse(result['complete'])

    async def test_benign_unicode_definitions_remain_visible(self):
        for text in ('°C', 'Küche', 'Lamp 💡'):
            with self.subTest(text=text):
                result = await self.report(t.yaml(template=[{'sensor': [{'name': 'Probe', 'state': text}]}]), 'template')
                self.assertEqual(result['definitions'][0]['fields']['state'], text)
                self.assertTrue(result['complete'])

    async def test_policy_redaction_scope(self):
        cases = [
            ('sql', t.yaml(sql=[{'name': 'Probe', 'query': 'SELECT 1', 'column': 'n', 'db_url': URI}]), True),
            ('sql', t.yaml(sensor=[{'platform': 'sql', 'name': 'Probe', 'query': 'SELECT 1', 'column': 'n', 'db_url': URI}]), True),
            ('template', t.yaml(template=[{'sensor': [{'name': 'Probe', 'state': '1', 'db_url': URI}]}]), False),
            ('template', t.yaml(template=[{'sensor': [{'name': 'Probe', 'state': '1', 'attributes': {'db_url': URI}}]}]), False),
            ('sql', t.yaml(sql=[{'name': 'Probe', 'query': {'db_url': URI}, 'column': 'n'}]), False),
            ('filter', t.yaml(sensor=[{'platform': 'filter', 'name': 'Probe', 'entity_id': 'sensor.a', 'db_url': URI}]), False),
        ]
        for integration, values, complete in cases:
            with self.subTest(integration=integration, expected_complete=complete):
                result = await self.report(values, integration)
                self.assertNotIn(NEEDLE, json.dumps(result))
                self.assertEqual(result['complete'], complete)

    async def test_derived_outputs_have_no_withheld_input(self):
        cases = [
            ('sql', t.yaml(sql=[{'name': URI, 'query': 'SELECT 1', 'column': 'n'}])),
            ('template', t.yaml(template=[{'unique_id': URI, 'sensor': [{'name': 'Probe', 'unique_id': 'one', 'state': '1'}]}])),
            ('utility_meter', t.yaml(utility_meter={'energy': {'source': 'sensor.a', 'unique_id': 'meter', 'tariffs': [URI]}})),
            ('group', t.yaml(group={URI: {'entities': ['light.a']}})),
            ('trend', t.yaml(binary_sensor=[{'platform': 'trend', 'sensors': {URI: {'entity_id': 'sensor.a'}}}])),
            ('derivative', t.yaml(sensor=[{'platform': 'derivative', 'source': URI}])),
            ('min_max', t.yaml(sensor=[{'platform': 'min_max', 'type': URI, 'entity_ids': ['sensor.a']}])),
            ('filter', t.yaml(sensor=[{'platform': 'filter', 'entity_id': 'sensor.a', 'entity_namespace': URI}])),
            ('sql', t.yaml(sensor=[{'platform': 'sql', 'name': URI, 'query': 'SELECT 1', 'column': 'n'}])),
        ]
        for integration, values in cases:
            with self.subTest(integration=integration):
                result = await self.report(values, integration)
                rendered = json.dumps(result).casefold()
                self.assertNotIn(NEEDLE.casefold(), rendered)
                self.assertFalse(result['complete'])
                for row in result['definitions']:
                    for entity in row['expected_entities']:
                        if entity['entity_domain'] != 'select':
                            self.assertIsNone(entity['derived_entity_id'])

    async def test_dependency_and_legacy_derivations_are_empty(self):
        values = t.yaml(group={'group_probe': {'entities': ['sensor.safe', URI]}},
                        sensor=[{'platform': 'template', 'name': URI, 'value_template': '1'}])
        collector = t.Definitions({key: value['data'] for key, value in values.items()}, (t.HA_TOKEN, t.ACCESS_SECRET))
        records = collector.collect()
        group = next(row for row in records if row['integration'] == 'group')
        legacy = next(row for row in records if row['form'] == 'legacy_platform')
        self.assertEqual(group['dependencies'], [])
        self.assertTrue(group['dependencies_withheld'])
        self.assertIsNone(legacy['legacy_object_id'])

if __name__ == '__main__':
    unittest.main()
