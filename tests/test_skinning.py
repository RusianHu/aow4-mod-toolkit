import base64
import copy
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import skin_meshes,skin_weights,shader_reference,publication
from aow4mod.core import FormatError

I=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]


def fixture(influences=3):
    encode=lambda raw:base64.b64encode(raw).decode();stride=64 if influences==3 else 68
    decl=struct.pack('<IHHIHHIHHIHHIHHIHHIHH',0x10,3,0,0x40,3,12,0x30,2,24,0x20,0xc4,32,0x70,4,36,0x80,0xa4,52,0x90,influences-1,56)
    raw=bytearray(stride*2)
    for i in range(2):
        struct.pack_into('<3f',raw,i*stride,1,2,3);raw[i*stride+52:i*stride+56]=bytes([0,1,2,255 if influences==3 else 3])
        struct.pack_into('<'+str(influences-1)+'f',raw,i*stride+56,*([0.25,0.5] if influences==3 else [0.25]*3))
    frames=bytearray(160*4)
    for i in range(4):
        name=('Bone'+str(i)).encode();frames[i*160:i*160+len(name)]=name;struct.pack_into('<i',frames,i*160+136,i)
    mesh={'cid':str(0x410035),'cname':'TREMeshResourceDEV','20_FName':'[LOCAL]MESH\\0','60_Version':'3',
        '26_FFrames':{'21_FCount':'4','22_Frames':encode(frames),'24_FBoneMatrices':{'21_FCount':'4','22_FList':encode(struct.pack('<16f',*I)*4)}},
        '62_FVertices':{'21_FCount':'2','22_FStream':encode(raw),'20_FFormat':{'20_FVertexSize':str(stride),'21_FCount':'7','22_&FAlignmentMode':'1','23_FList':encode(decl)}}}
    return {'20_FName':'LOCAL','21_FList':{'1_this':[mesh]}}


class SkinningTests(unittest.TestCase):
    def test_three_and_four_influences_and_unused_padding(self):
        for count in (3,4):
            mesh=skin_meshes.decode(skin_meshes.yaml_body(fixture(count)['21_FList']['1_this'][0]))
            self.assertEqual(mesh['influences'],count);self.assertEqual(len(mesh['vertices'][0]['indices']),count)
            self.assertAlmostEqual(sum(mesh['vertices'][0]['weights']),1)

    def test_noncommuting_palette_and_mixed_weights_have_known_result(self):
        bones=[list(I) for _ in range(3)];bones[0][12]=1;bones[1][13]=2;bones[2][14]=4
        vertex={'position':[1,2,3],'indices':[0,1,2],'weights':[0.25,0.5,0.25]}
        self.assertEqual(skin_meshes.position(vertex,[2,0,1],bones,[I]*3),[1.5,2.5,4])

    def test_invalid_weight_sums_and_shader_read_indices_are_refused(self):
        document=fixture();mesh=document['21_FList']['1_this'][0]
        for mode in ('sum','index','nan'):
            changed=copy.deepcopy(mesh);raw=bytearray(base64.b64decode(changed['62_FVertices']['22_FStream']))
            if mode=='sum':struct.pack_into('<2f',raw,56,0.8,0.8)
            elif mode=='nan':struct.pack_into('<f',raw,56,float('nan'))
            else:raw[53]=255
            changed['62_FVertices']['22_FStream']=base64.b64encode(raw).decode()
            with self.assertRaises(FormatError):skin_meshes.decode(skin_meshes.yaml_body(changed))

    def test_typed_weight_edit_preserves_every_other_byte_and_vertex(self):
        document=fixture();before=copy.deepcopy(document)
        after,change=skin_weights.derive(document,'[LOCAL]MESH\\0',[{'vertex':0,'bones':['Bone2','Bone1','Bone0'],'weights':[0.5,0.25,0.25]}])
        old=base64.b64decode(document['21_FList']['1_this'][0]['62_FVertices']['22_FStream']);new=base64.b64decode(after['21_FList']['1_this'][0]['62_FVertices']['22_FStream'])
        self.assertEqual(document,before);self.assertEqual(old[:52],new[:52]);self.assertEqual(old[55:56],new[55:56]);self.assertEqual(old[64:],new[64:])
        self.assertEqual(change['changes'][0]['effective_weights'],[0.5,0.25,0.25])

    def test_unknown_bones_duplicates_noops_and_bad_residual_are_refused(self):
        for edits in [
            [{'vertex':0,'bones':['Missing','Bone1','Bone2'],'weights':[0.25,0.5,0.25]}],
            [{'vertex':0,'bones':['Bone0','Bone0','Bone2'],'weights':[0.25,0.5,0.25]}],
            [{'vertex':0,'bones':['Bone0','Bone1','Bone2'],'weights':[0.25,0.5,0.25]}],
            [{'vertex':0,'bones':['Bone0','Bone1','Bone2'],'weights':[0.25,0.5,0.5]}]]:
            with self.assertRaises(FormatError):skin_weights.derive(fixture(),'[LOCAL]MESH\\0',edits)

    def test_scalar_interpreter_is_bounded_and_rejects_unreviewed_text(self):
        text='''%1 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)
%243 = fadd fast float %1, 1.000000e+00
%244 = fmul fast float %1, 2.000000e+00
%245 = fsub fast float %244, %243'''
        with self.assertRaises(FormatError):shader_reference.Evaluator(text,3)
        evaluator=shader_reference.Evaluator(text,3,checked=False)
        self.assertEqual(evaluator.position([3,0,0],[0,0,0],[1,0],[0],[I],[I]),[4,6,2])
        with self.assertRaises(FormatError):shader_reference.Evaluator(text.replace('fadd fast','fdiv fast'),3,checked=False).position([3,0,0],[0,0,0],[1,0],[0],[I],[I])

    def test_source_receipt_and_runtime_variant_gate_are_required(self):
        manifest={'source_files':{'SourceSkinWeights/LOCAL/mesh-0.json':{'sha256':'s'}},
            'draft_skin_weight_assets':[{'file':'Libraries/LOCAL.clb','mesh':'[LOCAL]MESH\\0','source_receipt':'SourceSkinWeights/LOCAL/mesh-0.json','source_receipt_sha256':'s'}],
            'release_gate':{'review_required':True,'issues':[{'issue':'skin_weights_require_runtime_variant_review','file':'Libraries/LOCAL.clb'}]}}
        expected={'Libraries/LOCAL.clb':'a'};publication.check_skin_weight_drafts(manifest,expected)
        for key in ('draft_skin_weight_assets','release_gate'):
            changed=copy.deepcopy(manifest);changed.pop(key)
            with self.assertRaises(FormatError):publication.check_skin_weight_drafts(changed,expected)


if __name__=='__main__':unittest.main()
