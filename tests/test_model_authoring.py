import base64
import copy
from contextlib import contextmanager
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import model_authoring,meshes
from aow4mod.core import FormatError
from test_mesh_shading import shaded
from test_content_libraries import library_bytes
from aow4mod.filesystem import file_hash,inventory


def documents():
    mesh=shaded();mesh['20_FName']='[TEST]MESH\\0'
    frame={'cid':str(0x41007f),'cname':'TREMeshFrameSet','24_FBoneMatrices':{'cid':str(0x400010),'cname':'TEMatrixList4x4'},
           '25_FOBBoxes':{'cid':str(0x410080),'cname':'TREOBBoxList'}}
    mesh['26_FFrames']=copy.deepcopy(frame);mesh['63_FFrames']=copy.deepcopy(frame)
    for key in ('27_FMaterialLink','68_FMaterialLink','69_FMaskLink'):mesh[key]={'cid':'1','cname':'Empty'}
    source={'YAMLRPKVersion':'1','19_*(int*)&FVersionNumber':'8','20_FName':'TEST',
            '21_FList':{'cid':str(0x400081),'cname':'TEResourceList','1_this':[mesh]},
            '24_FFeatures':{'cid':str(0x4000ab),'cname':'TELibraryFeatureContainer','1_this':[
                {'cid':str(0x412242),'cname':'TREMeshLibraryFeature','10_FUserName':'Default','11_FCategoryID':'0'}]}}
    empty={'cid':'1','cname':'Empty'};frames={'cid':str(0x410084),'cname':'TREModelFrameSet','24_ConvCodeScale':'true'}
    component={'cid':str(0x410067),'cname':'TREModelComponent',
               '31_FMeshLink':{'cid':str(0x410085),'cname':'TREComponentMeshLink','20_FName':'[SYSTEM_CORE]MESH\\6','23_FUserName':'Old'},
               '33_FMaterialLink':{'cid':str(0x4100a6),'cname':'TREComponentMaterialLink','20_FName':model_authoring.TEMPLATES[0],'23_FUserName':'Default'},
               '34_FSettings':{'cid':'1','cname':'Settings','20_FCastShadow':'false'},'35_FShadowMeshLink':copy.deepcopy(empty)}
    model={'cid':str(0x41004b),'cname':'TREModelResource','20_FName':model_authoring.TEMPLATES[1],'32_FScale':'1',
           '33_FFrames':copy.deepcopy(frames),'34_&FOBB':base64.b64encode(bytes(60)).decode(),
           '31_FLODs':{'cid':str(0x412e01),'cname':'TREModelResourceLODList','1_this':[
               {'cid':str(0x412e00),'cname':'TREModelResourceLOD','10_FComponents':{'cid':str(0x410068),'cname':'TREModelComponentList','1_this':[component]},
                '12_FFrames':copy.deepcopy(frames),'14_FMatrixMaps':{'cid':str(0x4100a1),'cname':'TREModelMatrixMapSet',
                    '1_this':[{'cid':str(0x4100a0),'cname':'TREModelMatrixMap'}]}}]}}
    for key in ('36_FDefaultAnimation','38_FCollider','39_FRig','41_FRigModifier'):model[key]=copy.deepcopy(empty)
    material={'cid':str(0x41166f),'cname':'TREEffectMaterialResource','20_FName':model_authoring.TEMPLATES[0],
              '50_FEffectLink':{'cid':'1','cname':'Link','20_FName':'[SYSTEM_EFFECTS]EFFECT\\0'}}
    template=copy.deepcopy(source);template['20_FName']='SYSTEM_EFFECTS';template['21_FList']['1_this']=[material,model]
    return source,template


