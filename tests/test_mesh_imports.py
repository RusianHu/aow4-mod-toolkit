import base64
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import mesh_imports,meshes,fbx,native,asset_yaml,native_objects
from aow4mod.core import FormatError,Node
from aow4mod.filesystem import file_hash,inventory
from test_aow4_mod_audit import acp,resource,mapping,string,u32
from test_content_libraries import library_bytes
from test_meshes import mesh,encoded
from test_fbx import source_bytes


def object_body(value):
    v=value['62_FVertices'];f=v['20_FFormat'];i=value['61_FIndices']
    return mapping({0x14:string(value['20_FName']),0x15:string(value['21_FUserName']),0x3c:u32(3),
        0x3d:mapping({0x14:b'\1',0x15:u32(3),0x16:base64.b64decode(i['22_FStream'])}),
        0x3e:mapping({0x14:mapping({0x14:u32(32),0x15:u32(3),0x16:u32(1),0x17:meshes.PNUV32}),
                     0x15:u32(3),0x16:base64.b64decode(v['22_FStream'])}),
        0x40:base64.b64decode(value['64_&FVerticesOBB']),0x42:base64.b64decode(value['66_&FOBB'])})


@unittest.skipUnless(__import__('importlib').util.find_spec('yaml'),'optional assets dependency')
class MeshImportTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.mod=self.root/'mod';self.mod.mkdir();(self.mod/'mod.acp').write_bytes(acp(resource()))
        (self.root/'inputs').mkdir();self.source=self.root/'inputs/source.fbx';self.source.write_bytes(source_bytes())
        self.game=self.root/'game';self.game.mkdir();(self.game/'libfbxsdk.dll').write_bytes(b'fixture sdk')
        self.binding={'game':str(self.game),'dll_sha256':'fixture','backend_sha256':'fixture',
                      'profile':{'name':'fixture','fbx':{'sdk_sha256':file_hash(self.game/'libfbxsdk.dll')}}}
        self.public=native.public_binding(self.binding)
        for name,kwargs in [('native.profile_for',{'return_value':self.binding}),
                            ('native.run_worker',{'side_effect':self.worker}),
                            ('native_assets.export_library',{'side_effect':self.export})]:
            mock=patch('aow4mod.mesh_imports.'+name,**kwargs);mock.start();self.addCleanup(mock.stop)

    def worker(self,game,operation,params,cache,timeout):
        folder=self.root/'worker';folder.mkdir();value=mesh();value.update({'20_FName':'[FIXTURE]MESH\\0','21_FUserName':'测试 member'})
        before=copy.deepcopy(value);before['64_&FVerticesOBB']=before['66_&FOBB']=encoded(bytes(60))
        value['64_&FVerticesOBB']=value['66_&FOBB']=encoded(struct.pack('<15f',1,0,0,0,1,0,0,0,1,.5,.5,0,.5,.5,0))
        artifacts={}
        for kind,data in [('before',object_body(before)),('finalized',object_body(value)),('readback',object_body(value))]:
            path=folder/(kind+'.bin');path.write_bytes(data);artifacts[kind]={'file':str(path),'sha256':file_hash(path)}
        geometry=meshes.geometry(meshes.native_member(object_body(value),0x410035))
        self.document={'YAMLRPKVersion':'1','19_*(int*)&FVersionNumber':'8','20_FName':'FIXTURE',
                       '21_FList':{'cid':str(0x400081),'cname':'TEResourceList','1_this':[value]}}
        candidate=folder/'FIXTURE.clb';candidate.write_bytes(library_bytes());yaml=folder/'FIXTURE.clb.yaml';yaml.write_text(asset_yaml.dump(self.document), encoding='utf-8')
        payload={'namespace':'FIXTURE','sha256':params['sha256'],'sdk_sha256':file_hash(self.game/'libfbxsdk.dll'),'guards_preserved':True,
            'meshes':[{'geometry':geometry,'source_geometry':meshes.geometry(meshes.native_member(object_body(before),0x410035)),
                       'readback':native_objects.compare_bodies(object_body(value),object_body(value)),'artifacts':artifacts}],
            'artifacts':[{'file':str(path),'sha256':file_hash(path)} for path in (candidate,yaml)]}
        self.payload=payload
        return {'payload':payload,'binding':self.binding}

    def export(self,candidate,game,output,cache,timeout):
        output=Path(output);output.mkdir();(output/'FIXTURE.clb').write_bytes(Path(candidate).read_bytes())
        (output/'FIXTURE.clb.yaml').write_text(asset_yaml.dump(self.document), encoding='utf-8')
        return {'binding':self.public,'output':str(output),'files':[{'name':'FIXTURE.clb'},{'name':'FIXTURE.clb.yaml'}]}

    def plan(self):return mesh_imports.make_plan(self.mod,self.source,'FIXTURE',self.game,self.root/'cache')

    def test_plan_apply_retains_source_and_marks_material_model_binding_as_draft(self):
        before=inventory(self.mod);plan=self.plan();result=mesh_imports.apply_plan(plan,self.root/'stage')
        self.assertTrue(result['draft']);self.assertFalse(result['assessment']['material_binding_verified'])
        self.assertEqual((self.root/'stage/SourceMeshes/FIXTURE/source.fbx').read_bytes(),self.source.read_bytes())
        self.assertTrue((self.root/'stage/SourceMeshes/FIXTURE/.aow4mod-mesh.json').is_file())
        self.assertEqual(inventory(self.mod),before)

    def test_changed_source_mod_and_forged_target_are_rejected(self):
        plan=self.plan();changed=copy.deepcopy(plan);changed['target']='../outside.clb'
        with self.assertRaises(FormatError):mesh_imports.apply_plan(changed,self.root/'bad')
        self.source.write_bytes(source_bytes()+b'changed')
        with self.assertRaisesRegex(FormatError,'source or Mod changed'):mesh_imports.apply_plan(plan,self.root/'stale')
        self.assertFalse((self.root/'bad').exists());self.assertFalse((self.root/'stale').exists())

    def test_unrequested_fields_cannot_be_hidden_by_refreshed_artifact_hashes(self):
        plan=self.plan();row=plan['native_payload']['meshes'][0];path=Path(row['artifacts']['finalized']['file']);data=path.read_bytes()
        from aow4mod import editing
        path.write_bytes(editing.rewrite_map(Node(data,0,len(data)),{0x4d:string('unexpected material')}))
        row['artifacts']['finalized']['sha256']=file_hash(path)
        with self.assertRaisesRegex(FormatError,'unrequested'):mesh_imports.apply_plan(plan,self.root/'tampered')
        self.assertFalse((self.root/'tampered').exists())

    def test_existing_library_or_source_directory_is_not_overwritten(self):
        (self.mod/'SourceMeshes/FIXTURE').mkdir(parents=True)
        with self.assertRaisesRegex(FormatError,'source directory already exists'):self.plan()
        (self.mod/'Libraries').mkdir();(self.mod/'Libraries/FIXTURE.clb').write_bytes(library_bytes())
        with self.assertRaisesRegex(FormatError,'overwrite'):self.plan()

    def test_unsupported_source_is_refused_before_native_execution(self):
        self.source.write_bytes(b'FBX ascii unsupported')
        with patch('aow4mod.mesh_imports.native.run_worker') as worker:
            with self.assertRaises(FormatError):self.plan()
            worker.assert_not_called()

    def test_unreviewed_sdk_is_refused_before_native_initialization(self):
        (self.game/'libfbxsdk.dll').write_bytes(b'unreviewed sdk')
        with patch('aow4mod.mesh_imports.native.run_worker') as worker:
            with self.assertRaisesRegex(FormatError,'before initialization'):self.plan()
            worker.assert_not_called()

    def test_zero_output_and_failed_guards_cannot_be_applied(self):
        plan=self.plan()
        for change in ({'guards_preserved':False},{'meshes':[]}):
            modified=copy.deepcopy(plan);modified['native_payload'].update(change)
            with self.assertRaisesRegex(FormatError,'coverage'):mesh_imports.apply_plan(modified,self.root/'bad-output')
        self.assertFalse((self.root/'bad-output').exists())

    def test_candidate_changes_during_copy_cannot_replace_the_reviewed_asset(self):
        plan=self.plan();candidate=Path(plan['native_payload']['artifacts'][0]['file'])
        # Change only the final copy, after assessment has verified evidence.
        from contextlib import contextmanager
        actual=mesh_imports.copy_directory
        @contextmanager
        def copying(*args,**kwargs):
            with actual(*args,**kwargs) as values:
                candidate.write_bytes(library_bytes(value=99))
                yield values
        with patch('aow4mod.mesh_imports.copy_directory',copying):
            with self.assertRaisesRegex(FormatError,'copy differs'):mesh_imports.apply_plan(plan,self.root/'raced')
        self.assertFalse((self.root/'raced').exists())


if __name__=='__main__':unittest.main()
