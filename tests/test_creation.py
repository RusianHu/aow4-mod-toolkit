import base64
import ctypes as c
from datetime import datetime,timezone
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import authoring,creation,publication
from aow4mod.core import FormatError,Node
from aow4mod.steam_transport import Steam,StringArray
from test_aow4_mod_audit import acp,rpk,resource,typed,mapping


class FakeSteam:
    def __init__(self):
        self.owner=123;self.item=456;self.creates=0;self.uploads=0;self.unknown=False;self.legal=False;self.upload_unknown=False
        self.details={'publishedfileid':'456','consumer_app_id':1669000,'creator':'123','title':'',
                      'time_created':int(time.time()),'time_updated':0,'visibility':2,'description':'',
                      'tags':[],'tags_truncated':False,'preview_present':False}
    def steam_id(self):return self.owner
    def close(self):pass
    def item_details(self,item,timeout):return dict(self.details)
    def create_item(self,context,path,timeout):
        self.creates+=1
        record={'format':'aow4mod-creation-receipt/1','app_id':1669000,'created_utc':datetime.now(timezone.utc).isoformat(),
                **context,'status':'creation_outcome_unknown' if self.unknown else 'created','needs_workshop_agreement':self.legal}
        if not self.unknown:record['published_file_id']=self.item
        with Path(path).open('x',encoding='utf8') as f:json.dump(record,f)
        if self.unknown:raise TimeoutError('simulated lost creation callback')
        return record
    def publish(self,manifest,content,path,timeout):
        self.uploads+=1;publication.check_staged(manifest)
        initial=manifest['_new_item_metadata']
        assert '.upload-attempts' in manifest['description_file']
        assert '.upload-attempts' in initial['preview']
        result={'status':'timeout_outcome_unknown' if self.upload_unknown else 'success','app_id':1669000,
                'published_file_id':manifest['published_file_id'],'content_files':manifest['files']}
        with Path(path).open('x',encoding='utf8') as f:json.dump(result,f)
        self.details.update(title=initial['title'],tags=initial['tags'],visibility=initial['visibility'],preview_present=True,
                            description=Path(manifest['description_file']).read_text(encoding='utf8').rstrip())
        if self.upload_unknown:raise TimeoutError('simulated lost upload callback')
        return result
    def verify_download(self,manifest,timeout):return {'content_matches':True,'files':manifest['files']}


class CreationTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        source=self.root/'mod';source.mkdir();raw=acp(resource());fields=Node(raw,19,len(raw)).typed()[1][0x40009d].mapping()
        data={k:v.raw() for k,v in fields.items()};data[0x2a]=uuid.uuid4().bytes_le
        (source/'mod.acp').write_bytes(raw[:19]+typed(0x40009d,{0x40009d:mapping(data)}))
        self.game=self.root/'game';(self.game/'Content').mkdir(parents=True)
        (self.game/'Content/base.rpk').write_bytes(rpk([resource()]))
        project=self.root/'project';authoring.import_project(source,project);authoring.build_project(project,self.root/'release',self.game)
        self.manifest=self.root/'release/manifest.json';self.preview=self.root/'preview.png'
        self.preview.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nKcAAAAASUVORK5CYII='))
        self.plan=creation.make_plan(self.manifest,123,self.preview,'New Example',['Mod'])
        self.path=self.root/'create-plan.json';self.path.write_text(json.dumps(self.plan),encoding='utf8')
        self.client=FakeSteam();self.factory=lambda game:self.client

    def execute(self,**kwargs):return creation.execute(self.path,factory=self.factory,**kwargs)

    def test_default_is_preflight_only_with_private_visibility(self):
        r=self.execute();self.assertFalse(r['submitted']);self.assertEqual(r['visibility'],'private')
        self.assertEqual(self.client.creates,0);self.assertFalse(creation.paths(self.path)[0].exists())

    def test_create_upload_verify_and_duplicate_prevention(self):
        r=self.execute(submit=True);self.assertTrue(r['verification_required'])
        self.assertEqual((self.client.creates,self.client.uploads),(1,1))
        v=creation.verify(self.path,True,factory=self.factory);self.assertTrue(v['desired_state_verified'])
        self.assertFalse(v['preview_pixel_identity_verified'])
        with self.assertRaises(FormatError):self.execute(submit=True)
        with self.assertRaises(FormatError):self.execute(submit=True,resume=True)
        self.assertEqual(self.client.creates,1)

    def test_legal_agreement_stops_upload_until_explicit_acknowledgment(self):
        self.client.legal=True;r=self.execute(submit=True)
        self.assertTrue(r['needs_workshop_agreement']);self.assertEqual(self.client.uploads,0)
        self.execute(submit=True,resume=True,agreement_accepted=True)
        self.assertEqual((self.client.creates,self.client.uploads),(1,1))

    def test_unknown_creation_never_repeats_and_explicit_recovery_reuses_item(self):
        self.client.unknown=True
        with self.assertRaises(TimeoutError):self.execute(submit=True)
        with self.assertRaises(FormatError):self.execute(submit=True)
        with self.assertRaises(FormatError):self.execute(submit=True,resume=True)
        recovered=creation.recover(self.path,456,factory=self.factory)
        self.assertFalse(recovered['created_again']);self.assertFalse(recovered['uploaded'])
        self.execute(submit=True,resume=True)
        self.assertEqual((self.client.creates,self.client.uploads),(1,1))

    def test_recovery_rejects_other_owner_and_old_items(self):
        self.client.unknown=True
        with self.assertRaises(TimeoutError):self.execute(submit=True)
        self.client.details['creator']='999'
        with self.assertRaises(FormatError):creation.recover(self.path,456,factory=self.factory)
        self.client.details['creator']='123';self.client.details['time_created']=1
        with self.assertRaisesRegex(FormatError,'predates'):creation.recover(self.path,456,factory=self.factory)

    def test_unknown_upload_is_verified_without_resubmission(self):
        self.client.upload_unknown=True
        with self.assertRaises(TimeoutError):self.execute(submit=True)
        with self.assertRaises(FormatError):self.execute(submit=True,resume=True)
        result=creation.verify(self.path,True,factory=self.factory)
        self.assertTrue(result['desired_state_verified']);self.assertFalse(result['resubmitted'])

    def test_historical_verification_uses_frozen_payload_and_metadata(self):
        self.execute(submit=True)
        self.preview.unlink();self.manifest.write_text('{}', encoding='utf-8')
        result=creation.verify(self.path,True,factory=self.factory)
        self.assertTrue(result['desired_state_verified'])

    def test_wrong_current_author_and_changed_plan_inputs_never_create(self):
        self.client.owner=999
        with self.assertRaises(FormatError):self.execute(submit=True)
        self.assertEqual(self.client.creates,0)
        self.client.owner=123;self.preview.write_bytes(b'changed')
        with self.assertRaises(FormatError):self.execute(submit=True)
        self.assertEqual(self.client.creates,0)

    def test_creation_metadata_bounds_and_existing_item_guard(self):
        for kwargs in ({'title':'x'*129},{'tags':['bad,tag']},{'tags':[{}]},{'visibility':'invalid'}):
            with self.assertRaises(FormatError):creation.make_plan(self.manifest,123,self.preview,**kwargs)
        manifest=json.loads(self.manifest.read_text(encoding='utf-8'));manifest['workshop']={'published_file_id':456}
        self.manifest.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaises(FormatError):creation.make_plan(self.manifest,123,self.preview)

    def test_native_create_callback_decodes_id_and_legal_flag_without_live_mutation(self):
        client=Steam.__new__(Steam);client.ugc=99;client._dispatch=lambda:1
        client.fn=lambda name,args,result:(lambda *args:77)
        client._wait_call=lambda *args:(struct.pack('<i4xQ?7x',1,456,True),False)
        receipt=self.root/'native-create.json';result=client.create_item({'expected_owner':123},receipt,1)
        self.assertEqual(result['published_file_id'],456);self.assertTrue(result['needs_workshop_agreement'])

    def test_failed_native_create_does_not_treat_undefined_result_id_as_confirmed(self):
        client=Steam.__new__(Steam);client.ugc=99;client._dispatch=lambda:1
        client.fn=lambda name,args,result:(lambda *args:77)
        client._wait_call=lambda *args:(struct.pack('<i4xQ?7x',2,456,False),False)
        receipt=self.root/'native-failed.json'
        with self.assertRaises(RuntimeError):client.create_item({'expected_owner':123},receipt,1)
        result=json.loads(receipt.read_text(encoding='utf-8'))
        self.assertNotIn('published_file_id',result);self.assertEqual(result['returned_item_id'],456)

    def test_native_initial_setters_receive_reviewed_metadata(self):
        manifest,content=publication.load_release(self.manifest)
        manifest['_new_item_metadata']={k:self.plan[k] for k in ('title','tags','visibility','preview','preview_sha256')}
        manifest['published_file_id']=456;manifest,_=publication.stage_release(manifest,content,self.root)
        client=Steam.__new__(Steam);client.ugc=99;client._dispatch=lambda:1;calls={}
        def bind(name,args,result):
            def invoke(*values):
                calls[name]=values
                if name.endswith('StartItemUpdate'):return 99
                if name.endswith('SetItemTags'):
                    array=c.cast(values[2],c.POINTER(StringArray)).contents
                    calls['decoded_tags']=[array.strings[i].decode() for i in range(array.count)]
                if name.endswith('SubmitItemUpdate'):return 0
                return True
            return invoke
        client.fn=bind
        with self.assertRaisesRegex(RuntimeError,'invalid call'):client.publish(manifest,Path(manifest['content_directory']),self.root/'native-upload.json',1)
        self.assertEqual(calls['decoded_tags'],['Mod'])
        self.assertEqual(calls['SteamAPI_ISteamUGC_SetItemVisibility'][2],2)
        self.assertEqual(calls['SteamAPI_ISteamUGC_SetItemTitle'][2],b'New Example')


if __name__=='__main__':unittest.main()