class ModelAuthoringTests(unittest.TestCase):
    def test_recipe_adds_local_definitions_preserves_source_and_contains_all_vertices(self):
        source,template=documents();before=copy.deepcopy(source);original_template=copy.deepcopy(template)
        result,addition=model_authoring.derive(source,template,'[test]mesh\\0','模型')
        self.assertEqual(source,before);self.assertEqual(template,original_template)
        self.assertEqual(result['21_FList']['1_this'][0],before['21_FList']['1_this'][0])
        self.assertEqual(addition['model'],'[TEST]MODEL\\0');self.assertEqual(addition['material'],'[TEST]MAT\\0')
        self.assertEqual(addition['bounds']['outside_vertex_count'],0)
        self.assertEqual(result['22_FDependencies'],['SYSTEM_EFFECTS.CLB'])
        self.assertEqual(addition['clb_imports']['added'],['SYSTEM_EFFECTS.CLB'])
        model=result['21_FList']['1_this'][2];component=model['31_FLODs']['1_this'][0]['10_FComponents']['1_this'][0]
        self.assertEqual(component['31_FMeshLink']['20_FName'],'[TEST]MESH\\0')
        self.assertEqual(component['33_FMaterialLink']['20_FName'],'[TEST]MAT\\0')
        self.assertEqual(result['21_FList']['1_this'][1]['50_FEffectLink'],template['21_FList']['1_this'][0]['50_FEffectLink'])

    def test_repeated_creation_allocates_new_ids_and_reuses_category_features(self):
        source,template=documents();first,a=model_authoring.derive(source,template,'[TEST]MESH\\0','first')
        second,b=model_authoring.derive(first,template,'[TEST]MESH\\0','second')
        self.assertEqual(b['model'],'[TEST]MODEL\\1');self.assertEqual(b['material'],'[TEST]MAT\\1')
        self.assertEqual(second['21_FList']['1_this'][:3],first['21_FList']['1_this'])
        self.assertEqual(second['24_FFeatures'],first['24_FFeatures'])
        self.assertEqual(second['22_FDependencies'],['SYSTEM_EFFECTS.CLB'])
        self.assertEqual(b['clb_imports']['added'],[])

    def test_wrong_default_effect_and_ambiguous_existing_dependencies_are_refused(self):
        source,template=documents();template['21_FList']['1_this'][0]['50_FEffectLink']['20_FName']='[OTHER]EFFECT\\0'
        with self.assertRaisesRegex(FormatError,'template effect differs'):model_authoring.derive(source,template,'[TEST]MESH\\0','test')
        source,template=documents();source['22_FDependencies']=['SYSTEM_CORE.CLB','System_Core.clb']
        with self.assertRaises(FormatError):model_authoring.derive(source,template,'[TEST]MESH\\0','test')

    def test_unreviewed_vertex_profile_rig_and_prebound_material_are_refused(self):
        for kind in ('format','rig','material'):
            source,template=documents();mesh=source['21_FList']['1_this'][0]
            if kind=='format':mesh['62_FVertices']['20_FFormat']['20_FVertexSize']='48'
            elif kind=='rig':mesh['26_FFrames']['1_this']=[{'cid':'1'}]
            else:mesh['68_FMaterialLink']['20_FName']='[SOMEWHERE]MAT\\0'
            with self.subTest(kind=kind),self.assertRaises(FormatError):model_authoring.derive(source,template,'[TEST]MESH\\0','name')

    def test_nonstatic_template_shapes_are_refused(self):
        for kind in ('scale','rig','frames','shadow','matrix'):
            source,template=documents();model=template['21_FList']['1_this'][1];lod=model['31_FLODs']['1_this'][0]
            if kind=='scale':model['32_FScale']='2'
            elif kind=='rig':model['39_FRig']['20_FName']='[OTHER]RIG\\0'
            elif kind=='frames':model['33_FFrames']['1_this']=[{'cid':'1'}]
            elif kind=='shadow':lod['10_FComponents']['1_this'][0]['35_FShadowMeshLink']['20_FName']='[OTHER]MESH\\0'
            else:lod['14_FMatrixMaps']['1_this'][0]['10_Value']='unreviewed'
            with self.subTest(kind=kind),self.assertRaises(FormatError):model_authoring.derive(source,template,'[TEST]MESH\\0','name')

    def test_float32_bounds_round_outwards_at_large_coordinates(self):
        low=[struct.unpack('<f',struct.pack('<f',x))[0] for x in (100000.1,-200000.2,.1)]
        high=[struct.unpack('<f',struct.pack('<f',x))[0] for x in (100002.3,-199995.7,.2)]
        box=model_authoring.bounds(low,high);data=b''.join(struct.pack('<8f',*p,0,0,1,0,0) for p in (low,high))
        checked=meshes.stored_bounds(box,data);self.assertEqual(checked['outside_vertex_count'],0);self.assertTrue(checked['orthonormal'])

    def test_path_escape_and_invalid_names_are_refused(self):
        for path in ('../outside.clb','C:/outside.clb','/outside.clb','Libraries/../../x.clb','Libraries/test.txt'):
            with self.subTest(path=path),self.assertRaises(FormatError):model_authoring.relative_path(path)
        source,template=documents()
        for name in ('','  ','bad\0name','x'*129):
            with self.subTest(name=name),self.assertRaises(FormatError):model_authoring.derive(source,template,'[TEST]MESH\\0',name)


class ModelStageTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.mod=self.root/'mod';(self.mod/'Libraries').mkdir(parents=True);self.source=self.mod/'mod.acp';self.source.write_bytes(b'fixture')
        (self.mod/'Libraries/FIXTURE.clb').write_bytes(b'original library')
        self.compiled=self.root/'compiled';self.compiled.mkdir();self.binary=self.compiled/'FIXTURE.clb';self.binary.write_bytes(library_bytes())
        self.original=self.binary.read_bytes();self.game=self.root/'game';self.game.mkdir();self.output=self.root/'stage'
        self.plan={'source':str(self.source),'game':str(self.game),'relative_path':'Libraries/FIXTURE.clb',
                   'source_files':inventory(self.mod),'compiled_file':str(self.binary),'compiled_sha256':file_hash(self.binary)}
        self.addition={'model':'[FIXTURE]MODEL\\0','material':'[FIXTURE]MAT\\0','mesh':'[FIXTURE]MESH\\0','name':'test'}
        mocked=patch.object(model_authoring,'validate',return_value=self.addition);mocked.start();self.addCleanup(mocked.stop)

    def test_staging_preserves_input_and_writes_draft_provenance(self):
        before=inventory(self.mod);receipt=model_authoring.apply_plan(self.plan,self.output)
        self.assertEqual(before,inventory(self.mod));self.assertTrue(receipt['draft'])
        self.assertFalse(receipt['shader_variant_verified']);self.assertTrue((self.output/'SourceModels/FIXTURE/.aow4mod-model.json').is_file())

    def test_changed_candidate_during_copy_is_rejected_against_reviewed_digest(self):
        original=model_authoring.copy_directory
        @contextmanager
        def race(*args):
            with original(*args) as value:
                self.binary.write_bytes(b'changed candidate');yield value
        with patch.object(model_authoring,'copy_directory',race),self.assertRaisesRegex(FormatError,'candidate changed'):
            model_authoring.apply_plan(self.plan,self.output)
        self.assertFalse(self.output.exists());self.assertEqual((self.mod/'Libraries/FIXTURE.clb').read_bytes(),b'original library')

    def test_source_change_between_validation_and_copy_is_rejected(self):
        original=model_authoring.copy_directory
        @contextmanager
        def race(*args):
            (self.mod/'later.txt').write_bytes(b'user change')
            with original(*args) as value:yield value
        with patch.object(model_authoring,'copy_directory',race),self.assertRaisesRegex(FormatError,'source changed before staging'):
            model_authoring.apply_plan(self.plan,self.output)
        self.assertFalse(self.output.exists())


if __name__=='__main__':unittest.main()
