from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import fbx
from aow4mod.core import FormatError


def prop(kind,value,compressed=False):
    if kind=='S':raw=value.encode();return b'S'+struct.pack('<I',len(raw))+raw
    if kind=='L':return b'L'+struct.pack('<q',value)
    if kind in ('d','i'):
        raw=struct.pack('<'+str(len(value))+kind,*value);packed=zlib.compress(raw) if compressed else raw
        return kind.encode()+struct.pack('<III',len(value),int(compressed),len(packed))+packed
    raise AssertionError(kind)


def source_bytes(version=7400,extra=(),indices=(0,1,-3),compressed=False):
    wide=version>=7500;header=25 if wide else 13
    objects=[('Geometry',[prop('L',1),prop('S','Triangle'),prop('S','Mesh')],[
        ('Vertices',[prop('d',(0,0,0,1,0,0,0,1,0),compressed)],[]),
        ('PolygonVertexIndex',[prop('i',indices,compressed)],[])]),
        ('Model',[prop('L',2),prop('S','Triangle'),prop('S','Mesh')],[]),
        ('Material',[prop('L',3),prop('S','Material'),prop('S','')],[])]+list(extra)
    def node(value,start):
        name,props,children=value;name=name.encode();payload=b''.join(props);cursor=start+header+len(name)+len(payload);body=b''
        for child in children:
            encoded=node(child,cursor);body+=encoded;cursor+=len(encoded)
        if children:body+=bytes(header);cursor+=header
        return struct.pack('<QQQ' if wide else '<III',cursor,len(props),len(payload))+bytes([len(name)])+name+payload+body
    return fbx.MAGIC+struct.pack('<I',version)+node(('Objects',[],objects),27)+bytes(header)


class FbxTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name).resolve()/'mesh.fbx'

    def read(self,data):self.path.write_bytes(data);return fbx.inspect(self.path)

    def test_binary_versions_and_bounded_compressed_arrays(self):
        for version in (7400,7500):
            for compressed in (False,True):
                result=self.read(source_bytes(version,compressed=compressed))
                self.assertEqual(result['geometries'],[{'control_points':3,'expected_triangles_before_instancing':1}])
                self.assertEqual(result['materials'],1)

    def test_media_skins_animation_curves_and_unknown_objects_are_refused(self):
        for name in ('Texture','Video','Deformer','AnimationCurve','AnimationCurveNode','Camera'):
            extra=[(name,[prop('L',10),prop('S','Unsupported'),prop('S','')],[])]
            with self.subTest(name=name),self.assertRaisesRegex(FormatError,'unsupported static-scope'):
                self.read(source_bytes(extra=extra))

    def test_empty_animation_containers_are_allowed(self):
        extra=[(name,[prop('L',index),prop('S','Empty'),prop('S','')],[]) for index,name in ((4,'AnimationStack'),(5,'AnimationLayer'))]
        self.assertEqual(self.read(source_bytes(extra=extra))['models'],1)

    def test_invalid_geometry_and_duplicate_ids_are_refused(self):
        for indices in ((0,1,-5),(0,1,2),(0,-2),()):
            with self.subTest(indices=indices),self.assertRaises(FormatError):self.read(source_bytes(indices=indices))
        extra=[('Material',[prop('L',3),prop('S','Duplicate'),prop('S','')],[])]
        with self.assertRaisesRegex(FormatError,'duplicate'):self.read(source_bytes(extra=extra))

    def test_source_truncation_node_bounds_header_and_array_lengths_are_refused(self):
        raw=source_bytes()
        damaged=bytearray(raw);struct.pack_into('<I',damaged,27,len(raw)+100)
        for data in (raw[:24],raw[:-20],bytes(damaged),b'not binary',source_bytes(7300)):
            with self.subTest(size=len(data)),self.assertRaises(FormatError):self.read(data)
        damaged=bytearray(source_bytes(compressed=True));index=damaged.index(b'\x78\x9c');damaged[index]=0
        with self.assertRaisesRegex(FormatError,'compressed array'):self.read(damaged)


if __name__=='__main__':unittest.main()
