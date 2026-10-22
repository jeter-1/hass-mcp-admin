"""Independent PR294 correction-delta tests. Synthetic data; offline only."""
import copy,json,unittest
from urllib.parse import quote
import test_hamcp132_configuration_definitions as t
from ha_mcp_engineering.configuration_definitions import credentials as c
from ha_mcp_engineering.configuration_definitions.contracts import MAX_TITLE

def forms():
    base={s:t.db_url(s) for s in ('postgresql','mysql','mssql','mongodb','redis','mqtt','http','https')}
    base.update(query='https://synthetic.invalid/?password='+t.DB_PASSWORD,
                encoded=quote(t.db_url(),safe=''),
                double_encoded=quote(quote(t.db_url(),safe=''),safe=''),
                bare=t.DB_USER+':'+t.DB_PASSWORD+'@synthetic.invalid',
                nfkc=''.join(chr(ord(x)+65248) if 33<=ord(x)<=126 else x for x in t.db_url()))
    return base

class DeltaChecks(unittest.IsolatedAsyncioTestCase):
    async def report(self,values=None,core=None,ids=(),integrations=('template',)):
        env=t.setup(t.owner_yaml() if values is None else values,t.owner_core() if core is None else core)
        try: return await env.service.report(ids,integrations)
        finally: t.end_request(env.token)

    async def test_additional_shapes_titles_and_metadata(self):
        for label,text in forms().items():
            with self.subTest(shape=label):
                values,core=t.plant_everywhere(text)
                response,data,audit=await t.serve_through_gateway(
                    {'entity_ids':['sensor.ui_template','sensor.planted'],'integrations':['template','sql']},
                    values,core)
                self.assertTrue(data['success'])
                self.assertNotIn(t.DB_PASSWORD,response)
                self.assertNotIn(t.DB_PASSWORD,audit)
                self.assertEqual(data['data']['ui_entries'][0]['title'],c.WITHHELD_MARKER)
                self.assertIn(c.OMISSION,data['data']['omissions'])
                self.assertFalse(data['data']['complete'])

    async def test_every_envelope_field_keys_nested_values_and_omissions(self):
        env=t.setup()
        try:
            base=await env.service.report((),('template',))
            for field,value in base.items():
                if isinstance(value,(dict,list,str)) and field != 'completeness':
                    with self.subTest(field=field):
                        report=copy.deepcopy(base)
                        if isinstance(value,dict):
                            report[field]['synthetic_nested']={t.db_url(): 'drop key','keep':[t.db_url()]}
                        elif isinstance(value,list):
                            report[field].append({'source':'synthetic','status':'complete','reason':t.db_url(),
                                                  'nested':{t.db_url():t.db_url()}})
                        else:
                            report[field]=t.db_url()
                        omissions=list(report.pop('omissions'))
                        result=env.service._finish(report,omissions,lambda item:omissions.append(item))
                        self.assertNotIn(t.DB_PASSWORD,json.dumps(result))
                        self.assertIn(c.OMISSION,result['omissions'])
                        self.assertFalse(result['complete'])
        finally:t.end_request(env.token)

    async def test_omission_reason_is_screened(self):
        env=t.setup()
        try:
            report=await env.service.report((),('template',))
            omissions=[t.db_url()]
            result=env.service._finish(report,omissions,lambda item:omissions.append(item))
            self.assertNotIn(t.DB_PASSWORD,json.dumps(result))
            self.assertFalse(result['complete'])
            self.assertIn(c.OMISSION,result['omissions'])
        finally:t.end_request(env.token)

    async def test_known_secret_case_variants(self):
        for value in (t.ACCESS_SECRET.lower(),t.ACCESS_SECRET.upper(),t.ACCESS_SECRET.swapcase()):
            with self.subTest(case='variant'):
                core=t.owner_core();core['config_entries/get']['template'][0]['title']=value
                response,data,audit=await t.serve_through_gateway({'integrations':['template']},t.yaml(),core)
                self.assertNotIn(value,response)
                self.assertNotIn(value,audit)
                self.assertEqual(data['data']['ui_entries'][0]['title'],c.WITHHELD_MARKER)
                self.assertFalse(data['data']['complete'])

    async def test_title_truncation_cannot_remove_credential_context(self):
        for prefix in ('','https://'):
            with self.subTest(kind='bare' if not prefix else 'https'):
                credential=prefix+t.DB_USER+':'+t.DB_PASSWORD
                title='x'*(MAX_TITLE-len(credential)-1)+' '+credential+'@synthetic.invalid'
                self.assertTrue(c.credential_text(title),'control: full title is detected')
                core=t.owner_core();core['config_entries/get']['template'][0]['title']=title
                response,data,audit=await t.serve_through_gateway({'integrations':['template']},t.yaml(),core)
                self.assertTrue(data['success'])
                self.assertNotIn(t.DB_PASSWORD,response,'synthetic password survives pre-screen truncation')
                self.assertFalse(data['data']['complete'])

    async def test_non_nfkc_unicode_confusable_credentials(self):
        values={
            'ratio_division':t.db_url().replace(':','\u2236').replace('/','\u2215'),
            'cyrillic_o_password':'https://synthetic.invalid/?passw\u043erd='+t.DB_PASSWORD,
        }
        for label,title in values.items():
            with self.subTest(form=label):
                core=t.owner_core();core['config_entries/get']['template'][0]['title']=title
                response,data,audit=await t.serve_through_gateway({'integrations':['template']},t.yaml(),core)
                self.assertTrue(data['success'])
                self.assertNotIn(t.DB_PASSWORD,response,'synthetic password survives confusable credential text')
                self.assertFalse(data['data']['complete'])

    async def test_duplicate_registry_identity_has_no_first_row_guess(self):
        core=t.owner_core()
        core['config/entity_registry/list'].append(t.registry_row('sensor.second','template','feels_like'))
        r=await self.report(core=core)
        definition=next(d for d in r['definitions'] if d['definition_id']=='template[0].sensor[0]')
        match=definition['expected_entities'][0]['match']
        self.assertEqual(match['method'],'ambiguous')
        self.assertIsNone(match['entity_id'])
        self.assertIsNone(match['confidence'])

    async def test_parser_dependent_id_and_name_matrix(self):
        for raw,alternative in [('on','True'),('off','False'),('yes','True'),('no','False'),('1:30','90')]:
            for registered in (raw,alternative):
                with self.subTest(raw=raw,registered=registered):
                    core=t.owner_core()
                    core['config/entity_registry/list'].append(t.registry_row('sensor.probe','template',registered))
                    values=t.yaml(template=[{'sensor':[{'name':raw,'unique_id':raw,'state':'1'}]}])
                    r=await self.report(values,core,('sensor.probe',))
                    m=r['entities'][0]['definition']
                    self.assertEqual((m['method'],m['confidence']),('heuristic','medium'))
                    d=r['definitions'][0]['expected_entities'][0]
                    self.assertIsNone(d['derived_entity_id'])

    async def test_unprojected_blueprint_is_a_definition_gap(self):
        values=t.yaml(template=[{'use_blueprint':{'path':'synthetic/example.yaml','input':{}}}])
        r=await self.report(values,ids=('sensor.ghost',))
        self.assertEqual(r['definitions'][0]['status'],'blueprint_not_projected')
        self.assertEqual(r['entities'][0]['diagnosis']['classification'],'undetermined')
        self.assertNotIn('definition_absent',r['entities'][0]['diagnosis']['reasons'])

    async def test_malformed_nested_state_and_disabled_values_preserve_siblings(self):
        for value in ([],{},None,1,True):
            with self.subTest(kind='state',value_type=type(value).__name__):
                core=t.owner_core();core['states'].append({'entity_id':'sensor.probe','state':value})
                r=await self.report(core=core,ids=('sensor.probe','sensor.database_size'))
                self.assertEqual(r['entities'][0]['state'],'invalid_response')
                self.assertEqual(r['entities'][1]['diagnosis']['classification'],'available')
                self.assertEqual(t.source(r,'states')['status'],'partial')
        for value in ([],{},1,True):
            with self.subTest(kind='disabled_by',value_type=type(value).__name__):
                core=t.owner_core();core['config/entity_registry/list'][0]['disabled_by']=value
                r=await self.report(core=core,ids=('sensor.database_free_space','sensor.database_size'))
                self.assertEqual(r['entities'][0]['registry']['disabled_by'],'invalid_response')
                self.assertEqual(r['entities'][1]['diagnosis']['classification'],'available')
                self.assertEqual(t.source(r,'config/entity_registry/list')['status'],'partial')

    async def test_malformed_registry_identity_is_not_silently_complete(self):
        for field in ('platform','unique_id','config_entry_id'):
            with self.subTest(field=field):
                core=t.owner_core();core['config/entity_registry/list'][0][field]={'invalid':True}
                r=await self.report(core=core,ids=('sensor.database_free_space','sensor.database_size'))
                self.assertEqual(t.source(r,'config/entity_registry/list')['reason'],'invalid_response')
                self.assertEqual(t.source(r,'config/entity_registry/list')['status'],'partial')
                self.assertEqual(r['entities'][1]['diagnosis']['classification'],'available')

if __name__=='__main__':unittest.main()

