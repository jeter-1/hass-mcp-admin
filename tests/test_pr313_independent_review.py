"""Independent PR313 regression tests. Synthetic fixtures only; run from repo root.
Set TMPDIR to a disposable directory under /var/tmp. No live HA or host journal.
"""
import asyncio
import ctypes
import dataclasses
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path.cwd()/'tests'),str(Path.cwd()/'hass_mcp_engineering_beta')]
import test_hamcp224_host_journal as author
import test_hamcp131_incident_logs as gateway_tests
from ha_mcp_engineering.errors import GovernanceError

hj=author.hj

class NativeFieldOverlay:
    """Replace one native get_data result; retain real fixture traversal and cap."""
    def __init__(self, field, value):
        self.real=hj.load_library()
        self.field=field.encode()
        self.buffer=ctypes.create_string_buffer(self.field+b'='+value)
    def __getattr__(self,name):return getattr(self.real,name)
    def get_data(self,handle,field,pointer,size):
        if field==self.field:
            ctypes.cast(pointer,ctypes.POINTER(ctypes.c_void_p))[0]=ctypes.addressof(self.buffer)
            ctypes.cast(size,ctypes.POINTER(ctypes.c_size_t))[0]=len(self.buffer.raw)-1
            return 0
        return self.real.get_data(handle,field,pointer,size)

class ReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.helper=author.JournalServiceTests()
        await self.helper.asyncSetUp()
        self.addCleanup(self.helper.doCleanups)
    async def read(self,value,field='MESSAGE',source='supervisor',**kwargs):
        lib=NativeFieldOverlay(field,value)
        service=self.helper.make_service(author.reader(self.helper.journal,library=lambda:lib))
        start=7 if source=='host:kernel' else 0
        args={'sources':[source],'start':author.iso(start),'end':author.iso(start+1),**kwargs}
        return await self.helper.read(service=service,**args)
    async def test_truncated_uri_is_withheld(self):
        for prefix in (b'PLANTEDUSER:PLANTEDPASS',b'https://PLANTEDUSER:PLANTEDPASS'):
            with self.subTest(prefix=prefix):
                page=await self.read(prefix+b'x'*16400+b'@host')
                self.assertNotIn('PLANTED',json.dumps(page))
                self.assertIn('withheld',page['entries'][0]);self.assertFalse(page['complete'])
    async def test_obfuscated_credentials_are_withheld(self):
        for value in (b'api%5fkey=PLANTEDTOKEN0123456789',b'api_\tkey=PLANTEDTOKEN0123456789',
                      'api_k\u0435y=PLANTEDTOKEN0123456789'.encode(),
                      b'Authoriz%61tion: Be%61rer PLANTEDTOKEN0123456789',
                      b'mosquitto_pub --p\tw PLANTEDPASS0123456789'):
            with self.subTest(value=value):
                page=await self.read(value)
                self.assertNotIn('PLANTED',json.dumps(page));self.assertFalse(page['complete'])
    async def test_withheld_metadata_is_partial(self):
        for field in ('SYSLOG_IDENTIFIER','_KERNEL_SUBSYSTEM','_UDEV_SYSNAME'):
            with self.subTest(field=field):
                page=await self.read(b'password=PLANTEDMETAPASS',field,'host:kernel')
                self.assertNotIn('PLANTED',json.dumps(page));self.assertFalse(page['complete'])
                self.assertEqual(page['sources'][0]['status'],'partial')
    async def test_filtered_withheld_message_is_partial(self):
        page=await self.read(b'password=PLANTEDFILTERED',text='ordinary')
        self.assertEqual(page['returned'],0);self.assertFalse(page['complete'])
    async def test_known_secret_case_split_and_encoded(self):
        for value in (b'SYNTHETIC-KNOWN-SECRET-224',b'synthetic-known-\tsecret-224',
                      b'synthetic%2Dknown%2Dsecret%2D224'):
            with self.subTest(value=value):
                page=await self.read(value)
                self.assertEqual(page['entries'][0]['message'],hj.WITHHELD_MARKER)
                self.assertFalse(page['complete'])
    async def test_ordinary_multiline_unicode_remains_complete(self):
        page=await self.read('Kitchen 20 °C, Küche 😀\nUSB reconnected'.encode())
        self.assertTrue(page['complete']);self.assertIn('Küche',page['entries'][0]['message'])
    async def test_microsecond_cross_source_order(self):
        real=author.reader(self.helper.journal)
        class Timing:
            def read(self,plans,digest,deadline):
                result=real.read(plans,digest,deadline)
                for source,delta in (('supervisor',1900),('host:kernel',1100)):
                    scan=result.scans[source]
                    scan.records=[dataclasses.replace(scan.records[0],rt_us=author.at(0)+delta)]
                    scan.finished='tail_reached';scan.clock_regressions=0
                return result
        page=await self.helper.read(service=self.helper.make_service(Timing()),sources=['supervisor','host:kernel'])
        self.assertEqual([e['source'] for e in page['entries']],['host:kernel','supervisor'])
    async def test_expired_deadline_performs_no_field_reads(self):
        class Counting:
            def __init__(self):self.real=hj.load_library();self.fields=0
            def __getattr__(self,name):return getattr(self.real,name)
            def get_data(self,*args):self.fields+=1;return self.real.get_data(*args)
        lib=Counting()
        result=author.reader(self.helper.journal,library=lambda:lib).read(
            [hj.Plan('host',author.at(0),author.at(7000),100)],author.digest,hj.time.monotonic()-1)
        self.assertEqual(result.scans['host'].stopped,'deadline_reached')
        self.assertEqual(lib.fields,0)
    async def test_cursor_forgery_query_and_process_replay_refused(self):
        page=await self.helper.read(sources=['supervisor'],limit=1)
        cursor=page['next_cursor']
        payload=self.helper.service.codec.decode(cursor)
        self.assertNotIn('s=',json.dumps(payload));self.assertNotIn('b007',json.dumps(payload))
        bad=cursor[:-1]+('A' if cursor[-1]!='A' else 'B')
        for arguments in ({'sources':['supervisor'],'cursor':bad},
                          {'sources':['host'],'cursor':cursor}):
            with self.assertRaises(GovernanceError):await self.helper.read(**arguments)
        other=author.CursorCodec('incident_logs',key=b'q'*32)
        with self.assertRaises(GovernanceError):other.decode(cursor)
        expired=author.CursorCodec('incident_logs',key=author.CODEC_KEY,clock=lambda:payload['x']+1)
        with self.assertRaises(GovernanceError):expired.decode(cursor)
    async def test_native_handle_closed_when_field_read_raises(self):
        class Exploding:
            def __init__(self):self.real=hj.load_library();self.closes=0
            def __getattr__(self,name):return getattr(self.real,name)
            def get_data(self,*args):raise RuntimeError('synthetic-field-error')
            def close(self,handle):self.closes+=1;return self.real.close(handle)
        lib=Exploding()
        page=await self.helper.read(service=self.helper.make_service(author.reader(self.helper.journal,library=lambda:lib)),sources=['host'])
        self.assertEqual(lib.closes,1);self.assertFalse(page['complete'])
        self.assertEqual(page['sources'][0]['reason'],'journal_read_failed')

class GatewayReviewTests(unittest.TestCase):
    def test_truncated_credentials_do_not_reach_wire(self):
        root=author.journal_root();self.addCleanup(author.shutil.rmtree,root)
        lib=NativeFieldOverlay('MESSAGE',b'https://PLANTEDUSER:PLANTEDPASS'+b'x'*16400+b'@host')
        with patch.object(gateway_tests,'journald_off_reader',lambda:author.reader(root,library=lambda:lib)):
            _,_,wire=gateway_tests.IncidentLogGatewayStackTests().call(
                {'sources':['supervisor'],'start':author.iso(0),'end':author.iso(1)})
        self.assertNotIn('PLANTED',wire)

if __name__=='__main__':unittest.main()
