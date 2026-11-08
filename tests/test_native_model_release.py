import copy
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import native,native_release,native_model_release,model_links,native_objects,content_libraries,publication
from aow4mod.core import FormatError
from aow4mod.filesystem import file_hash,inventory,save_record
from test_aow4_mod_audit import mapping,array,typed,string,u32


def model_fields(text,material='[TEST]MAT\\0',mesh='[TEST]MESH\\0',locator='[TEST]MODEL\\0'):
    component=typed(0x410067,{0x1f:mapping({0x14:text(mesh)}),0x21:mapping({0x14:text(material)}),0x23:mapping({})})
    return {0x14:text(locator),0x1f:array([typed(0x412e00,{0xa:array([component])})])}


def library_bytes(material='[TEST]MAT\\0',mesh='[TEST]MESH\\0',model_count=1):
    strings=bytearray()
    def text(value):
        raw=value.encode();offset=len(strings);strings.extend(raw+b'\0');return struct.pack('<II',len(raw),offset)
    name=text('TEST')
    values=[typed(0x410035,{0x14:text('[TEST]MESH\\0')}),typed(0x41166f,{0x14:text('[TEST]MAT\\0')})]
    values.extend(typed(0x41004b,model_fields(text,material,mesh,f'[TEST]MODEL\\{i}')) for i in range(model_count))
    metadata=mapping({0x13:u32(8),0x14:name,0x15:array(values),0x1d:b'\1'})
    align=lambda b:bytes(b)+bytes(-len(b)%16)
    strings,metadata=align(strings),align(metadata)
    return content_libraries.SIGNATURE+struct.pack('<4I',8,len(strings),len(metadata),0)+bytes(8)+strings+metadata


class NativeModelReleaseTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.release=self.root/'release';self.release.mkdir();self.content=self.release/'content';self.content.mkdir()
        self.source=self.content/'TEST.clb';self.source.write_bytes(library_bytes())
        self.game=self.root/'game';self.game.mkdir();self.cache=self.root/'cache';self.worker=self.root/'worker';self.worker.mkdir()
        self.binding={'game':str(self.game),'dll_sha256':next(iter(native.PROFILES)),'backend_sha256':'fixture','profile':next(iter(native.PROFILES.values()))}
        self.public=native.public_binding(self.binding);self.material='[TEST]MAT\\0';self.mesh='[TEST]MESH\\0';self.calls=0
        for name,kw in [('native.profile_for',{'return_value':self.binding}),('native_assets.export_library',{'side_effect':self.export})]:
            mock=patch('aow4mod.native_release.'+name,**kw);mock.start();self.addCleanup(mock.stop)
        mock=patch('aow4mod.native_model_release.model_links.check',side_effect=self.model_check);mock.start();self.addCleanup(mock.stop)

    def export(self,source,game,output,*args):
        output=Path(output);output.mkdir();dest=output/'TEST.clb';shutil.copyfile(source,dest)
        return {'format':'aow4mod-native-assets/1','source_sha256':file_hash(source),'binding':self.public,
                'files':[{'name':'TEST.clb'}],'comparison':content_libraries.compare(source,dest),'diagnostics':[]}

    def model_check(self,source,locators,*args):
        self.calls+=1;folder=self.worker/str(self.calls);folder.mkdir()
        def artifact(name,raw):
            path=folder/name;path.write_bytes(raw);return {'file':str(path),'sha256':file_hash(path)}
        before=mapping(model_fields(string,self.material,self.mesh));row={'locator':'[TEST]MODEL\\0','status':3,'components':[],
            'artifacts':{'before':artifact('before.bin',before),'after':artifact('after.bin',before)},
            'comparison':native_objects.compare_bodies(before,before)}
        component={'lod':0,'component':0,'status':1 if self.mesh.endswith('9999') else 3}
        for label,locator in (('mesh',self.mesh),('material',self.material)):
            missing=locator.endswith('9999');is_mesh=locator.startswith('[TEST]MESH')
            link={'declared_locator':locator,'resolved':not missing,'resolved_cid':None if missing else '00410035' if is_mesh else '0041166F',
                  'resolved_local_locator':None if missing else locator,'same_local_member':not missing,'connected_and_activated':not missing}
            if not missing:
                link['target_ordinal']=0 if is_mesh else 1
                if label=='mesh' or not is_mesh:link['target_artifact']=artifact(label+'.bin',mapping({0x14:string(locator)}))
            component[label]=link
        component['shadow']={'declared_locator':'','resolved':False,'resolved_cid':None,'resolved_local_locator':None,'same_local_member':False,'connected_and_activated':False}
        row['components']=[component];row['issues']=model_links.issues_for(row)
        return {'format':'aow4mod-native-model-links/1','source':str(source),'source_sha256':file_hash(source),'library':'TEST',
                'selection':native_model_release.model_members(source),'binding':self.public,'models':[row],'diagnostics':[],
                'run':str(folder),'status':'review_required' if row['issues'] else 'local_links_resolved'}

    def collect(self):return native_release.collect(self.content,self.game,self.release,self.cache,_protocol=6)

    def detail(self,report):
        record=next(r for r in report['checks'] if r['operation']=='clb_model_links');path=self.release/record['detail_file']
        return record,path,json.loads(path.read_text(encoding='utf-8'))

    def refresh(self,report,record,path,detail):
        save_record(path,detail);record['detail_sha256']=file_hash(path);report['evidence_files']=inventory(self.release/'native-evidence')

    def manifest(self,report):
        save_record(self.release/'native-validation.json',report)
        for name in ('description','change_notes'):(self.release/(name+'.txt')).write_text('fixture', encoding='utf-8')
        value={'format':'aow4mod-release/1','app_id':1669000,'content_directory':'content','files':{p:r['sha256'] for p,r in inventory(self.content).items()},
               'native_validation_requested':True,'native_validation_protocol':6,'native_validation_file':'native-validation.json',
               'native_validation_sha256':file_hash(self.release/'native-validation.json'),'native_validation_summary':report['summary'],
               'release_gate':{'review_required':report['summary']['review_required'],'issues':[{'issue':'native_validation_review_required'}] if report['summary']['review_required'] else []}}
        for name in ('description','change_notes'):value[name+'_file']=name+'.txt';value[name+'_sha256']=file_hash(self.release/(name+'.txt'))
        path=self.release/'manifest.json';save_record(path,value);return path

    def test_new_protocol_automatically_collects_models_and_rechecks_without_native_code(self):
        report=self.collect();self.assertEqual(report['summary']['operations']['clb_model_links'],1)
        self.assertFalse(report['summary']['review_required']);self.assertEqual(report['summary']['model_link_issue_count'],0)
        self.worker.rename(self.root/'old-worker')
        with patch.object(model_links,'check',side_effect=AssertionError('offline must not execute native code')):
            self.assertEqual(native_release.validate(report,self.content,self.release,6),report['summary'])

    def test_more_than_sixteen_models_are_batched_without_dropping_coverage(self):
        self.source.write_bytes(library_bytes(model_count=17));batch_sizes=[]
        def batch(source,locators,*args):
            batch_sizes.append(len(locators));part=self.model_check(source,locators,*args);models=[]
            for locator in locators:
                model=copy.deepcopy(part['models'][0]);body=mapping(model_fields(string,locator=locator))
                path=self.worker/(str(self.calls)+'-'+locator.rsplit('\\',1)[1]+'.bin');path.write_bytes(body)
                model['locator']=locator;model['comparison']=native_objects.compare_bodies(body,body)
                model['artifacts']={k:{'file':str(path),'sha256':file_hash(path)} for k in ('before','after')};models.append(model)
            part['models']=models;return part
        with patch.object(model_links,'check',side_effect=batch):report=self.collect()
        self.assertEqual(batch_sizes,[16,1]);self.assertEqual(report['summary']['errors'],0)
        _,_,detail=self.detail(report);self.assertEqual(detail['comparison']['model_count'],17)

    def test_zero_diagnostics_do_not_hide_missing_or_wrong_type_model_links(self):
        self.material='[TEST]MESH\\0';self.source.write_bytes(library_bytes(self.material));report=self.collect()
        self.assertEqual(report['summary']['errors'],0);self.assertEqual(report['summary']['mismatches'],0)
        self.assertEqual(report['summary']['diagnostic_count'],0);self.assertEqual(report['summary']['model_link_issue_count'],1)
        self.assertTrue(report['summary']['review_required'])
        path=self.manifest(report);value=json.loads(path.read_text(encoding='utf-8'));value['release_gate']={'review_required':False,'issues':[]};save_record(path,value)
        with self.assertRaisesRegex(FormatError,'findings'):publication.load_release(path)

    def test_missing_mesh_is_a_frozen_link_finding(self):
        self.mesh='[TEST]MESH\\9999';self.source.write_bytes(library_bytes(mesh=self.mesh));report=self.collect()
        self.assertEqual(report['summary']['model_link_issue_count'],2)
        self.assertEqual(native_release.validate(report,self.content,self.release,6),report['summary'])

    def test_clearing_findings_and_forging_runtime_type_cannot_hide_payload_wrong_type(self):
        self.material='[TEST]MESH\\0';self.source.write_bytes(library_bytes(self.material));report=self.collect();record,path,detail=self.detail(report)
        model=detail['models'][0];link=model['components'][0]['material'];link['resolved_cid']='0041166F'
        link['target_artifact']=copy.deepcopy(model['components'][0]['mesh']['target_artifact']);model['issues']=[]
        self.refresh(report,record,path,detail)
        with self.assertRaisesRegex(FormatError,'runtime type differs'):native_release.validate(report,self.content,self.release,6)

    def test_omitted_model_evidence_and_protocol_downgrade_are_refused(self):
        report=self.collect();missing=copy.deepcopy(report);missing['checks']=[r for r in missing['checks'] if r['operation']!='clb_model_links']
        with self.assertRaisesRegex(FormatError,'coverage'):native_release.validate(missing,self.content,self.release,6)
        old=copy.deepcopy(missing);old.update(format=native_release.CONTEXT_FORMAT,scope=native_release.CONTEXT_SCOPE,summary=native_release.summary(old['checks'],3))
        self.assertEqual(native_release.validate(old,self.content,self.release,3),old['summary'])
        with self.assertRaisesRegex(FormatError,'protocol'):native_release.validate(old,self.content,self.release,6)

    def test_refreshed_model_body_hash_cannot_hide_changed_payload_link(self):
        report=self.collect();record,path,detail=self.detail(report);model=detail['models'][0]
        altered=mapping(model_fields(string,mesh='[TEST]MESH\\9'))
        for key in ('before','after'):
            file=self.release/model['artifacts'][key]['file'];file.write_bytes(altered);model['artifacts'][key]['sha256']=file_hash(file)
        model['comparison']=native_objects.compare_bodies(altered,altered)
        link=model['components'][0]['mesh'];link['declared_locator']=link['resolved_local_locator']='[TEST]MESH\\9'
        target=self.release/link['target_artifact']['file'];target.write_bytes(mapping({0x14:string('[TEST]MESH\\9')}))
        link['target_artifact']['sha256']=file_hash(target)
        record['artifact_sha256']=model['artifacts']['before']['sha256'];self.refresh(report,record,path,detail)
        with self.assertRaisesRegex(FormatError,'payload declaration'):native_release.validate(report,self.content,self.release,6)

    def test_upload_snapshot_preserves_model_witnesses_after_original_cleanup(self):
        report=self.collect();path=self.manifest(report);manifest,content=publication.load_release(path)
        frozen,stage=publication.stage_release(manifest,content,self.release)
        (self.release/'native-evidence').rename(self.release/'old-evidence');self.worker.rename(self.root/'old-worker')
        publication.check_staged(frozen)
        self.assertTrue((stage.parent/'native-evidence').is_dir())


if __name__=='__main__':unittest.main()
