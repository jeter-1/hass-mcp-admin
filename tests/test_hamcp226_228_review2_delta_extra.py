"""PR317 independent correction probes. Synthetic only; offline local stores."""
import copy,errno,json,unittest
from unittest.mock import patch,AsyncMock
import test_hamcp228_baseline_store as b
import test_hamcp226_228_review1_corrections as corr
import hamcp226_credential_cases as cases
from ha_mcp_engineering.audit_baseline.capture_runtime import AUTOMATION_BASELINE_CAPTURE
from ha_mcp_engineering.tools import audit_baseline as tool
from ha_mcp_engineering.audit_baseline import capture_contracts as c
from ha_mcp_engineering.audit_baseline.models import FINGERPRINT_MODEL

class Extra(b.Harness):
 async def call(self,service):
  with patch.object(AUTOMATION_BASELINE_CAPTURE,'require',return_value=service):
   return json.loads(await tool.capture_automation_baseline(limit=100))

 async def test_new_unscreened_capture_diagnostic_never_reaches_disk(self):
  service,_=self.service()
  original=service.provider.collect
  secret='syntheticNewField317Needle'
  value='postgresql'+':'+'//user:'+secret+'@host.invalid/db'
  async def collect(*args,**kwargs):
   report=await original(*args,**kwargs)
   report['diagnostics']['future_field']={'nested':[value]}
   return report
  with patch.object(service.provider,'collect',collect):
   result=await self.call(service)
  self.assertNotIn(secret,json.dumps(result),'response backstop failed')
  disk=b''.join(p.read_bytes() for p in service.store.root.rglob('*') if p.is_file())
  self.assertFalse(secret.encode() in disk,'final-screen refusal occurred after the credential was committed')

 async def test_permission_denied_index_rebuilds_without_losing_valid_baseline(self):
  service,_=self.service()
  page=await service.capture(limit=100);key=b.capture_id(page)
  path=service.store.records/(key+'.json');before=path.read_bytes()
  index=service.store.index_path
  index.chmod(0)
  try:
   with self.assertRaises(PermissionError):index.read_bytes()
   try:
    report,entry=service.store.get(key,b.OWNER,service.now_ms())
   except b.StoreError as exc:
    self.assertEqual(path.read_bytes(),before,'committed record bytes changed')
    self.fail('permission-denied index was not rebuilt: '+exc.reason)
   self.assertEqual(entry['capture_id'],key)
   self.assertEqual(service.store.status['last_index_rebuild']['reason'],'index_unreadable')
  finally:index.chmod(0o600)
  self.assertEqual(path.read_bytes(),before)

 async def test_credential_shaped_names_do_not_block_other_capture_records(self):
  data=b.fixture(3)
  data['states'][0]['attributes']['id']='BEGINPRIVATEKEY'
  data['registry'][0]['unique_id']='BEGINPRIVATEKEY'
  data['configs']['BEGINPRIVATEKEY']={'id':'BEGINPRIVATEKEY'}
  data['configs']['1']['alias']='postgresql'+':'+'//user:syntheticAlias317@host.invalid'
  data['states'][1]['attributes']['friendly_name']='Token refresh'
  service,_=self.service(data)
  response=await self.call(service)
  self.assertTrue(response['success'])
  self.assertEqual(len(response['data']['records']),2)
  self.assertEqual(response['metadata']['completeness'],'partial')
  disk=b''.join(p.read_bytes() for p in service.store.root.rglob('*') if p.is_file())
  self.assertNotIn(b'syntheticAlias317',disk)
  self.assertNotIn(b'BEGINPRIVATEKEY',disk)

 async def test_baseline_paths_registry_corpus(self):
  # Baseline projection does not return names/platform text; still sweep both
  # capture and compare outputs and every stored byte for each planted field.
  fields=('entity_id','unique_id','platform','disabled_by','name','original_name','aliases','device_id','area_id','original_device_class')
  from pathlib import Path
  import tempfile
  for field in fields:
   for label,(value,needle) in cases.withheld_cases().items():
    with self.subTest(field=field,case=label),tempfile.TemporaryDirectory(dir=self.governance) as temp:
     data=b.fixture(2)
     data['registry'][0][field]=[value] if field=='aliases' else value
     service,_=self.service(data)
     service.store=b.BaselineStore(Path(temp))
     result=await self.call(service)
     self.assertNotIn(needle,json.dumps(result))
     if result['success']:
      key=result['data']['snapshot']['capture_id']
      with patch.object(AUTOMATION_BASELINE_CAPTURE,'require',return_value=service):
       compared=await tool.compare_automation_baselines(earlier_capture_id=key,later_capture_id=key)
      self.assertNotIn(needle,compared)
     disk=b''.join(p.read_bytes() for p in service.store.root.rglob('*') if p.is_file())
     self.assertNotIn(needle.encode(),disk)

class EquivalentAndRecovery(b.Harness):
 async def test_h2_03_removed_guard_still_refuses_before_mutation(self):
  import inspect,textwrap
  from ha_mcp_engineering.audit_baseline import store as module
  service,_=self.service()
  with patch.object(c,'STORE_BASELINES',13):
   for _ in range(13):await service.capture(limit=100)
  (service.store.records/('f'*32+'.json')).write_bytes(b'invalidSynthetic')
  before={p.name:p.read_bytes() for p in service.store.records.iterdir()}
  service.store.index_path.unlink()
  source=textwrap.dedent(inspect.getsource(module.BaselineStore._rebuild))
  fragment='    if len(recovered) > c.STORE_BASELINES:\n        # More verified records than the store admits: fail closed before\n        # any file is touched.\n        raise StoreError("store_invalid")\n'
  self.assertIn(fragment,source)
  namespace=dict(vars(module));exec(source.replace(fragment,''),namespace)
  with patch.object(module.BaselineStore,'_rebuild',namespace['_rebuild']):
   with self.assertRaises(b.StoreError) as caught:
    with service.store.transaction():service.store.load_index(service.now_ms())
  self.assertEqual(caught.exception.reason,'store_invalid')
  self.assertFalse(service.store.index_path.exists())
  self.assertEqual({p.name:p.read_bytes() for p in service.store.records.iterdir()},before)

 async def test_rebuild_partial_leftovers_preserves_all_valid_files(self):
  service,_=self.service()
  keys=[b.capture_id(await service.capture(limit=100)) for _ in range(3)]
  before={key:(service.store.records/(key+'.json')).read_bytes() for key in keys}
  (service.store.records/'.pending-review317').write_bytes(b'incompleteSynthetic')
  damaged='e'*32
  (service.store.records/(damaged+'.json')).write_bytes(b'incompleteSynthetic')
  service.store.index_path.write_bytes(b'broken-index')
  for key in keys:
   report,entry=service.store.get(key,b.OWNER,service.now_ms())
   self.assertEqual(entry['capture_id'],key)
   self.assertEqual((service.store.records/(key+'.json')).read_bytes(),before[key])
  self.assertEqual({p.name for p in service.store.records.iterdir()},{key+'.json' for key in keys})
  index=service.store.load_index(service.now_ms())
  self.assertEqual(index['quarantine']['entries'][0]['capture_id'],damaged)
  self.assertEqual(index['recovery']['last_index_rebuild']['recovered'],3)

if __name__=='__main__':unittest.main(verbosity=2)
