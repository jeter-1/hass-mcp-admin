import sys,unittest,copy
from pathlib import Path
sys.path[:0]=[str(Path.cwd()/'tests'),str(Path.cwd()/'hass_mcp_engineering_beta')]
from types import SimpleNamespace
import test_hamcp226_named_sources as n
from ha_mcp_engineering.dependency import script_sources as ss,bounded_sources as bs
class FullBound(unittest.IsolatedAsyncioTestCase):
 async def test_full_script_bound_preserves_distinct_source_total(self):
  count=ss.MAX_SCRIPT_BOUNDED_OBLIGATIONS//2+1
  candidates=tuple((f'script.s{i:04d}',f's{i:04d}') for i in range(count))
  template=n.big_template()
  config={'sequence':[{'service':'light.turn_on','target':{'entity_id':template}},{'service':'light.turn_off','target':{'entity_id':template}}]}
  loaded=ss.LoadedScriptSource(outcomes={entity:((True,{'config':config}),(True,{})) for entity,key in candidates},available=frozenset(e for e,k in candidates))
  diagnostics=await ss.collect_script_diagnostics([],[],registry_complete=True,reader_factory=lambda:SimpleNamespace(current=lambda:True,provenance='synthetic'),secret='',concurrency=1,selection=ss.ScriptSelection(candidates,(),False),loaded=loaded)
  result=bs.bounded_source_entries(diagnostics.bounded_obligations)
  self.assertEqual(diagnostics.profile['successful_documents'],count,'all source configs must be inspected')
  self.assertEqual(result.total,count)
  self.assertEqual(result.omitted,count-len(result.entries))
if __name__=='__main__':unittest.main(verbosity=2)
