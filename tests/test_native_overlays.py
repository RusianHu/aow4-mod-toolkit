import copy
from contextlib import nullcontext
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import native,native_overlays as overlays,editing,authoring,native_dependencies,overlay_imports
from aow4mod.core import FormatError,Node,Resource,read_rpk,sha
from aow4mod.filesystem import file_hash,inventory
from test_aow4_mod_audit import acp,resource,string,mapping
from test_resource_groups import pack,category,located,groups


class NativeOverlayTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name).resolve()
        self.game=self.root/'game';(self.game/'Language/EN').mkdir(parents=True);(self.game/'Language/EN/EN.MO').write_bytes(b'fixture language')
        from test_pack_imports import named_pack
        (self.game/'Content').mkdir();(self.game/'Content/Base.rpk').write_bytes(named_pack([located(99,0,1,'Dependency')],'BASE.RPK'))
        self.base=self.root/'base/Own.rpk';self.base.parent.mkdir()
        self.base.write_bytes(pack([(0,groups.GENERIC,[category('Objects',0)],[located(1,0,1,'Base'),located(2,0,2,'Other')])]))
        self.first=self.mod('first',1);self.second=self.mod('second',2);self.cache=self.root/'cache'
        self.binding={'game':str(self.game),'dll_sha256':'fixture','backend_sha256':'fixture','profile':{'name':'fixture'}}
        self.runtime=None;self.counter=0;self.mutate=None;self.skip=False;self.reorder=False
        self.corrupt_preparation=False
        mock=patch.object(native,'run_worker',side_effect=self.worker);mock.start();self.addCleanup(mock.stop)
        mock=patch('aow4mod.native_overlays.native_validation.diagnostics',return_value=[]);mock.start();self.addCleanup(mock.stop)

    def mod(self,name,value,operation=0x40,pack_name='OWN.RPK',rid=1):
        path=self.root/name;path.mkdir();obj=located(rid,0,1,name)
        obj=editing.rewrite_typed(Node(obj,0,len(obj)),{0x471c6d:mapping({1:struct.pack('<I',value)})})
        raw=acp(obj,operation);root=Node(raw,19,len(raw));scope=root.typed()[1][0x40009d].mapping()
        changes=editing.rewrite_array(scope[0x26],lambda node,index:editing.rewrite_wrapper(node,0x401204,{1:string(pack_name)}))
        raw=raw[:19]+editing.rewrite_wrapper(root,0x40009d,{0x26:changes})
        (path/(name+'.acp')).write_bytes(raw);return path

    def worker(self,game,operation,params,cache,timeout):
        self.counter+=1;run=self.root/f'run-{self.counter}';run.mkdir();data=Path(params['path']).read_bytes()
        application={'baseline':self.saved(run/'baseline',data),'steps':[],'library_classes':['0400017F']}
        for index,record in enumerate(params['modifications']):
            body=bytes.fromhex(record['body_hex']);record_file=run/f'mod-data-{index}.bin';record_file.write_bytes(body)
            fields=Node(body,0,len(body)).mapping()[0x401204].mapping();updates={}
            for node in fields[2].array().values():
                resource=Resource.parse(node.typed()[1][0x401206].mapping()[1]);updates[resource.rid]=resource
            applied=[]
            def change(pack,node):
                old=Resource.parse(node)
                if old.rid not in updates or self.skip:return node.raw()
                applied.append(old.rid)
                return editing.rewrite_typed(updates[old.rid].node,{0x14:old.fields[0x14].raw()})
            if operation=='package-dependencies':
                data=native_dependencies.append(data,record)
                if self.corrupt_preparation:
                    data=editing.transform_records(data,'.rpk',lambda p,n:editing.rewrite_typed(n,{0x15:string('unrequested')}))
            else:data=editing.transform_records(data,'.rpk',change)
            if self.reorder:
                root=Node(data,176,len(data));f=root.mapping();containers={}
                for key,node in f[13].mapping().items():
                    wrapper=node.typed()[1];feature=wrapper[0x33].mapping()
                    objects=editing.replace_array_items(feature[0x15],[v.raw() for v in reversed(list(feature[0x15].array().values()))])
                    containers[key]=editing.rewrite_typed(node,{0x33:editing.rewrite_map(wrapper[0x33],{0x15:objects})})
                payload=editing.rewrite_map(root,{13:editing.rewrite_map(f[13],containers)})
                data=data[:12]+struct.pack('<I',len(payload))+data[16:176]+payload
            output=self.saved(run/f'step-{index}',data)
            application['steps'].append({'file':record['file'],'sha256':record['sha256'],'entries':[
                {'id':row['id'],'cid':row['cid'],'operation':0x40,'applied_before':0,'applied_after':int(int(row['id'],16) in applied)}
                for row in record['resources']], 'mod_data_file':str(record_file),'mod_data_sha256':sha(body),'output':output})
        payload={'sha256':params['sha256'],'language_sha256':params['language_sha256'],'callback_result':0,
                 'content_present':True,'context_libraries':params.get('libraries',[]),'context_packs':params.get('packs',[]),
                 'dependencies_only':operation=='package-dependencies','application':application}
        self.runtime={'operation':operation,'params':params,'payload':payload,'binding':self.binding,'run':str(run)}
        if self.mutate:self.mutate()
        return self.runtime

    def saved(self,directory,data):
        directory.mkdir();path=directory/'Own.rpk';path.write_bytes(data)
        return {'output_file':str(path),'output_sha256':sha(data),'save_result':0,'native_pack_class':'0400001A'}

    def check(self,mods=None,output=None):
        return overlays.run(self.base,mods or [self.first],self.game,output or self.root/'result',self.cache)

    def add_import(self,mod=None,dependency='LEAF.RPK',reverse=False):
        from test_pack_imports import named_pack
        from aow4mod import pack_imports
        mod=mod or self.first;path=next(mod.glob('*.acp'))
        path.write_bytes(overlay_imports.rewrite(path.read_bytes(),'OWN.RPK',[dependency]))
        if dependency=='LEAF.RPK':
            data=named_pack([located(77,0,1,'Leaf')],'LEAF.RPK')
            if reverse:data=pack_imports.rewrite(data,['OWN.RPK'])
            (mod/'Leaf.rpk').write_bytes(data)

    def test_additive_preparation_context_copy_and_exact_duplicate_order(self):
        self.add_import();self.add_import(self.second)
        # Both Mods contain an identical dependency; the collector still sees
        # both declarations and must retain the duplicate string.
        report=self.check([self.first,self.second])
        self.assertEqual(self.counter,2)
        self.assertTrue(report['proof']['dependency_preparation']['dependency_preparation_matched'])
        self.assertTrue(report['proof']['scoped_application_witness_matched'])
        data=(self.root/'result/prepared/Own.rpk').read_bytes();fields=Node(data,176,len(data)).mapping()
        from aow4mod.core import string_list
        self.assertEqual(string_list(fields[0x25]),['LEAF.RPK','LEAF.RPK'])
        self.assertEqual(len(report['context_packs']),1)
        copied=self.root/'result'/report['context_packs'][0]['artifact']
        self.assertEqual(file_hash(copied),file_hash(self.first/'Leaf.rpk'))

    def test_import_cycles_and_missing_required_imports_fail_before_native(self):
        self.add_import(reverse=True)
        with self.assertRaisesRegex(FormatError,'import cycle'):self.check()
        self.assertEqual(self.counter,0)
        path=next(self.first.glob('*.acp'))
        path.write_bytes(overlay_imports.rewrite(path.read_bytes(),'OWN.RPK',['MISSING.RPK']))
        with self.assertRaisesRegex(FormatError,'Missing required'):self.check()
        self.assertEqual(self.counter,0)

    def test_preparation_cannot_change_unrelated_resources(self):
        self.add_import();self.corrupt_preparation=True
        with self.assertRaisesRegex(FormatError,'preparation changed unexpected data'):self.check()
        self.assertFalse((self.root/'result').exists());self.assertEqual(self.counter,1)

    def test_dependency_phase_and_context_binding_cannot_be_hidden(self):
        self.check();params=self.runtime['params'];payload=copy.deepcopy(self.runtime['payload'])
        payload['dependencies_only']=True
        with self.assertRaisesRegex(FormatError,'phase'):native.validate_payload('package-apply',payload,params)
        payload['dependencies_only']=False;payload['context_packs']=[{'file':'invented'}]
        with self.assertRaisesRegex(FormatError,'RPK context'):native.validate_payload('package-apply',payload,params)

    def test_explicit_order_changes_final_value_and_every_step_is_checked(self):
        report=self.check([self.first,self.second]);self.assertTrue(report['proof']['scoped_application_witness_matched'])
        final=read_rpk(self.root/'result/step-1-Own.rpk')[1][0]
        self.assertEqual(final.leaves()['0x00471C6D/0x01'],struct.pack('<I',2))
        reverse=self.check([self.second,self.first],self.root/'reverse');self.assertTrue(reverse['proof']['scoped_application_witness_matched'])
        self.assertEqual(read_rpk(self.root/'reverse/step-1-Own.rpk')[1][0].leaves()['0x00471C6D/0x01'],struct.pack('<I',1))

    def test_native_reordering_is_reported_without_hiding_resource_data(self):
        self.reorder=True;report=self.check();step=report['proof']['steps'][0]
        self.assertTrue(step['comparison']['resource_order_changed']);self.assertTrue(step['comparison']['all_expected_data_equal'])
        self.assertTrue(step['comparison']['library_membership_equal'])

    def test_successful_return_and_saved_pack_do_not_hide_skipped_modification(self):
        self.skip=True;report=self.check()
        self.assertFalse(report['proof']['scoped_application_witness_matched']);self.assertTrue(report['review_required'])
        self.assertFalse(report['proof']['steps'][0]['all_marked_applied'])

    def test_no_matching_pack_is_not_evaluated_and_ignored_mod_is_explicit(self):
        other=self.mod('other',1,pack_name='OTHER.RPK')
        with self.assertRaisesRegex(FormatError,'not evaluated'):self.check([other])
        report=self.check([other,self.first]);self.assertEqual(len(report['mods_without_selected_overrides']),1)

    def test_missing_id_wrong_type_and_unsupported_operation_fail_before_native_execution(self):
        missing=self.mod('missing',1,rid=3)
        for path in (missing,self.mod('delete',1,operation=0x80)):
            with self.assertRaises(FormatError):self.check([path])
        raw=next(self.first.glob('*.acp')).read_bytes()
        changed=editing.transform_records(raw,'.acp',lambda pack,node:struct.pack('<I',0x471c72)+node.raw()[4:])
        next(self.first.glob('*.acp')).write_bytes(changed)
        with self.assertRaisesRegex(FormatError,'ID/type'):self.check()
        self.assertEqual(self.counter,0)

    def test_proof_rejects_resource_loss_extra_data_and_input_readback_differences(self):
        self.check();params=self.runtime['params'];original=self.runtime['payload']
        targets=overlays.collect_records(self.base,[self.first])[-1]
        changed=copy.deepcopy(original);step=changed['application']['steps'][0]
        path=Path(step['output']['output_file']);data=path.read_bytes()
        altered=editing.transform_records(data,'.rpk',lambda pack,node:None if Resource.parse(node).rid==2 else node.raw())
        path.write_bytes(altered);step['output']['output_sha256']=sha(altered)
        proof=overlays.verify_application(self.base,params,changed,targets)
        self.assertFalse(proof['scoped_application_witness_matched']);self.assertEqual(proof['steps'][0]['comparison']['missing_ids'],['0000000000000002'])
        path.write_bytes(data);changed=copy.deepcopy(original);step=changed['application']['steps'][0]
        body=Path(step['mod_data_file']).read_bytes();root=Node(body,0,len(body));scope=root.mapping()[0x401204]
        normalized=editing.rewrite_map(root,{0x401204:editing.rewrite_map(scope,{0x99:b'changed'})})
        Path(step['mod_data_file']).write_bytes(normalized);step['mod_data_sha256']=sha(normalized)
        self.assertFalse(overlays.verify_application(self.base,params,changed,targets)['scoped_application_witness_matched'])

    def test_metadata_and_empty_resource_class_scope_changes_are_visible(self):
        _,rows=read_rpk(self.base);expected={r.rid:r for r in rows};path=self.root/'altered.rpk'
        data=self.base.read_bytes();root=Node(data,176,len(data));payload=editing.rewrite_map(root,{0x88:b'changed'})
        path.write_bytes(data[:12]+struct.pack('<I',len(payload))+data[16:176]+payload)
        self.assertFalse(overlays.compare_stage(self.base,path,expected)['nonresource_metadata_equal'])
        data=editing.transform_records(data,'.rpk',lambda pack,node:editing.rewrite_typed(node,{0x470099:b'\0'}) if Resource.parse(node).rid==1 else node.raw())
        path.write_bytes(data);self.assertFalse(overlays.compare_stage(self.base,path,expected)['all_expected_data_equal'])

    def test_source_inputs_remain_identical_and_output_is_independent_of_cache(self):
        before=inventory(self.first);base=file_hash(self.base);report=self.check()
        self.assertEqual(inventory(self.first),before);self.assertEqual(file_hash(self.base),base)
        for item in report['artifacts']:self.assertEqual(file_hash(self.root/'result'/item['file']),item['sha256'])

    def test_source_changes_during_run_abort_output_without_deleting_new_user_work(self):
        self.mutate=lambda:(self.first/'user.txt').write_text('new work', encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'Mod inputs changed'):self.check()
        self.assertFalse((self.root/'result').exists());self.assertTrue((self.first/'user.txt').exists())

    def test_protected_existing_outputs_and_bad_step_coverage_are_rejected(self):
        for output in (self.root/'result',self.first/'output',self.game/'output',self.base.parent/'output'):
            if output==self.root/'result':output.mkdir()
            with self.assertRaises(FormatError):self.check(output=output)
        self.check(output=self.root/'valid');payload=copy.deepcopy(self.runtime['payload']);payload['application']['steps']=[]
        with self.assertRaisesRegex(FormatError,'coverage'):native.validate_payload('package-apply',payload,self.runtime['params'])

    @unittest.skipUnless(os.name=='nt','Windows sharing guards')
    def test_read_guard_prevents_writes_and_releases_all_handles(self):
        before=self.base.read_bytes()
        with overlays.locked_inputs([self.base]):
            self.assertEqual(self.base.read_bytes(),before)
            with self.assertRaises(OSError):self.base.write_bytes(b'bad')
            with self.assertRaises(OSError):self.base.unlink()
        self.base.write_bytes(before)


if __name__=='__main__':unittest.main()
