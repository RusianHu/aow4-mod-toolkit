import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import asset_templates, asset_yaml, native
from aow4mod.core import FormatError
from aow4mod.filesystem import file_hash
from test_aow4_mod_audit import mapping, u32
from test_content_libraries import library_bytes


class TemplateSelectionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        (self.root/'inputs').mkdir();self.source=self.root/'inputs/fixture.clb';self.source.write_bytes(library_bytes())

    def test_exact_selection_uses_canonical_identity_and_order(self):
        name,rows=asset_templates.selection(self.source,['[fixture]mesh\\0'])
        self.assertEqual(name,'FIXTURE');self.assertEqual(rows[0]['locator'],'[FIXTURE]MESH\\0')
        self.assertEqual(rows[0]['ordinal'],0)

    def test_invalid_ambiguous_and_unknown_selections_are_refused(self):
        for values in ([],['[FIXTURE]MESH\\0']*17,['[FIXTURE]MESH\\0','[fixture]mesh\\0'],
                       ['garbage'],['[FIXTURE]MESH\\9'],[None]):
            with self.subTest(values=values),self.assertRaises(FormatError):asset_templates.selection(self.source,values)
        name,rows=asset_templates.selection(self.source,['[FIXTURE]MESH\\0'])
        with patch.object(asset_templates.content_libraries,'read') as read:
            rows[0]['type_id']='0x00410024';read.return_value.name=name;read.return_value.members=[{'identity':rows[0]}]
            with self.assertRaisesRegex(FormatError,'reviewed'):asset_templates.selection(self.source,['[FIXTURE]MESH\\0'])

    def test_worker_receipt_requires_exact_selection_and_object_coverage(self):
        params={'sha256':'source','language_sha256':'language','selection':[{'locator':'[X]MODEL\\0'}],'library_name':'X'}
        valid={'sha256':'source','language_sha256':'language','callback_result':0,'content_present':True,
               'artifacts':[{},{}],'selection':params['selection'],'library_name':'X',
               'objects':[{'locator':'[X]MODEL\\0'}]}
        native.validate_payload('library-extract',valid,params)
        for key,value in [('objects',[]),('objects',[{'locator':'[X]MODEL\\1'}]),('selection',[]),('library_name','OTHER')]:
            broken={**valid,key:value}
            with self.subTest(key=key,value=value),self.assertRaises(FormatError):native.validate_payload('library-extract',broken,params)


class TemplateExtractionTests(unittest.TestCase):
    def setUp(self):
        TemplateSelectionTests.setUp(self)
        try:asset_yaml.yaml_module()
        except FormatError:self.skipTest('assets extra is optional')
        self.game=self.root/'game';(self.game/'Language/EN').mkdir(parents=True)
        (self.game/'Language/EN/EN.MO').write_bytes(b'language');(self.game/native.DLL).write_bytes(b'dll')
        self.output=self.root/'output';self.binding={'game':str(self.game),'dll_sha256':'d','backend_sha256':'b','profile':{'name':'fixture'}}
        self.document={'YAMLRPKVersion':'1','19_*(int*)&FVersionNumber':'8','20_FName':'FIXTURE',
                       '21_FList':{'cid':'4194433','cname':'TEResourceList','1_this':[
                           {'cid':str(0x410035),'cname':'TREMeshResourceDEV','20_FName':'[FIXTURE]MESH\\0','21_FUserName':'测试 member'}]}}
        self.runs=[];self.tamper=None
        self.addCleanup(patch.stopall)
        patch.object(native,'run_worker',side_effect=self.worker).start()
        patch.object(native,'profile_for',return_value=self.binding).start()
        patch.object(asset_templates.native_validation,'diagnostics',return_value=[]).start()

    def worker(self,game,operation,params,cache,timeout):
        run=self.root/f'run-{len(self.runs)}';run.mkdir();self.runs.append(run)
        reload=Path(params['path'])!=self.source
        binary=run/'fixture.clb';binary.write_bytes(library_bytes(pixel=b'changed' if self.tamper=='clb' and reload else b'pixels'))
        document=copy.deepcopy(self.document)
        if self.tamper=='yaml':document['21_FList']['1_this'][0]['20_FName']='[FIXTURE]MESH\\9'
        text=run/'fixture.clb.yaml';text.write_text(asset_yaml.dump(document),encoding='utf-8')
        body=run/'body.bin';body.write_bytes(mapping({0x14:u32(2 if self.tamper=='object' and reload else 1)}))
        objects=[{'file':str(body),'sha256':file_hash(body),'locator':'[FIXTURE]MESH\\0'}]
        if self.tamper=='object_hash' and reload:objects[0]['sha256']='0'*64
        return {'run':str(run),'binding':self.binding,'payload':{'artifacts':[
            {'file':str(p),'sha256':file_hash(p),'size':p.stat().st_size} for p in (binary,text)],'objects':objects}}

    def extract(self):
        return asset_templates.extract(self.source,['[FIXTURE]MESH\\0'],self.game,self.output,self.root/'cache')

    def test_independent_source_objects_and_file_are_compared(self):
        before=self.source.read_bytes();result=self.extract()
        self.assertEqual(len(self.runs),2);self.assertTrue(result['comparison']['all_compared_data_equal'])
        self.assertTrue(result['object_comparisons'][0]['byte_identical'])
        self.assertEqual(before,self.source.read_bytes());self.assertTrue((self.output/'fixture.clb.yaml').is_file())

    def test_refreshed_hash_cannot_hide_object_or_clb_mutation(self):
        for kind in ('object','clb','object_hash','yaml'):
            self.tamper=kind
            with self.subTest(kind=kind),self.assertRaises(FormatError):self.extract()
            self.assertFalse(self.output.exists())

    def test_changed_file_during_copy_is_refused_and_output_removed(self):
        original=shutil.copyfile
        def race(source,dest):
            original(source,dest)
            if Path(dest).suffix=='.clb':Path(dest).write_bytes(b'changed during copy')
        with patch.object(asset_templates.shutil,'copyfile',side_effect=race),self.assertRaisesRegex(FormatError,'copying'):self.extract()
        self.assertFalse(self.output.exists())

    def test_existing_output_does_not_start_native_process(self):
        self.output.mkdir();(self.output/'keep').write_bytes(b'kept')
        with self.assertRaises(FormatError):self.extract()
        self.assertEqual(self.runs,[]);self.assertEqual((self.output/'keep').read_bytes(),b'kept')


if __name__=='__main__':unittest.main()
