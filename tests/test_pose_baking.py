"""Independent direction conventions and release policy boundaries."""
import copy
import base64
import struct
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import pose_baking,skin_meshes,shader_reference,publication,meshes
from aow4mod.core import FormatError

I=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]


class PoseBakeTests(unittest.TestCase):
    def test_directions_ignore_translation_and_use_direct_not_inverse_transpose(self):
        inverse=list(I);inverse[0]=2;inverse[5]=3;inverse[12:15]=[100,200,300]
        bone=list(I);bone[0:3]=[0,1,0];bone[4:7]=[-1,0,0];bone[12:15]=[-30,-40,-50]
        vertex={'position':[1,1,0],'indices':[0],'weights':[1]}
        self.assertEqual(skin_meshes.transform([1,1,0],0,vertex,[0],[bone],[inverse]),[-3,2,0])
        self.assertEqual(skin_meshes.position(vertex,[0],[bone],[inverse]),[-233,62,250])

    def test_independent_normalization_preserves_direction_and_rejects_collapse(self):
        self.assertEqual(pose_baking.unit([0,0,-4]),[0,0,-1])
        for values in ([0,0,0],[float('nan'),0,1],[float('inf'),0,1]):
            with self.assertRaises(FormatError):pose_baking.unit(values)

    def test_direction_prefix_does_not_read_projective_fourth_components(self):
        inverse=list(I);inverse[3]=10;bone=list(I);bone[12]=100
        v={'position':[1,0,0],'indices':[0],'weights':[1]}
        self.assertEqual(skin_meshes.transform([1,0,0],0,v,[0],[bone],[inverse]),[1,0,0])

    def tangent_mesh(self,degenerate=False):
        positions=[(0,0,0),(1,0,0),(0,1,0),(1,1,0)]
        raw=b''.join(struct.pack('<8f',*p,0,0,1,*(p[:2] if not degenerate else (0,0)))+b'\xff'*4+struct.pack('<4f',0,-1,0,-1) for p in positions)
        return {'62_FVertices':{'21_FCount':'4','22_FStream':base64.b64encode(raw).decode()},'61_FIndices':{'21_FCount':'6','22_FStream':base64.b64encode(struct.pack('<6H',0,1,2,2,1,3)).decode()}}

    def test_uv_rebuild_preserves_oriented_geometry_and_repairs_tangent_sign(self):
        m=self.tangent_mesh();old=copy.deepcopy(m);pose_baking.rebuild_tangents(m)
        old_raw=base64.b64decode(old['62_FVertices']['22_FStream']);raw=base64.b64decode(m['62_FVertices']['22_FStream']);indices=base64.b64decode(m['61_FIndices']['22_FStream'])
        project=lambda v:b''.join(v[i:i+32] for i in range(0,len(v),52))
        self.assertEqual(meshes.triangle_signature(project(old_raw),base64.b64decode(old['61_FIndices']['22_FStream'])),meshes.triangle_signature(project(raw),indices))
        self.assertTrue(all(struct.unpack_from('<4f',raw,i+36)==(1,0,0,1) for i in range(0,len(raw),52)))

    def test_uv_rebuild_rejects_indeterminate_tangents(self):
        with self.assertRaises(FormatError):pose_baking.rebuild_tangents(self.tangent_mesh(True))

    def test_shader_direction_inputs_do_not_alias_position_or_translation(self):
        text='''%1 = call float @dx.op.loadInput.f32(i32 4, i32 1, i32 0, i8 0, i32 undef)
%2 = call float @dx.op.loadInput.f32(i32 4, i32 4, i32 0, i8 1, i32 undef)
%243 = fadd fast float %1, 0.000000e+00
%244 = fadd fast float %1, 0.000000e+00
%245 = fadd fast float %1, 0.000000e+00
%312 = fmul fast float %1, 2.000000e+00
%313 = fmul fast float %1, 3.000000e+00
%314 = fmul fast float %1, 4.000000e+00
%381 = fmul fast float %2, 5.000000e+00
%382 = fmul fast float %2, 6.000000e+00
%383 = fmul fast float %2, 7.000000e+00'''
        value=shader_reference.Evaluator(text,3,checked=False)
        self.assertEqual(value.directions([2,0,0],[0,3,0,-1],[0,0,0],[1,0],[0],[I],[I]),([4,6,8],[15,18,21]))
        with self.assertRaises(FormatError):shader_reference.Evaluator(text,3)

    def test_pose_provenance_policy_and_static_variant_gate_cannot_disappear(self):
        record={'source_files':{'SourcePoseBakes/LOCAL/model-1.json':{'sha256':'s'}},
            'draft_pose_bake_assets':[{'file':'Libraries/LOCAL.clb','model':'[LOCAL]MODEL\\1','source_receipt':'SourcePoseBakes/LOCAL/model-1.json','source_receipt_sha256':'s'}],
            'release_gate':{'review_required':True,'issues':[{'issue':'pose_bake_requires_static_variant_review','file':'Libraries/LOCAL.clb'}]}}
        publication.check_pose_bake_drafts(record,{'Libraries/LOCAL.clb':'a'})
        for key in ('draft_pose_bake_assets','release_gate'):
            changed=copy.deepcopy(record);changed.pop(key)
            with self.assertRaises(FormatError):publication.check_pose_bake_drafts(changed,{'Libraries/LOCAL.clb':'a'})
        changed=copy.deepcopy(record);changed['draft_pose_bake_assets'][0]['source_receipt_sha256']='changed'
        with self.assertRaises(FormatError):publication.check_pose_bake_drafts(changed,{'Libraries/LOCAL.clb':'a'})


if __name__=='__main__':unittest.main()
