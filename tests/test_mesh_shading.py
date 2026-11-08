import base64
import copy
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import meshes,mesh_shading,mesh_imports,native
from aow4mod.core import FormatError
from test_meshes import mesh,encoded


def shaded(mirror=False):
    value=mesh();vertices=value['62_FVertices'];raw=base64.b64decode(vertices['22_FStream']);rows=[]
    for row in struct.iter_unpack('<8f',raw):
        if mirror:row=(*row[:6],1-row[6],row[7])
        rows.append(struct.pack('<8f',*row)+b'\xff'*4+struct.pack('<4f',-1 if mirror else 1,0,0,-1 if mirror else 1))
    vertices['22_FStream']=encoded(b''.join(rows));fmt=vertices['20_FFormat'];fmt['20_FVertexSize']='52';fmt['21_FCount']='5';fmt['23_FList']=encoded(meshes.PNUVCT52)
    return value


class MeshShadingTests(unittest.TestCase):
    def test_native_layout_keeps_projected_attributes_and_checks_ordinary_and_mirrored_uv(self):
        original=meshes.geometry(mesh());after=meshes.geometry(shaded());mirrored=meshes.geometry(shaded(True))
        self.assertEqual(after['oriented_triangle_sha256'],original['oriented_triangle_sha256'])
        for result in (after,mirrored):
            self.assertFalse(result['review_required']);self.assertEqual(result['stride'],52)
            self.assertEqual(result['vertex_profile'],'pnuvct52');self.assertFalse(result['shading']['issues'])

    def test_colors_zero_or_changed_are_reviewed(self):
        for color in (bytes(4),b'\xff\0\0\xff'):
            value=shaded();data=bytearray(base64.b64decode(value['62_FVertices']['22_FStream']));data[32:36]=color
            value['62_FVertices']['22_FStream']=encoded(data)
            self.assertIn('non_white_packed_colors',{x['issue'] for x in meshes.geometry(value)['issues']})

    def test_tangent_zero_normal_parallel_bad_sign_and_opposite_direction_are_rejected(self):
        cases=[((0,0,0,1),'non_unit_tangents'),((0,0,1,1),'non_orthogonal_tangents'),
               ((1,0,0,0),'invalid_tangent_handedness'),((-1,0,0,1),'uv_tangent_direction_conflicts'),
               ((1,0,0,-1),'uv_bitangent_handedness_conflicts')]
        for tangent,issue in cases:
            value=shaded();data=bytearray(base64.b64decode(value['62_FVertices']['22_FStream']));struct.pack_into('<4f',data,36,*tangent)
            value['62_FVertices']['22_FStream']=encoded(data)
            with self.subTest(tangent=tangent):self.assertIn(issue,{x['issue'] for x in meshes.geometry(value)['issues']})

    def test_nonfinite_tangent_is_refused(self):
        value=shaded();data=bytearray(base64.b64decode(value['62_FVertices']['22_FStream']));struct.pack_into('<f',data,36,float('nan'))
        value['62_FVertices']['22_FStream']=encoded(data)
        with self.assertRaisesRegex(FormatError,'non-finite tangent'):meshes.geometry(value)

    def test_degenerate_uv_is_flagged_before_generation_and_in_decoded_output(self):
        value=shaded();data=bytearray(base64.b64decode(value['62_FVertices']['22_FStream']))
        for i in range(3):struct.pack_into('<2f',data,i*52+24,0,0)
        value['62_FVertices']['22_FStream']=encoded(data);projected=b''.join(data[i:i+32] for i in range(0,len(data),52))
        indices=base64.b64decode(value['61_FIndices']['22_FStream'])
        self.assertEqual(mesh_shading.check_uv(projected,indices),1)
        self.assertIn('degenerate_uv_triangles',{x['issue'] for x in meshes.geometry(value)['issues']})

    def test_full_shading_signature_preserves_storage_order_invariance_and_detects_color_tangent_changes(self):
        value=shaded();raw=base64.b64decode(value['62_FVertices']['22_FStream']);indices=struct.pack('<3H',0,1,2)
        signature=meshes.shading_signature(raw,indices)
        reordered=b''.join(raw[i*52:(i+1)*52] for i in (2,0,1))
        self.assertEqual(signature,meshes.shading_signature(reordered,struct.pack('<3H',1,2,0)))
        for offset in (32,36):
            changed=bytearray(raw);changed[offset]^=1
            self.assertNotEqual(signature,meshes.shading_signature(changed,indices))
        self.assertNotEqual(signature,meshes.shading_signature(raw,struct.pack('<3H',0,2,1)))

    def test_unknown_vertex_extensions_remain_not_evaluated(self):
        value=shaded();value['62_FVertices']['20_FFormat']['23_FList']=encoded(meshes.PNUVCT52[:-1]+b'\xff')
        self.assertEqual(meshes.geometry(value)['status'],'not_evaluated')

    def test_profile_binding_and_missing_shaded_witness_are_refused(self):
        payload={'guards_preserved':True,'meshes':[{'artifacts':{}}],'artifacts':[{},{}],'vertex_profile':'pnuvct52'}
        with self.assertRaisesRegex(FormatError,'artifact coverage'):mesh_imports.assess(payload,{})
        with self.assertRaisesRegex(FormatError,'Unknown mesh'):mesh_imports.assess({**payload,'vertex_profile':'anything'}, {})
        with self.assertRaisesRegex(FormatError,'Unsupported imported'):
            mesh_imports.make_plan(None,None,None,None,vertex_profile='anything')
        profile=next(iter(native.PROFILES.values()))
        payload={'sha256':'source','namespace':'TEST','guards_preserved':True,'meshes':[{}],'artifacts':[{},{}],
                 'sdk_sha256':profile['fbx']['sdk_sha256'],'vertex_profile':'pnuvct52'}
        with self.assertRaisesRegex(FormatError,'vertex profile differs'):
            native.validate_payload('mesh-import',payload,{'sha256':'source','namespace':'TEST','vertex_profile':'pnuv32'})


if __name__=='__main__':unittest.main()
