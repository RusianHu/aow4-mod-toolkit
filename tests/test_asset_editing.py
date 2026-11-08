import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import asset_editing, asset_yaml, native
from aow4mod.core import FormatError
from aow4mod.filesystem import inventory, file_hash
from test_aow4_mod_audit import acp, resource
from test_content_libraries import library_bytes


try:asset_yaml.yaml_module();YAML=True
except FormatError:YAML=False


@unittest.skipUnless(YAML, 'assets extra is optional')
class AssetEditTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.mod=self.root/'mod';self.mod.mkdir();self.acp=self.mod/'mod.acp';self.acp.write_bytes(acp(resource()))
        self.game=self.root/'game';self.game.mkdir();self.library=self.game/'FIXTURE.clb';self.library.write_bytes(library_bytes())
        self.export=self.root/'export';self.export.mkdir();self.compiled=self.root/'compiled';self.compiled.mkdir()
        self.binding={'game':str(self.game),'dll_sha256':'fixture','backend_sha256':'fixture','profile':{'name':'fixture'}}
        self.before={'YAMLRPKVersion':'1','19_*(int*)&FVersionNumber':'8','20_FName':'FIXTURE',
                     '21_FList':{'cid':'4194433','cname':'TEResourceList','1_this':[
                         {'cid':str(0x410035),'cname':'TREMesh','20_FName':'[FIXTURE]MESH\\0',
                          '21_FUserName':'测试 member',str(0x410035)+'_scope':{'1_Value':'1'}}]}}
        self.path=['21_FList','1_this',0,str(0x410035)+'_scope','1_Value']
        desired=copy.deepcopy(self.before);old,new=asset_yaml.set_value(desired,self.path,2)
        baseline=self.export/'FIXTURE.clb.yaml';baseline.write_text(asset_yaml.dump(self.before),encoding='utf-8')
        self.desired=self.root/'desired.yaml';self.desired.write_text(asset_yaml.dump(desired),encoding='utf-8')
        binary=self.compiled/'FIXTURE.clb';binary.write_bytes(library_bytes(value=2))
        text=self.compiled/'FIXTURE.clb.yaml';text.write_text(asset_yaml.dump(desired),encoding='utf-8')
        files=[{'name':p.name,'sha256':file_hash(p),'size':p.stat().st_size} for p in [binary,text]]
        self.plan={'format':asset_editing.FORMAT,'generator_sha256':file_hash(asset_editing.__file__),
            'game':str(self.game),'mod_source':str(self.acp),'source_files':inventory(self.mod),
            'library':str(self.library),'library_sha256':file_hash(self.library),'relative_path':'Libraries/FIXTURE.clb',
            'edit':{'path':self.path,'old':old,'new':new,'requested':2,'float32':False},
            'changes':asset_yaml.differences(self.before,desired),
            'export':{'source':str(self.library),'source_sha256':file_hash(self.library),'output':str(self.export),
                      'files':[{}, {'name':baseline.name,'sha256':file_hash(baseline)}]},
            'compile':{'format':'aow4mod-native-asset-compile/1','binding':native.public_binding(self.binding),
                       'source':str(self.desired),'source_sha256':file_hash(self.desired),'output':str(self.compiled),
                       'yaml_fields_preserved':True,'member_identities_preserved':True,'files':files},
            'desired_yaml':str(self.desired),'desired_yaml_sha256':file_hash(self.desired),
            'compiled_file':str(binary),'compiled_sha256':file_hash(binary),'normalization_review_required':False,
            'coverage':'fixture'}

    def apply(self,plan):
        with patch.object(native,'profile_for',return_value=self.binding):
            return asset_editing.apply_plan(plan,self.root/'staged')

    def test_apply_stages_only_reviewed_asset_and_preserves_source(self):
        before=inventory(self.mod);result=self.apply(self.plan)
        self.assertEqual(inventory(self.mod),before)
        self.assertEqual((self.root/'staged/mod.acp').read_bytes(),self.acp.read_bytes())
        self.assertEqual(file_hash(self.root/'staged/Libraries/FIXTURE.clb'),self.plan['compiled_sha256'])
        self.assertTrue(result['native_yaml_readback_verified'])

    def test_source_artifact_and_generator_changes_are_refused(self):
        for key in ['source_files','generator_sha256','compiled_sha256','desired_yaml_sha256']:
            plan=copy.deepcopy(self.plan);plan[key]={} if key=='source_files' else 'changed'
            with self.assertRaises(FormatError):self.apply(plan)
        self.assertFalse((self.root/'staged').exists())

    def test_receipt_cannot_be_rebound_to_different_binary(self):
        plan=copy.deepcopy(self.plan);plan['compile']['files'][0]['sha256']='different'
        with self.assertRaisesRegex(FormatError,'receipt'):self.apply(plan)

    def test_hidden_extra_yaml_edit_is_refused(self):
        plan=copy.deepcopy(self.plan);doc=asset_yaml.load(self.desired.read_bytes())
        doc['21_FList']['1_this'][0]['21_FUserName']='unreviewed'
        self.desired.write_text(asset_yaml.dump(doc),encoding='utf-8');plan['desired_yaml_sha256']=file_hash(self.desired)
        plan['compile']['source_sha256']=plan['desired_yaml_sha256']
        with self.assertRaisesRegex(FormatError,'reviewed edit'):self.apply(plan)

    def test_destination_traversal_and_library_rename_are_refused(self):
        for path in ['../outside.clb','C:/outside.clb','Libraries/../outside.clb','Other/a.clb']:
            with self.assertRaises(FormatError):asset_editing.relative_clb(path)
        plan=copy.deepcopy(self.plan);plan['relative_path']='Libraries/Different.clb'
        with self.assertRaisesRegex(FormatError,'identity'):self.apply(plan)


if __name__=='__main__':unittest.main()
