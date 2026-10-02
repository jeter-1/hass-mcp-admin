"""Exact Core isolated-function evidence (not an assembled disposable Core lane).

Set AUTOMATION_BASELINE_CORE_ARCHIVE to the verified retained source archive.
No download, Core import, household fixture or service access is performed.
"""
import asyncio
import hashlib
import os
from pathlib import Path
import tarfile
import textwrap
from types import SimpleNamespace
import unittest

ARCHIVE = os.environ.get('AUTOMATION_BASELINE_CORE_ARCHIVE')
COMMIT = '9212531f40a0b7b23229a90d688dd79d9dfccff4'
SHA256 = 'c8d814b63c0b7043ad54980044f9824c9582450af9cac38ba376fadc37fa4056'

@unittest.skipUnless(ARCHIVE, 'exact Core source archive not supplied; not a disposable Core result')
class ExactCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=Path(ARCHIVE)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=SHA256:
            raise AssertionError('Core source archive identity mismatch')
        wanted={'homeassistant/components/api/__init__.py', 'homeassistant/components/auth/__init__.py',
                'homeassistant/auth/__init__.py', 'homeassistant/auth/mfa_modules/totp.py',
                'homeassistant/auth/mfa_modules/notify.py', 'homeassistant/auth/mfa_modules/insecure_example.py',
                'homeassistant/components/config/view.py','homeassistant/components/config/entity_registry.py',
                'homeassistant/components/hassio/coordinator.py','homeassistant/helpers/device_registry.py'}
        cls.source={}
        with tarfile.open(path) as archive:
            for member in archive:
                parts=member.name.split('/',1)
                if len(parts)==2 and parts[1] in wanted and member.isfile():
                    cls.source[parts[1]]=archive.extractfile(member).read().decode()
        if cls.source.keys()!=wanted: raise AssertionError('Core source files missing')
    def section(self,path,start,end):
        source=self.source[path];position=source.index(start)
        return source[position:source.index(end,position)]
    def compile(self,text,namespace):
        exec(compile('from __future__ import annotations\n'+textwrap.dedent(text),'exact_core_'+COMMIT,'exec'),namespace)
        return namespace

    def test_rest_admin_inventory_is_all_or_error_not_silent_omission(self):
        body=self.section('homeassistant/components/api/__init__.py','class APIStatesView','\nclass APIEntityStateView')
        class Response:
            def __init__(self,**kwargs): self.body=kwargs['body']
            def enable_compression(self):pass
        namespace={'HomeAssistantView':object,'URL_API_STATES':'/api/states','ha':SimpleNamespace(callback=lambda f:f),
                   'web':SimpleNamespace(Response=Response),'KEY_HASS_USER':'user','KEY_HASS':'hass','POLICY_READ':'read',
                   'CONTENT_TYPE_JSON':'application/json','MIN_COMPRESSED_RESPONSE_SIZE':99999}
        view=self.compile(body,namespace)['APIStatesView']()
        rows=[SimpleNamespace(entity_id='automation.off',as_dict_json=b'{"entity_id":"automation.off","state":"off"}'),
              SimpleNamespace(entity_id='automation.on',as_dict_json=b'{"entity_id":"automation.on","state":"on"}')]
        class Request(dict):pass
        request=Request(user=SimpleNamespace(is_admin=True)); request.app={'hass':SimpleNamespace(states=SimpleNamespace(async_all=lambda:rows))}
        self.assertIn(b'automation.off',view.get(request).body)
        class Broken:
            @property
            def as_dict_json(self):raise ValueError('synthetic bad state')
        rows.append(Broken())
        with self.assertRaises(ValueError):view.get(request)
        rows.pop()
        request['user']=SimpleNamespace(is_admin=False,permissions=SimpleNamespace(check_entity=lambda *_:False))
        self.assertEqual(view.get(request).body,b'[]')

    def test_current_user_returns_exact_connection_admin_boolean(self):
        body=self.section('homeassistant/components/auth/__init__.py','async def websocket_current_user(', '\n\n@websocket_api.websocket_command(')
        received=[]
        async def mfa(_):return {}
        namespace={'websocket_api':SimpleNamespace(result_message=lambda i,r:r)}
        function=self.compile(body,namespace)['websocket_current_user']
        user=SimpleNamespace(id='synthetic-user',name='not-retained',is_owner=False,is_admin=True,credentials=[])
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_enabled_mfa=mfa,auth_mfa_modules=[]))
        asyncio.run(function(hass,SimpleNamespace(user=user,send_message=received.append),{'id':1}))
        self.assertIs(received[0]['is_admin'],True)
        user.is_admin=False
        asyncio.run(function(hass,SimpleNamespace(user=user,send_message=received.append),{'id':2}))
        self.assertIs(received[1]['is_admin'],False)

    def test_builtin_mfa_setup_reads_do_not_save_or_invoke_flows(self):
        for module,attribute,storagekey in (('totp','_users','users'),('notify','_user_settings','users')):
            with self.subTest(module=module):
                path='homeassistant/auth/mfa_modules/'+module+'.py'
                body=self.section(path,'    async def async_is_user_setup(', '\n    @override')
                load=self.section(path,'    async def _async_load(', '\n    async def _async_save(')
                namespace={'Any':object,'cast':lambda _type,value:value,'STORAGE_USERS':storagekey,'NotifySetting':lambda **kw:kw}
                functions=self.compile(load+'\n'+body,namespace)
                loaded=[]
                async def read():loaded.append(True);return None
                async def forbidden(*_):raise AssertionError('write or flow reached')
                state=SimpleNamespace(**{attribute:None},_init_lock=asyncio.Lock(),_user_store=SimpleNamespace(async_load=read,async_save=forbidden),_async_save=forbidden)
                async def do_load():await functions['_async_load'](state)
                state._async_load=do_load
                self.assertFalse(asyncio.run(functions['async_is_user_setup'](state,'synthetic-user')))
                self.assertEqual(loaded,[True])
                self.assertEqual(getattr(state,attribute),{})

    def test_exact_rest_lookup_registry_omission_and_anchor_persistence(self):
        source=self.source['homeassistant/components/config/view.py']
        self.assertIn('return next((val for val in data if val.get(CONF_ID) == config_key), None)',source)
        self.assertIn('@require_admin\n    async def get',source)
        registry=self.source['homeassistant/components/config/entity_registry.py']
        self.assertIn('partial_json_repr',registry)
        core=self.source['homeassistant/components/hassio/coordinator.py']
        self.assertIn('identifiers={(DOMAIN, "core")}',core)
        device=self.source['homeassistant/helpers/device_registry.py']
        self.assertIn('id=device["id"]',device)
        self.assertIn('"config_entry_id": self.config_entry_id',device)
