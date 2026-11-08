import base64
import copy
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import meshes,asset_yaml
from aow4mod.core import FormatError


def encoded(value):return base64.b64encode(value).decode('ascii')


def mesh():
    vertices=b''.join(struct.pack('<8f',*row) for row in (
        (0,0,0,0,0,1,0,0),(1,0,0,0,0,1,1,0),(0,1,0,0,0,1,0,1)))
    return {'cid':str(0x410035),'cname':'TREMeshResourceDEV','20_FName':'[TEST]MESH\\0','21_FUserName':'Triangle',
        '60_Version':'3','61_FIndices':{'cid':str(0x41000e),'cname':'TREIndexList','20_(char&)FFormat':'1',
            '21_FCount':'3','22_FStream':encoded(struct.pack('<3H',0,1,2))},
        '62_FVertices':{'cid':str(0x41000c),'cname':'TREVertexList','21_FCount':'3','22_FStream':encoded(vertices),
            '20_FFormat':{'cid':str(0x41343e),'cname':'TREVertexFormat','20_FVertexSize':'32','21_FCount':'3',
                '22_&FAlignmentMode':'1','23_FList':encoded(meshes.PNUV32)}}}


class MeshTests(unittest.TestCase):
    def test_triangle_signature_preserves_attributes_and_winding_but_ignores_storage_order(self):
        before=mesh();raw=base64.b64decode(before['62_FVertices']['22_FStream']);after=copy.deepcopy(before)
        after['62_FVertices']['22_FStream']=encoded(b''.join(raw[n*32:(n+1)*32] for n in (2,0,1)))
        after['61_FIndices']['22_FStream']=encoded(struct.pack('<3H',1,2,0))
        signature=meshes.geometry(before)['oriented_triangle_sha256']
        self.assertEqual(meshes.geometry(after)['oriented_triangle_sha256'],signature)
        after['61_FIndices']['22_FStream']=encoded(struct.pack('<3H',1,0,2))
        self.assertNotEqual(meshes.geometry(after)['oriented_triangle_sha256'],signature)
        after=copy.deepcopy(before);changed=bytearray(raw);struct.pack_into('<f',changed,24,.25)
        after['62_FVertices']['22_FStream']=encoded(changed)
        self.assertNotEqual(meshes.geometry(after)['oriented_triangle_sha256'],signature)

    def test_stored_oriented_bounds_contain_a_translated_rotated_shape(self):
        data=mesh();q=2**-.5;vertices=[]
        for x,y,z,nx,ny,nz,u,v in struct.iter_unpack('<8f',base64.b64decode(data['62_FVertices']['22_FStream'])):
            vertices.append(struct.pack('<8f',(x-.5)*q-(y-.5)*q+3,(x-.5)*q+(y-.5)*q+4,z+5,nx,ny,nz,u,v))
        data['62_FVertices']['22_FStream']=encoded(b''.join(vertices))
        box=struct.pack('<15f',q,q,0,-q,q,0,0,0,1,.5,.5,0,3,4,5)
        data['64_&FVerticesOBB']=encoded(box);result=meshes.geometry(data)
        self.assertFalse(result['review_required']);self.assertTrue(result['stored_bounds']['64_&FVerticesOBB']['orthonormal'])
        self.assertEqual(result['stored_bounds']['64_&FVerticesOBB']['outside_vertex_count'],0)
        data['64_&FVerticesOBB']=encoded(struct.pack('<15f',q,q,0,-q,q,0,0,0,1,.5,.5,0,30,4,5))
        self.assertIn('vertices_outside_stored_vertex_bounds',{r['issue'] for r in meshes.geometry(data)['issues']})

    def test_invalid_bounds_and_incomplete_native_body_are_refused(self):
        for extent in (-1,float('nan')):
            data=mesh();data['64_&FVerticesOBB']=encoded(struct.pack('<15f',1,0,0,0,1,0,0,0,1,extent,1,1,0,0,0))
            with self.assertRaises(FormatError):meshes.geometry(data)
        from test_aow4_mod_audit import mapping
        with self.assertRaisesRegex(FormatError,'required fields'):meshes.native_member(mapping({}),0x410035)
        with self.assertRaisesRegex(FormatError,'requires MeshDEV'):meshes.native_member(mapping({}),0x40009d)

    def test_geometry_bounds_topology_and_stream_hashes(self):
        result=meshes.geometry(mesh())
        self.assertEqual(result['status'],'geometry_decoded');self.assertFalse(result['review_required'])
        self.assertEqual(result['triangle_count'],1);self.assertEqual(result['vertex_count'],3)
        self.assertEqual(result['unique_positions'],3);self.assertEqual(result['computed_bounds'],{'minimum':[0,0,0],'maximum':[1,1,0]})
        self.assertFalse(result['rendering_verified']);self.assertIn('not applied',result['coordinate_space'])

    def test_unknown_layout_or_class_stays_not_evaluated(self):
        for transform in (lambda m:m.update(cid=str(0x41006c)),
                          lambda m:m['62_FVertices']['20_FFormat'].update({'20_FVertexSize':'64'}),
                          lambda m:m['61_FIndices'].update({'20_(char&)FFormat':'2'})):
            value=mesh();transform(value);result=meshes.geometry(value)
            self.assertEqual(result['status'],'not_evaluated');self.assertTrue(result['review_required'])
            self.assertNotIn('triangle_count',result)

    def test_wrong_lengths_bad_base64_and_count_bounds_are_refused(self):
        for key,value in [('22_FStream','!not base64'),('22_FStream',encoded(b'x')),
                          ('21_FCount','1000001'),('21_FCount','١'),('21_FCount','-1')]:
            data=mesh();data['62_FVertices'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(FormatError):meshes.geometry(data)

    def test_empty_and_incomplete_triangles_cannot_be_successful_geometry(self):
        for count in ('0','1','2','4'):
            data=mesh();data['61_FIndices']['21_FCount']=count
            with self.subTest(count=count),self.assertRaises(FormatError):meshes.geometry(data)

    def test_out_of_range_indices_and_nonfinite_attributes_are_refused(self):
        data=mesh();data['61_FIndices']['22_FStream']=encoded(struct.pack('<3H',0,1,3))
        with self.assertRaisesRegex(FormatError,'outside'):meshes.geometry(data)
        for offset,value in ((0,float('nan')),(12,float('inf')),(24,float('-inf'))):
            data=mesh();raw=bytearray(base64.b64decode(data['62_FVertices']['22_FStream']))
            struct.pack_into('<f',raw,offset,value);data['62_FVertices']['22_FStream']=encoded(raw)
            with self.assertRaisesRegex(FormatError,'non-finite'):meshes.geometry(data)

    def test_degenerate_triangles_normals_and_zero_bounds_remain_findings(self):
        data=mesh();data['61_FIndices']['22_FStream']=encoded(struct.pack('<3H',0,1,1))
        raw=bytearray(base64.b64decode(data['62_FVertices']['22_FStream']));struct.pack_into('<3f',raw,12,0,0,0)
        data['62_FVertices']['22_FStream']=encoded(raw);data['64_&FVerticesOBB']=encoded(bytes(60))
        result=meshes.geometry(data);self.assertTrue(result['review_required'])
        self.assertEqual({row['issue'] for row in result['issues']},{'degenerate_triangles','non_unit_vertex_normals','zero_stored_bounds_for_nonzero_geometry'})

    @unittest.skipUnless(importlib.util.find_spec('yaml'),'optional YAML dependency')
    def test_exported_library_selection_is_offline_and_preserves_source(self):
        document={'YAMLRPKVersion':'1','19_*(int*)&FVersionNumber':'8','20_FName':'TEST',
                  '21_FList':{'cid':str(0x400081),'cname':'TEResourceList','1_this':[mesh()]}}
        with tempfile.TemporaryDirectory() as name:
            path=Path(name).resolve()/'library.yaml';path.write_text(asset_yaml.dump(document), encoding='utf-8');before=path.read_bytes()
            with patch('aow4mod.native.run_worker',side_effect=AssertionError('offline inspection must not start the DLL')):
                result=meshes.inspect(path,'[test]mesh\\0')
            self.assertEqual(result['vertex_count'],3);self.assertEqual(path.read_bytes(),before)
            with self.assertRaisesRegex(FormatError,'exactly one'):meshes.inspect(path,'[TEST]MESH\\1')


if __name__=='__main__':unittest.main()
