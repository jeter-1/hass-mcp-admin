import copy,json,sys,unittest
from pathlib import Path
sys.path[:0]=[str(Path.cwd()/'tests'),str(Path.cwd()/'hass_mcp_engineering_beta')]
import test_hamcp228_baseline_store as b
import test_hamcp226_named_sources as n
from ha_mcp_engineering import untrusted_text
from ha_mcp_engineering.audit_baseline import capture_contracts as c
from unittest.mock import patch
class Store(b.Harness):
 async def test_missing_index_must_preserve_committed_records(self):
  svc,_=self.service();page=await self.capture(svc);key=b.capture_id(page)
  record=self.record_path(svc,key);before=record.read_bytes()
  svc.store.index_path.unlink()
  try:await svc.load(key,b.OWNER)
  except c.CaptureError:pass
  self.assertTrue(record.exists(),'loading with a missing index deleted a committed baseline')
  self.assertEqual(record.read_bytes(),before)
 async def test_resume_refuses_new_core_version_same_generation(self):
  svc,_=self.service()
  with patch.object(c,'COLLECTION_SECONDS',10):page=await self.capture(svc)
  self.core.current_observation.version='2026.10.3'
  with self.assertRaises(c.CaptureError):await self.capture(svc,resume_capture_id=b.capture_id(page))
 async def test_comparison_has_continuation_for_omitted_rows(self):
  svc,_=self.service(b.fixture(300));page=await self.capture(svc)
  report=await svc.compare(earlier_capture_id=b.capture_id(page),recorded_fingerprints=[],fingerprint_model=b.FINGERPRINT_MODEL)
  self.assertTrue(report['omitted_detail_count']>0)
  self.assertTrue(report.get('next_cursor') or report.get('pagination',{}).get('next_cursor'),'omitted rows have no continuation')
class Text(unittest.IsolatedAsyncioTestCase):
 async def test_registry_platform_screened_in_integrity(self):
  rest,ws=n.house();original=ws.command
  needle='BEGIN'+'PRIVATE'+'KEY'
  async def command(payload):
   rows=await original(payload)
   for row in rows:row['platform']=needle
   return rows
  ws.command=command
  result=await n.integrity(rest,ws)
  self.assertIsNotNone(untrusted_text.screen(needle))
  self.assertNotIn(needle,n.rendered(result),'registry platform escaped default+PEM screen')
 async def test_orphan_identifier_screened_in_integrity(self):
  rest,ws=n.house();original=ws.command
  needle='begin'+'private'+'key';entity='sensor.'+needle
  async def command(payload):
   return (await original(payload))+[{'entity_id':entity,'platform':'demo','disabled_by':None}]
  ws.command=command
  index,_=await n.build(rest,ws)
  svc=n.ConfigurationIntegrityAnalysisService(n.DirectHaIntegrityProvider(index,rest,ws,secret=n.SECRET),cursor_key=b'k'*32)
  result=await svc.analyze(source_types=list(n.SOURCE_TYPES),include_orphan_candidates=True,detail_level='evidence',limit=100,refresh_index=True)
  self.assertIsNotNone(untrusted_text.screen(entity))
  self.assertNotIn(needle,n.rendered(result),'orphan identifier escaped default+PEM screen')
if __name__=='__main__':unittest.main(verbosity=2)
