import sys,unittest,copy,json
from pathlib import Path
sys.path[:0]=[str(Path.cwd()/'tests'),str(Path.cwd()/'hass_mcp_engineering_beta')]
from unittest.mock import patch
from types import SimpleNamespace
import test_hamcp226_named_sources as n
from ha_mcp_engineering.tools import analysis
from ha_mcp_engineering.dependency import script_sources as ss,bounded_sources as bs
class Additional(unittest.IsolatedAsyncioTestCase):
 async def test_registry_platform_screened_in_tool_envelope(self):
  rest,ws=n.house();original=ws.command
  needle='BEGIN'+'PRIVATE'+'KEY'
  async def command(payload):
   rows=await original(payload)
   for row in rows:row['platform']=needle
   return rows
  ws.command=command
  index,_=await n.build(rest,ws)
  service=n.ConfigurationIntegrityAnalysisService(n.DirectHaIntegrityProvider(index,rest,ws,secret=n.SECRET),cursor_key=b'k'*32)
  with patch.object(analysis.CONFIGURATION_INTEGRITY_ANALYSIS,'require',return_value=service):
   result=await analysis.configuration_integrity_analysis(source_types=list(n.SOURCE_TYPES),include_orphan_candidates=False,detail_level='evidence',limit=20,refresh_index=True)
  self.assertTrue(json.loads(result)['success'])
  self.assertNotIn(needle,result)
 async def test_script_overflow_counts_missing_source_reason_pairs(self):
  candidates=(('script.alpha','alpha'),('script.zulu','zulu'))
  config={'sequence':[{'service':'light.turn_on','target':{'entity_id':n.big_template()}},{'service':'light.turn_off','target':{'entity_id':n.big_template()}}]}
  loaded=ss.LoadedScriptSource(outcomes={entity:((True,{'config':copy.deepcopy(config)}),(True,{})) for entity,key in candidates},available=frozenset(e for e,k in candidates))
  with patch.object(ss,'MAX_SCRIPT_BOUNDED_OBLIGATIONS',2):
   diagnostics=await ss.collect_script_diagnostics([],[],registry_complete=True,reader_factory=lambda:SimpleNamespace(current=lambda:True,provenance='synthetic'),secret='',concurrency=1,selection=ss.ScriptSelection(candidates,(),False),loaded=loaded)
  result=bs.bounded_source_entries(diagnostics.bounded_obligations)
  self.assertEqual(result.total,2,'obligation cap loses a distinct bounded source without counting it')
if __name__=='__main__':unittest.main(verbosity=2)
