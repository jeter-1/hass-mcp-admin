"""Independent PR313 delta regressions; synthetic fixture journals only.
Run from the candidate repository root; set TMPDIR under /var/tmp.
"""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import quote
sys.path[:0] = [str(Path.cwd() / 'tests'), str(Path.cwd() / 'hass_mcp_engineering_beta')]
import test_pr313_independent_review as original
import test_hamcp224_review1_corrections as author

NEEDLE = 'SYNTHETIC' + 'compact79'
FIELDS = (('MESSAGE', 'supervisor'), ('SYSLOG_IDENTIFIER', 'host:kernel'),
          ('_KERNEL_SUBSYSTEM', 'host:kernel'), ('_UDEV_SYSNAME', 'host:kernel'))
def forms():
    argv = ['mosquitto_pub', '-P', NEEDLE]
    return {
        'python_argv': repr(argv),
        'json_argv': json.dumps(argv),
        'quoted_shell': 'mosquitto_pub "-P" "' + NEEDLE + '"',
        'encoded_argv': quote(json.dumps(argv), safe=''),
        'short_option_tab': 'mosquitto_pub -\tP ' + NEEDLE,
        'short_option_newline': 'mosquitto_pub -\nP ' + NEEDLE,
        'split_bearer': 'Be\tarer ' + NEEDLE,
    }

class DeltaResidualTests(author.Harness):
    async def test_labelled_credentials_withheld_in_every_field(self):
        for label, value in forms().items():
            for field, source in FIELDS:
                with self.subTest(form=label, field=field):
                    page = await self.planted(value.encode(), field, source)
                    self.assertNotIn(NEEDLE, json.dumps(page), 'credential reached output')
                    self.assertTrue(all('withheld' in row for row in page['entries']))
                    self.assertFalse(page['complete'])

    async def test_original_control_text_is_screened_before_ansi_removal(self):
        value = '\x1b[31mordinary\x1b[0m'
        self.assertIsNotNone(author.credentials.screen(value, (), labelled=True))
        for field, source in FIELDS:
            with self.subTest(field=field):
                page = await self.planted(value.encode(), field, source)
                self.assertTrue(all('withheld' in row for row in page['entries']))
                self.assertFalse(page['complete'])

    async def test_level_filtered_withholding_is_partial(self):
        page = await self.planted(('pass' + 'word=' + NEEDLE).encode(), min_level='critical')
        self.assertEqual(page['returned'], 0)
        self.assertFalse(page['complete'], 'withholding lost after severity filtering')
        self.assertEqual(page['sources'][0]['status'], 'partial')
        self.assertTrue(page['omissions'])

class GatewayResidualTests(unittest.TestCase):
    def test_argv_credential_never_reaches_mcp_wire(self):
        root = original.author.journal_root()
        self.addCleanup(original.author.shutil.rmtree, root)
        library = original.NativeFieldOverlay('MESSAGE', forms()['json_argv'].encode())
        with patch.object(original.gateway_tests, 'journald_off_reader',
                          lambda: original.author.reader(root, library=lambda: library)):
            _, _, wire = original.gateway_tests.IncidentLogGatewayStackTests().call(
                {'sources': ['supervisor'], 'start': original.author.iso(0), 'end': original.author.iso(1)})
        self.assertNotIn(NEEDLE, wire, 'credential reached authenticated MCP wire')

if __name__ == '__main__':
    unittest.main(verbosity=2)
