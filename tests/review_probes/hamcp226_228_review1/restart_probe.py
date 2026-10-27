import sys,unittest
from pathlib import Path
sys.path[:0]=[str(Path.cwd()/'tests'),str(Path.cwd()/'hass_mcp_engineering_beta')]
from unittest.mock import patch
import test_hamcp228_baseline_store as b
from ha_mcp_engineering.audit_baseline import capture_contracts as c
from ha_mcp_engineering.ha_core_readmission.coordinator import LocalCoreGenerationAllocator
class Restart(b.Harness):
 async def test_new_runtime_changed_core_must_not_resume_old_authority(self):
  first_generation=LocalCoreGenerationAllocator().next_generation()
  new_generation=LocalCoreGenerationAllocator().next_generation()
  self.assertEqual(first_generation,new_generation)
  before,_=self.service()
  with patch.object(c,'COLLECTION_SECONDS',10):page=await self.capture(before)
  self.core=b.Core()
  self.core.generation=new_generation
  self.core.current_observation.version='2026.10.3'
  after,_=self.service()
  self.assertIsNot(before,after)
  with self.assertRaises(c.CaptureError):
   await self.capture(after,resume_capture_id=b.capture_id(page))
if __name__=='__main__':unittest.main(verbosity=2)
