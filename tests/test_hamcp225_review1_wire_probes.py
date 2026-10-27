"""Authenticated MCP confirmation using production tools and synthetic Core replies."""
import sys,json,unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path.cwd()/'tests'),str(Path.cwd()/'hass_mcp_engineering_beta'),str(Path.cwd())]
import test_hamcp225_trace_snapshots as a
from ha_mcp_engineering.tools import registry
N='synthetic'+'WireProbe225'
class Wire(a.GatewayStackTests):
 def test_trace_tool_hardened_screen_on_authenticated_wire(self):
  self.gateway_core=a.FakeRuntime()
  original=a.tool_module.registered_tools()
  original['get_automation_trace']=registry._SDK_TOOLS.snapshot()['get_automation_trace']
  async def command(payload):
   self.assertEqual(payload,{'type':'trace/get','domain':'automation','item_id':'a1','run_id':'r1'})
   return {**a.header('r1'),'trace':{'action/0':[{'changed_variables':{'message':'pass\tword='+N}}]}}
  with patch.object(a.tool_module,'registered_tools',lambda:original),patch.object(a.compatibility,'ws_command',command):
   rendered,_,wire=self.call('get_automation_trace',{'automation_id':'a1','run_id':'r1'})
  self.assertTrue(rendered['success'])
  self.assertFalse(N in wire,'synthetic split-password credential reached authenticated trace response')
 def test_pem_dictionary_key_not_on_wire(self):
  self.gateway_core=a.FakeRuntime()
  original=a.tool_module.registered_tools();original['get_automation_trace']=registry._SDK_TOOLS.snapshot()['get_automation_trace']
  async def command(payload):return {**a.header('r1'),'config':{a.h.private_key_pem(N):'benign'}}
  with patch.object(a.tool_module,'registered_tools',lambda:original),patch.object(a.compatibility,'ws_command',command):
   rendered,_,wire=self.call('get_automation_trace',{'automation_id':'a1','run_id':'r1'})
  self.assertTrue(rendered['success'])
  self.assertFalse(N in wire,'synthetic private-key text reached authenticated response')
for name in a.GatewayStackTests.__dict__:
 if name.startswith('test_'):setattr(Wire,name,None)
if __name__=='__main__':unittest.main(verbosity=2)
