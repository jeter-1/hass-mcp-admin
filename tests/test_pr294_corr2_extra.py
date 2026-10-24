"""Independent correction-batch-2 probes; synthetic data and in-process gateway only."""
import json
import unittest
from urllib.parse import quote

import test_hamcp132_configuration_definitions as t
from ha_mcp_engineering.configuration_definitions import credentials as c
from ha_mcp_engineering.configuration_definitions.contracts import MAX_TITLE, MAX_TEXT
from ha_mcp_engineering.providers import upstream_yaml_reader as reader

NEEDLE = 'SYNTHprobeQ9X'
INVISIBLES = (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0xAD)

def reader_envelope(text):
    parsed = [{'sensor': [{'name': 'Probe', 'state': "{{ '" + text + "' }}"}]}]
    payload = {'success': True, 'yaml_path': 'template', 'matches': [
        {'file': 'configuration.yaml', 'yaml_path': 'template', 'parsed': parsed}],
        'count': 1, 'files_searched': 1, 'files_unreadable': 0}
    result = reader.project_success({'content': [{'type': 'text', 'text': json.dumps(payload)}]},
                                   'template', known_secrets=(t.HA_TOKEN, t.ACCESS_SECRET))
    values = t.yaml()
    values['template']['data'] = result.envelope
    return values

class ExtraChecks(unittest.IsolatedAsyncioTestCase):
    async def test_title_truncation_all_schemes(self):
        for scheme in ('', 'https://', 'anything+driver://', 'mysql://', 'mqtt://'):
            for cut in ('middle', 'beyond'):
                with self.subTest(scheme=scheme, cut=cut):
                    credential = scheme + 'synthetic:' + NEEDLE
                    padding = MAX_TITLE-len(credential)-1 if cut == 'middle' else MAX_TITLE
                    title = 'x'*padding + ' ' + credential + '@synthetic.invalid'
                    core = t.owner_core()
                    core['config_entries/get']['template'][0]['title'] = title
                    response, data, audit = await t.serve_through_gateway(
                        {'integrations': ['template']}, t.yaml(), core)
                    self.assertNotIn(NEEDLE, response + audit)
                    self.assertFalse(data['data']['complete'])

    async def test_title_invisible_variants_withheld(self):
        for cp in INVISIBLES:
            with self.subTest(codepoint=hex(cp)):
                title = 'https://synthetic.invalid/?pass' + chr(cp) + 'word=' + NEEDLE
                core = t.owner_core()
                core['config_entries/get']['template'][0]['title'] = title
                response, data, audit = await t.serve_through_gateway(
                    {'integrations': ['template']}, t.yaml(), core)
                self.assertNotIn(NEEDLE, response + audit)
                self.assertEqual(data['data']['ui_entries'][0]['title'], c.UNVERIFIABLE_MARKER)
                self.assertFalse(data['data']['complete'])

    async def test_yaml_new_visible_confusables_withheld(self):
        for word in ('p\u03b1ssword', 'p\u0430ssw\u03bfrd', 'passw\u043erd'):
            with self.subTest(word=word):
                text = 'https://synthetic.invalid/?' + word + '=' + NEEDLE
                response, data, audit = await t.serve_through_gateway(
                    {'integrations': ['template']}, reader_envelope(text), t.owner_core())
                self.assertNotIn(NEEDLE, response + audit)
                self.assertFalse(data['data']['complete'])

    async def test_yaml_invisible_query_password_withheld(self):
        for cp in INVISIBLES:
            for encoded in (False, True):
                with self.subTest(codepoint=hex(cp), encoded=encoded):
                    marker = quote(chr(cp), safe='') if encoded else chr(cp)
                    text = 'https://synthetic.invalid/?pass' + marker + 'word=' + NEEDLE
                    response, data, audit = await t.serve_through_gateway(
                        {'integrations': ['template']}, reader_envelope(text), t.owner_core())
                    self.assertNotIn(NEEDLE, response, 'credential survives real reader and final gateway response')
                    self.assertFalse(data['data']['complete'])

    async def test_final_envelope_screen_precedes_string_bound(self):
        env = t.setup()
        try:
            base = await env.service.report((), ('template',))
            for prefix in ('', 'https://', 'anything://'):
                with self.subTest(prefix=prefix):
                    value = prefix + 'synthetic:' + NEEDLE
                    report = dict(base, probe='x'*(MAX_TEXT-len(value)-1)+' '+value+'@synthetic.invalid')
                    omissions = list(report.pop('omissions'))
                    result = env.service._finish(report, omissions, omissions.append)
                    self.assertNotIn(NEEDLE, json.dumps(result))
                    self.assertFalse(result['complete'])
        finally:
            t.end_request(env.token)

if __name__ == '__main__':
    unittest.main()
