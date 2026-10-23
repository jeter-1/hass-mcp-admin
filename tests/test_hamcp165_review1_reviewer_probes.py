"""Independent offline PR #303 probes. Real service/F3; synthetic provider only."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.test_hamcp165_label_registry import ServiceTestCase, CREATE_HOLIDAY, arguments, planning
from ha_mcp_engineering.errors import GovernanceError
from ha_mcp_engineering.ha_core_readmission.routes import static_tool_requirements, static_tool_target
from ha_mcp_engineering.request_context import begin_request, end_request


class ReviewerProbes(ServiceTestCase):
    def production_transport(self):
        # Real client and auth checks; only the aiohttp peer is synthetic.
        from ha_mcp_engineering.clients.websocket import HomeAssistantWebSocketClient
        registry = self.ws
        class Socket:
            def __init__(self): self.stage=0; self.payload=None
            async def __aenter__(self): return self
            async def __aexit__(self,*_): return False
            async def send_json(self,value):
                if value.get('type')!='auth': self.payload={k:v for k,v in value.items() if k!='id'}
            async def receive_json(self):
                self.stage+=1
                if self.stage==1: return {'type':'auth_required'}
                if self.stage==2: return {'type':'auth_ok'}
                return {'id':1,'type':'result','success':True,'result':await registry.command(self.payload)}
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self,*_): return False
            def ws_connect(self,*_,**__): return Socket()
        self.resource_gateway.label_registry.websocket_client=HomeAssistantWebSocketClient(SimpleNamespace(
            ha_timeout_seconds=5,websocket_url='ws://127.0.0.1:1/api/websocket',ha_token='synthetic-reviewer-only'))
        return patch('ha_mcp_engineering.clients.websocket.aiohttp.ClientSession',side_effect=lambda **_:Session())

    async def test_admitted_production_client_control(self):
        with self.production_transport():
            created=await self.approved(CREATE_HOLIDAY)
            result=await self.apply(created)
        self.assertEqual(result['task_state'],'succeeded_verified')
        self.assertEqual(len(self.ws.writes()),1)

    async def test_generic_internal_builder_requires_admission(self):
        request = planning.validate_request(arguments(CREATE_HOLIDAY))
        prepared = await planning.prepare(self.service.label_registry_gateway,
            self.service.entity_metadata_gateway, self.service.entity_metadata_snapshot_reader, request)
        self.core.withdraw()
        self.ws.commands.clear()
        try:
            result = await self.service._create_configuration_plan(title='Reviewer label', description='Synthetic',
                operations=prepared.operations(), _label_context=prepared)
        except GovernanceError as error:
            self.assertEqual(error.details.get('reason_code'), 'pending_signed_registry_admission')
        else:
            print('INTERNAL_BUILDER', json.dumps({'persisted':len(self.repository.list()),
                'ha_commands':len(self.ws.commands), 'status':result.get('status')}))
        self.assertEqual(len(self.repository.list()), 0, 'Unadmitted internal builder persisted a label plan')

    async def test_apply_without_core_cannot_write(self):
        created = await self.approved(CREATE_HOLIDAY)
        self.core.withdraw()
        self.core = None
        await self.restart_runtime()
        self.ws.commands.clear()
        with self.production_transport():
            try:
                result = await self.apply(created)
            except GovernanceError as error:
                result={'error':error.code.value}
        print('MISSING_CORE', json.dumps({'result':result.get('task_state',result.get('error')),
            'writes':[w['type'] for w in self.ws.writes()]}))
        self.assertEqual(self.ws.writes(), [], 'Label write occurred without a Core runtime')

    async def test_withdrawn_core_apply_and_restart_read_nothing(self):
        created = await self.approved(CREATE_HOLIDAY)
        self.core.withdraw()
        self.ws.commands.clear()
        result = await self.apply(created)
        await self.restart_runtime()
        await self.runtime.recover_once('reviewer_restart')
        print('WITHDRAWN_CORE',json.dumps({'task_state':result['task_state'],'ha_commands':self.ws.commands}))
        self.assertEqual(self.ws.commands, [])

    async def test_target_drift_observed_by_final_namespace_read_is_refused(self):
        self.ws.add_label('outdoor','Outdoor',color='green')
        created=await self.approved({'operation':'update','label_id':'outdoor','color':'red'})
        original=self.service.label_registry_gateway.namespace
        async def changed_namespace():
            self.ws.set_external_label('outdoor',color='blue')
            return await original()
        self.service.label_registry_gateway.namespace=changed_namespace
        self.ws.commands.clear()
        result=await self.apply(created)
        print('FINAL_READ_DRIFT',json.dumps({'task_state':result['task_state'],
              'color':self.ws.label('outdoor')['color'],'writes':[w['type'] for w in self.ws.writes()]}))
        self.assertEqual(self.ws.writes(), [], 'Final registry read saw target drift but the write proceeded')
        self.assertEqual(self.ws.label('outdoor')['color'],'blue')

    async def test_seq13_invalid_values_produce_refusal_receipt(self):
        # Compile only the exact lane closures; no lane execution or registry signing.
        source=Path('scripts/seq13_contract_acceptance.py')
        nodes={n.name:n for n in ast.walk(ast.parse(source.read_text()))
               if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
        namespace={'begin_request':begin_request,'end_request':end_request,
                   'static_tool_requirements':static_tool_requirements,'static_tool_target':static_tool_target,
                   'caller':'synthetic-reviewer','tool':'create_label_plan','service':self.service,
                   'lane':{'key':'label_registry'},
                   'GovernanceError':GovernanceError,
                   'engineering_ws':SimpleNamespace(writes=[],command_types=[])}
        module=ast.Module(body=[nodes['plan'],nodes['refused_plan']],type_ignores=[])
        exec(compile(module,str(source),'exec'),namespace)
        for field,value,reason in [('color','not a colour','invalid_label_color'),('icon','tag','invalid_label_icon')]:
            with self.subTest(field=field):
                receipt=await namespace['refused_plan'](self.core,arguments({'operation':'update','label_id':'outdoor',field:value}))
                self.assertEqual(receipt['reason_code'],reason)
                self.assertEqual(receipt['ha_commands'],0)

    def test_availability_receipt_requires_config_entry_reads(self):
        from tests.test_seq13_contract_acceptance import seq13, synthetic_receipt, CORE, CORE_SLOT
        receipt=synthetic_receipt('availability_exposure',CORE)
        for command in ('config/entity_registry/list','config_entries/get_single'):
            self.assertNotIn(command,receipt['engineering_command_types'])
        with self.assertRaises((AssertionError,ValueError),msg='Receipt accepted without exercising config-entry enable reads'):
            seq13.verify_result('availability_exposure',receipt,core_version=CORE,support_slot=CORE_SLOT)


if __name__=='__main__':
    unittest.main(defaultTest='ReviewerProbes',verbosity=2)
