import copy
from contextlib import contextmanager
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import native,native_edits as edits,native_worker,native_objects,native_authoring,editing,authoring,native_roundtrip
from aow4mod.core import FormatError,Node,read_rpk,sha
from aow4mod.filesystem import file_hash,inventory
from test_aow4_mod_audit import mapping,string,resource,acp
from test_native_authoring import pack_defaults

CID=0x471c71
FIELD='0x00471C6D/0x1'
DESCRIPTOR={'name':'Mode','category':'','codec':'enum_i32','editor_cid':'0040160D','editor_parameter':123}


def body(value=2,opaque=b'keep',rid=2):
    return mapping({0x14:string(f'[#{rid:016X}]0\\0'),0x15:string('Native target'),0x18:struct.pack('<Q',rid),
                    0x471c6d:mapping({1:struct.pack('<i',value),0x99:opaque})})


def job(value=2):
    return {**DESCRIPTOR,'property':FIELD,'value':value,'expected_before':2,'probe_value':0 if value==2 else value}


class FakeBindings:
    """Observable in-memory native model; no original game files or DLL are used."""
    edit_object=native_worker.Bindings.edit_object
    def __init__(self):self.objects={};self.counter=0;self.getter_calls=0;self.profile={'surface_class_id':0x410024}
    def types(self):return {'types':[{'cid':f'{CID:08X}','name':'TStructureSingleTargeter'}]}
    def create(self,cid):self.counter+=1;self.objects[self.counter]={'mode':2,'body':body()};return self.counter
    def release(self,handle):self.objects.pop(handle)
    @contextmanager
    def borrowed(self,handle,cid):yield handle
    def deserialize_object(self,pointer,data):
        fields=native_objects.storage_fields(data);raw=fields.get(FIELD)
        self.objects[pointer]={'mode':int.from_bytes(raw,'little',signed=True) if raw else 2,'body':data}
    def serialize_object(self,pointer):
        state=self.objects[pointer];root=Node(state['body'],0,len(state['body']));scope=root.mapping()[0x471c6d]
        value=struct.pack('<i',state['mode']) if state['mode']!=2 else None
        return editing.rewrite_map(root,{0x471c6d:editing.rewrite_map(scope,{1:value})})
    def property_records(self,handle):return [dict(DESCRIPTOR)]
    def default(self,handle,field):
        self.getter_calls+=1
        return {'default_status':'read','getter_status':0,'default':self.objects[handle]['mode']}
    def assign(self,handle,assignment):self.objects[handle]['mode']=assignment['value']


class NativeEditMaterialTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve();self.sequence=0
        self.binding={'game':str(self.root/'game'),'dll_sha256':'fixture','backend_sha256':'fixture','profile':{'name':'fixture'}}

    def result(self,value=2,original=None):
        self.sequence+=1;run=self.root/f'run-{self.sequence}';run.mkdir();original=body() if original is None else original
        params={'cid':f'{CID:08X}','name':'TStructureSingleTargeter','body_hex':original.hex(),'body_sha256':sha(original),'assignments':[job(value)]}
        payload=FakeBindings().edit_object(params,run)
        return {'operation':'object-edit','params':params,'payload':payload,'binding':self.binding,'run':str(run)},original

    def replace_artifact(self,item,key,data):
        Path(item[key+'_file']).write_bytes(data);item[key+'_sha256']=sha(data)

    def test_explicit_default_omission_is_proved_without_a_hardcoded_default_rule(self):
        result,original=self.result();after,proof=edits.verify_material(result,original)
        self.assertEqual(proof['source_normalization_fields'],[FIELD]);self.assertEqual(proof['changed_fields'],[FIELD])
        self.assertFalse(proof['mapping'][0]['output_serialized']);self.assertNotIn(FIELD,native_objects.storage_fields(after))
        self.assertEqual(proof['mapping'][0]['before'],2);self.assertEqual(proof['mapping'][0]['after'],2)
        self.assertEqual(proof['mapping'][0]['probe'],0)

    def test_nondefault_setter_changes_only_its_mapped_scalar_and_preserves_opaque_data(self):
        result,original=self.result(1);after,proof=edits.verify_material(result,original)
        self.assertEqual(native_objects.storage_fields(after)[FIELD],struct.pack('<i',1))
        self.assertEqual(native_objects.storage_fields(after)['0x00471C6D/0x99'],b'keep')
        self.assertTrue(proof['fresh_object_data_equal'])

    def test_wrong_declared_field_is_refused_even_when_getter_reports_desired_value(self):
        result,original=self.result(1);result['params']['assignments'][0]['property']='0x00471C6D/0x2'
        result['payload']['mapping_witnesses'][0]['property']='0x00471C6D/0x2'
        with self.assertRaisesRegex(FormatError,'map exclusively|unrequested fields'):edits.verify_material(result,original)

    def test_unrequested_field_loss_is_refused_despite_consistent_output_hashes_and_getters(self):
        result,original=self.result(1)
        data=Path(result['payload']['output_file']).read_bytes();root=Node(data,0,len(data))
        lost=editing.rewrite_map(root,{0x471c6d:editing.rewrite_map(root.mapping()[0x471c6d],{0x99:None})})
        for key in ('source','output'):self.replace_artifact(result['payload'],key,lost)
        with self.assertRaisesRegex(FormatError,'unrequested fields'):edits.verify_material(result,original)

    def test_identity_metadata_loss_is_not_allowed_as_scalar_normalization(self):
        result,original=self.result(1);data=Path(result['payload']['output_file']).read_bytes()
        forged=editing.rewrite_map(Node(data,0,len(data)),{0x15:string('Changed identity')})
        for key in ('source','output'):self.replace_artifact(result['payload'],key,forged)
        with self.assertRaisesRegex(FormatError,'unrequested fields'):edits.verify_material(result,original)

    def test_empty_unrequested_class_scope_cannot_disappear_behind_equal_leaf_fields(self):
        original=body();original=editing.rewrite_map(Node(original,0,len(original)),{0x470099:b'\0'})
        result,original=self.result(1,original)
        data=Path(result['payload']['output_file']).read_bytes();lost=editing.rewrite_map(Node(data,0,len(data)),{0x470099:None})
        for key in ('source','output'):self.replace_artifact(result['payload'],key,lost)
        with self.assertRaisesRegex(FormatError,'class scopes'):edits.verify_material(result,original)

    def test_compound_or_truncated_source_field_is_not_treated_as_an_integer(self):
        result,original=self.result(1);root=Node(original,0,len(original))
        compound=editing.rewrite_map(root,{0x471c6d:editing.rewrite_map(root.mapping()[0x471c6d],{1:struct.pack('<II',2,55)})})
        result['params'].update(body_hex=compound.hex(),body_sha256=sha(compound));self.replace_artifact(result['payload'],'input',compound)
        with self.assertRaisesRegex(FormatError,'primitive scalar encoding'):edits.verify_material(result,compound)

    def test_getter_types_descriptors_and_signed_zero_are_strict(self):
        self.assertFalse(edits.same('bool',False,0));self.assertFalse(edits.same('f32',-0.0,0.0))
        for change in ({'default':True},{'editor_parameter':999},{'codec':'bool'},{'default_status':'getter_failed'}):
            result,original=self.result(1);result['payload']['readbacks'][0].update(change)
            with self.assertRaises(FormatError):edits.verify_material(result,original)

    def test_missing_mapping_readback_and_equal_probe_do_not_verify(self):
        for change in ('mapping','before','after','probe'):
            result,original=self.result(1)
            if change=='mapping':result['payload']['mapping_witnesses']=[]
            elif change=='before':result['payload']['before_values']=[]
            elif change=='after':result['payload']['readbacks']=[]
            else:result['params']['assignments'][0]['probe_value']=2
            with self.assertRaises(FormatError):edits.verify_material(result,original)

    def test_artifact_path_escape_or_hash_change_is_refused(self):
        result,original=self.result(1);outside=self.root/'outside.bin';outside.write_bytes(original)
        result['payload']['input_file']=str(outside)
        with self.assertRaisesRegex(FormatError,'path/size'):edits.verify_material(result,original)
        result,original=self.result(1);Path(result['payload']['output_file']).write_bytes(b'changed')
        with self.assertRaises(FormatError):edits.verify_material(result,original)

    def test_worker_checks_descriptor_before_calling_a_getter_and_releases_objects(self):
        params={'cid':f'{CID:08X}','name':'TStructureSingleTargeter','body_hex':body().hex(),'body_sha256':sha(body()),
                'assignments':[{**job(),'codec':'bool','editor_cid':'00401607'}]}
        api=FakeBindings()
        with self.assertRaisesRegex(FormatError,'before getter access'):api.edit_object(params,self.root)
        self.assertEqual(api.getter_calls,0);self.assertEqual(api.objects,{})

    def test_scalar_encoder_rejects_compound_and_wrong_width_values(self):
        self.assertTrue(edits.scalar(struct.pack('<i',-7),'i32',-7));self.assertTrue(edits.scalar(b'\x01','bool',True))
        self.assertTrue(edits.scalar(struct.pack('<f',1.25),'f32',1.25));self.assertTrue(edits.scalar(string('abc'),'string','abc'))
        self.assertFalse(edits.scalar(struct.pack('<d',1.25),'f32',1.25));self.assertFalse(edits.scalar(b'\x02','bool',True))
        self.assertFalse(edits.scalar(mapping({1:b'x'}),'string','x'))


class NativeEditAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.fields=[{**DESCRIPTOR,'default_status':'read','getter_status':0,'default':2}]
        enriched=lambda fields,*args:[{**field,'enum':{'values':[{'value':0},{'value':1},{'value':2}]}} for field in fields]
        self.mock=patch.object(native,'enrich_enums',side_effect=enriched);self.mock.start();self.addCleanup(self.mock.stop)

    def test_enum_noop_selects_a_distinct_verified_probe_and_normalizes_path(self):
        rows=edits.assignments([{'name':'Mode','property':'0x471C6D/0x01','value':2}],self.fields,{},'schema')
        self.assertEqual(rows[0]['property'],FIELD);self.assertEqual(rows[0]['probe_value'],0)
        self.assertEqual(edits.assignments([{'name':'Mode','property':FIELD,'value':1}],self.fields,{},'schema')[0]['probe_value'],1)

    def test_non_enum_noop_requires_an_explicit_valid_distinct_probe(self):
        fields=[{**self.fields[0],'codec':'i32','editor_cid':'00401418'}]
        value={'name':'Mode','property':FIELD,'value':2}
        with self.assertRaisesRegex(FormatError,'explicit distinct'):edits.assignments([value],fields,{},None)
        self.assertEqual(edits.assignments([{**value,'probe_value':3}],fields,{},None)[0]['probe_value'],3)
        with self.assertRaises(FormatError):edits.assignments([{**value,'probe_value':2}],fields,{},None)

    def test_bool_probe_is_typed_and_duplicate_or_nested_paths_are_refused(self):
        fields=[{**self.fields[0],'codec':'bool','editor_cid':'00401607','default':False}]
        value={'name':'Mode','property':FIELD,'value':False}
        self.assertTrue(edits.assignments([value],fields,{},None)[0]['probe_value'])
        with self.assertRaises(FormatError):edits.assignments([value,value],fields,{},None)
        for path in ('meta/0x15','0x471C6D/1/0','0x1/1'):
            with self.assertRaises(FormatError):edits.assignments([{**value,'property':path}],fields,{},None)

    def test_unreadable_getters_unsupported_editors_and_bad_values_are_refused(self):
        value={'name':'Mode','property':FIELD,'value':2}
        for fields in ([{**self.fields[0],'default_status':'getter_failed'}],[{**self.fields[0],'codec':None}]):
            with self.assertRaises(FormatError):edits.assignments([value],fields,{},'schema')
        for values in ([],[value]*33,{'Mode':2},[{**value,'value':99}],[{**value,'unknown':True}]):
            with self.assertRaises(FormatError):edits.assignments(values,self.fields,{},'schema')


class NativeEditPlanTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve()
        self.game=self.root/'game';self.game.mkdir();self.mod=self.root/'mod';self.mod.mkdir()
        self.acp=self.mod/'Own.acp';raw=acp(resource())
        self.acp.write_bytes(raw[:19]+editing.rewrite_wrapper(Node(raw,19,len(raw)),0x40009d,{0x1a:authoring.string_vector(['OWN.RPK'])}))
        self.pack=self.mod/'Packs/Own.rpk';self.pack.parent.mkdir();self.pack.write_bytes(native_authoring.resource_pack_bytes(pack_defaults(),body(),f'{CID:08X}','Own.rpk'))
        self.binding={'game':str(self.game),'dll_sha256':'fixture','backend_sha256':'fixture','profile':{'name':'fixture'}};self.count=0
        self.fixture={'name':'Mode','property':FIELD,'value':2}
        for mock in (patch.object(native,'profile_for',return_value=self.binding),
                     patch.object(native,'cached',return_value={'payload':{'types':[{'cid':f'{CID:08X}','name':'TStructureSingleTargeter'}]}}),
                     patch.object(native,'run_worker',side_effect=self.worker),
                     patch.object(edits.native_roundtrip,'run',side_effect=self.pack_roundtrip),
                     patch.object(edits.native_validation,'diagnostics',return_value=['fixture finding']),
                     patch.object(native,'enrich_enums',side_effect=lambda fields,*args:[{**field,'enum':{'values':[{'value':0},{'value':1},{'value':2}]}} for field in fields])):
            mock.start();self.addCleanup(mock.stop)

    def worker(self,game,operation,params,cache,timeout):
        self.count+=1;run=self.root/f'run-{self.count}';run.mkdir();api=FakeBindings()
        if operation=='object-edit':payload=api.edit_object(params,run)
        else:
            data=bytes.fromhex(params['body_hex']);source=run/'object-source.bin';output=run/'object-serialized.bin';source.write_bytes(data)
            handle=api.create(CID);api.deserialize_object(handle,data);output.write_bytes(api.serialize_object(handle))
            field={**DESCRIPTOR,**api.default(handle,DESCRIPTOR)};api.release(handle)
            payload={'cid':params['cid'],'name':params['name'],'source_file':str(source),'source_sha256':sha(data),
                'output_file':str(output),'output_sha256':file_hash(output),'readbacks':[],'properties':[field],'package_version':None}
        return {'operation':operation,'params':params,'payload':payload,'binding':self.binding,'run':str(run)}

    def pack_roundtrip(self,candidate,game,output,*args,**kwargs):
        output.mkdir();destination=output/candidate.name;shutil.copyfile(candidate,destination)
        return {'output_file':str(destination),'comparison':native_roundtrip.compare(candidate,destination),'run':str(output),
                'binding':native.public_binding(self.binding),'diagnostics':['fixture finding']}

    def plan(self,**kwargs):
        values=dict(path=self.mod,rid='2',values=[self.fixture],game=self.game,schema_path='schema',cache_dir=self.root/'cache')
        values.update(kwargs);return edits.make_plan(**values)

    def test_plan_and_apply_preserve_acp_other_files_and_write_native_canonical_body(self):
        before=inventory(self.mod);plan=self.plan();result=edits.apply_plan(plan,self.root/'out')
        self.assertEqual(inventory(self.mod),before);self.assertEqual((self.root/'out/Own.acp').read_bytes(),self.acp.read_bytes())
        row=read_rpk(self.root/'out/Packs/Own.rpk')[1][0];self.assertNotIn('0x00471C6D/0x01',row.leaves())
        self.assertTrue(result['proof']['fresh_object_data_equal']);self.assertTrue(result['native_pack_review_required'])

    def test_source_generator_values_and_native_binding_changes_block_apply(self):
        plan=self.plan()
        for changed in ('generator','binding','values'):
            altered=copy.deepcopy(plan)
            if changed=='generator':altered['generator_sha256']='old'
            elif changed=='binding':altered['binding']['backend_sha256']='old'
            else:
                path=self.root/'values.json';path.write_text('changed', encoding='utf-8');altered['values_source']={'file':str(path),'sha256':'old'}
            with self.assertRaises(FormatError):edits.apply_plan(altered,self.root/('bad-'+changed))
        (self.mod/'user.txt').write_text('later work', encoding='utf-8')
        with self.assertRaises(FormatError):edits.apply_plan(plan,self.root/'bad-source')

    def test_hidden_or_removed_native_pack_review_evidence_is_refused(self):
        plan=self.plan()
        for change in ('flag','missing','log'):
            altered=copy.deepcopy(plan)
            if change=='flag':altered['review_required']=False
            elif change=='missing':altered['native_pack']=None
            else:altered['native_pack']['diagnostics']=[]
            with self.assertRaises(FormatError):edits.apply_plan(altered,self.root/('bad-'+change))

    def test_independent_reader_value_and_run_must_match_the_persisted_edit(self):
        plan=self.plan()
        for change in ('value','setter','same-run','selection'):
            altered=copy.deepcopy(plan)
            if change=='value':altered['independent_result']['payload']['properties'][0]['default']=1
            elif change=='setter':altered['independent_result']['params']['assignments']=[job()]
            elif change=='same-run':altered['independent_result']['run']=altered['native_result']['run']
            else:altered['independent_result']['params']['property_filter']=[]
            with self.assertRaises(FormatError):edits.apply_plan(altered,self.root/('bad-reader-'+change))

    def test_exact_rpk_needs_complete_mod_root_and_output_cannot_overwrite(self):
        with self.assertRaises(FormatError):self.plan(path=self.pack)
        plan=self.plan(path=self.pack,source_root=self.mod)
        for output in (self.mod,self.mod/'nested',self.game/'output'):
            with self.assertRaises(FormatError):edits.apply_plan(plan,output)
        with self.assertRaisesRegex(FormatError,'require a resource'):self.plan(rid=None)

    def test_forged_wrapped_file_and_missing_mapping_cannot_be_applied(self):
        plan=self.plan();altered=copy.deepcopy(plan);altered['file_hex']='00';altered['file_sha256']=sha(b'\0')
        with self.assertRaises(FormatError):edits.apply_plan(altered,self.root/'bad-file')
        altered=copy.deepcopy(plan);altered['native_result']['payload']['mapping_witnesses']=[]
        with self.assertRaises(FormatError):edits.apply_plan(altered,self.root/'bad-mapping')

    def test_source_race_before_staging_preserves_user_work_and_commits_no_output(self):
        plan=self.plan();copy_directory=edits.copy_directory
        def racing(*args,**kwargs):
            (self.mod/'user.txt').write_text('keep this', encoding='utf-8');return copy_directory(*args,**kwargs)
        with patch.object(edits,'copy_directory',side_effect=racing),self.assertRaisesRegex(FormatError,'before staging'):
            edits.apply_plan(plan,self.root/'bad-race')
        self.assertFalse((self.root/'bad-race').exists());self.assertTrue((self.mod/'user.txt').exists())


if __name__=='__main__':unittest.main()
