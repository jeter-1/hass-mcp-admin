import sys,asyncio,json
from pathlib import Path
sys.path[:0]=[str(Path.cwd()/'tests'),str(Path.cwd()/'hass_mcp_engineering_beta')]
from unittest.mock import patch
import test_hamcp226_named_sources as n
from ha_mcp_engineering.tools import analysis
async def main():
 leaks=[]
 for label,(value,needle) in n.cases.withheld_cases().items():
  rest,ws=n.house();original=ws.command
  async def command(payload):
   rows=await original(payload)
   for row in rows:row['platform']=value
   return rows
  ws.command=command
  index,_=await n.build(rest,ws)
  service=n.ConfigurationIntegrityAnalysisService(n.DirectHaIntegrityProvider(index,rest,ws,secret=n.SECRET),cursor_key=b'k'*32)
  with patch.object(analysis.CONFIGURATION_INTEGRITY_ANALYSIS,'require',return_value=service):
   result=await analysis.configuration_integrity_analysis(source_types=list(n.SOURCE_TYPES),include_orphan_candidates=False,detail_level='evidence',limit=20,refresh_index=True)
  assert json.loads(result)['success'],label
  if needle in result:leaks.append(label)
 print('Leaking registry_platform corpus cases:',leaks)
 assert not leaks,leaks
asyncio.run(main())
