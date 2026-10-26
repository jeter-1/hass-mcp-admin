"""Independent combined-ANSI regression. Synthetic fixture journals only."""
import json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path.cwd()/'tests'),str(Path.cwd()/'hass_mcp_engineering_beta')]
import test_hamcp224_review1_corrections as a
import test_pr313_independent_review as old
S='SYNTHETIC'+'ansiCombined79'
def forms():
 return {'password':'\x1b[pass\x1b[32mword='+S,
         'bearer':'\x1b[Be\x1b[0marer '+S,
         'token':'\x1b[1;To\x1b[31mken='+S}
class CombinedAnsi(a.Harness):
 async def test_combined_ansi_credential_withheld_in_every_field(self):
  for label,value in forms().items():
   for field,source in [('MESSAGE','supervisor'),('SYSLOG_IDENTIFIER','host:kernel'),('_KERNEL_SUBSYSTEM','host:kernel'),('_UDEV_SYSNAME','host:kernel')]:
    with self.subTest(form=label,field=field):
     page=await self.planted(value.encode(),field,source)
     self.assertNotIn(S,json.dumps(page),'combined ANSI credential reached output')
     self.assertFalse(page['complete'])
 async def test_components_individually_withheld(self):
  # Each constituent transformation is already within the accepted rules.
  for value in ('\x1b[password='+S, 'pass\x1b[32mword='+S, '\x1b[Bearer '+S, 'Be\x1b[0marer '+S):
   with self.subTest(component=value.split('=')[0]):
    page=await self.planted(value.encode())
    self.assertNotIn(S,json.dumps(page))
    self.assertFalse(page['complete'])
class Wire(unittest.TestCase):
 def test_combined_ansi_credential_not_in_mcp_response(self):
  root=old.author.journal_root();self.addCleanup(old.author.shutil.rmtree,root)
  lib=old.NativeFieldOverlay('MESSAGE',forms()['password'].encode())
  with patch.object(old.gateway_tests,'journald_off_reader',lambda:old.author.reader(root,library=lambda:lib)):
   _,_,wire=old.gateway_tests.IncidentLogGatewayStackTests().call({'sources':['supervisor'],'start':old.author.iso(0),'end':old.author.iso(1)})
  self.assertNotIn(S,wire,'combined ANSI credential reached authenticated wire')
if __name__=='__main__':unittest.main(verbosity=2)
