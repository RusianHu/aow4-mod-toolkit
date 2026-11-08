import copy
from contextlib import contextmanager
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import native,native_edits as edits,native_objects,native_worker,editing
from aow4mod.core import FormatError,Node,read_acp,sha
from aow4mod.filesystem import inventory,file_hash
from test_aow4_mod_audit import mapping,string,typed,acp

CID=0x47142d;OWNER=0x471428;EMBEDDED='0x00471428/0x61';FIELD='0x0047142D/0x9'
DESCRIPTOR={'name':'Hit Points','category':'','codec':'i32','editor_cid':'00401418','editor_parameter':0}


def stats(value=5):return mapping({CID:mapping({9:struct.pack('<i',value),10:struct.pack('<i',0)})})


class FakeStats:
    edit_object=native_worker.Bindings.edit_object
    def __init__(self):self.counter=0;self.objects={};self.profile={'surface_class_id':0x410024}
    def types(self):return {'types':[{'cid':f'{CID:08X}','name':'TUnitStats'}]}
    def create(self,cid):self.counter+=1;self.objects[self.counter]={'value':5,'body':stats()};return self.counter
    def release(self,handle):del self.objects[handle]
    @contextmanager
    def borrowed(self,handle,cid):yield handle
    def deserialize_object(self,handle,data):
        self.objects[handle]={'value':int.from_bytes(native_objects.storage_fields(data).get(FIELD,bytes(4)),'little',signed=True),'body':data}
    def serialize_object(self,handle):
        state=self.objects[handle];root=Node(state['body'],0,len(state['body']));scope=root.mapping()[CID]
        return editing.rewrite_map(root,{CID:editing.rewrite_map(scope,{9:struct.pack('<i',state['value'])})})
    def property_records(self,handle):return [dict(DESCRIPTOR)]
    def default(self,handle,field):return {'default_status':'read','getter_status':0,'default':self.objects[handle]['value']}
    def assign(self,handle,assignment):self.objects[handle]['value']=assignment['value']


class EmbeddedEditTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve()
        self.game=self.root/'game';self.game.mkdir();self.mod=self.root/'mod';self.mod.mkdir();self.count=0
        raw=typed(OWNER,{0x18:struct.pack('<Q',1),0x15:string('Unit'),OWNER:mapping({0x61:stats(),0x99:b'preserve parent'})})
        self.source=self.mod/'Unit.acp';self.source.write_bytes(acp(raw))
        self.binding={'game':str(self.game),'dll_sha256':'fixture','backend_sha256':'fixture','profile':{'name':'fixture',
            'inline_resource_objects':{f'{CID:08X}':{'name':'TUnitStats','owner_cid':f'{OWNER:08X}','property':EMBEDDED}}}}
        for mock in (patch.object(native,'profile_for',return_value=self.binding),
                     patch.object(native,'cached',return_value={'payload':FakeStats().types()}),patch.object(native,'run_worker',side_effect=self.worker)):
            mock.start();self.addCleanup(mock.stop)

    def worker(self,game,operation,params,cache,timeout):
        self.count+=1;run=self.root/f'run-{self.count}';run.mkdir();api=FakeStats()
        if operation=='object-edit':payload=api.edit_object(params,run)
        else:
            data=bytes.fromhex(params['body_hex']);source=run/'object-source.bin';saved=run/'object-serialized.bin';source.write_bytes(data)
            handle=api.create(CID);api.deserialize_object(handle,data);saved.write_bytes(api.serialize_object(handle))
            payload={'cid':params['cid'],'name':params['name'],'source_file':str(source),'source_sha256':sha(data),
                     'output_file':str(saved),'output_sha256':file_hash(saved),'readbacks':[],
                     'properties':[{**DESCRIPTOR,**api.default(handle,DESCRIPTOR)}],'package_version':None}
        return {'operation':operation,'params':params,'payload':payload,'binding':self.binding,'run':str(run)}

    def plan(self):return edits.make_plan(self.source,'1',[{'name':'Hit Points','property':FIELD,'value':20}],self.game,source_root=self.mod,cache_dir=self.root/'cache',embedded=EMBEDDED)

    def test_plan_applies_inline_setter_without_changing_parent_data(self):
        before=inventory(self.mod);plan=self.plan();receipt=edits.apply_plan(plan,self.root/'out')
        original=read_acp(self.source)[1][0]['resource'];actual=read_acp(self.root/'out/Unit.acp')[1][0]['resource']
        a,b=original.leaves(),actual.leaves();self.assertEqual([k for k in a if a[k]!=b[k]],[EMBEDDED])
        self.assertEqual(native_objects.storage_fields(b[EMBEDDED])[FIELD],struct.pack('<i',20))
        self.assertEqual(inventory(self.mod),before);self.assertEqual(receipt['embedded'],EMBEDDED)
        self.assertTrue(receipt['proof']['independent_readback']['reader_without_setters'])

    def test_missing_or_changed_embedding_and_wrong_reader_identity_are_refused(self):
        plan=self.plan()
        for change in ('removed','other-slot','reader-type'):
            bad=copy.deepcopy(plan)
            if change=='removed':del bad['embedded']
            elif change=='other-slot':bad['embedded']='0x00471428/0x19'
            else:bad['independent_result']['params']['cid']=f'{OWNER:08X}'
            output=self.root/change
            with self.subTest(change=change),self.assertRaises(FormatError):edits.apply_plan(bad,output)
            self.assertFalse(output.exists())

    def test_unreviewed_owner_or_foreign_inline_scope_is_not_inferred(self):
        owner,source=__import__('aow4mod.native_inspection',fromlist=['source_body']).source_body(self.source,'1')
        with self.assertRaisesRegex(FormatError,'not been reviewed'):edits.subject(owner,{**source,'cid':'00472004'},EMBEDDED,self.binding)
        root=Node(owner,0,len(owner));wrong=editing.rewrite_map(root,{OWNER:editing.rewrite_map(root.mapping()[OWNER],{0x61:mapping({0x471c71:mapping({})})})})
        with self.assertRaisesRegex(FormatError,'unsupported class scope'):edits.subject(wrong,source,EMBEDDED,self.binding)

    def test_only_datastore_tag_metadata_is_accepted(self):
        self.assertEqual(edits.field_path('meta/0x12c'),'meta/0x12C');self.assertEqual(edits.property_scope('meta/0x12C'),set())
        for field in ('meta/0x18','meta/0x15','meta/0x14'):
            with self.subTest(field=field),self.assertRaises(FormatError):edits.field_path(field)


if __name__=='__main__':unittest.main()
