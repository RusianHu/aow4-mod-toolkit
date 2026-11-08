"""Source semantics, independent binary binding and hierarchy defect controls."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import fbx,source_correspondence as corr,skin_meshes,meshes,editing,content_libraries
from aow4mod.core import Node,FormatError
from test_skeletal_imports import source_fixture
from test_aow4_mod_audit import mapping,typed,array,u32

I=[1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.]


def encode(roots):
    def prop(v):
        if isinstance(v,str):b=v.encode();return b'S'+struct.pack('<I',len(b))+b
        if isinstance(v,int):return b'L'+struct.pack('<q',v)
        if isinstance(v,float):return b'D'+struct.pack('<d',v)
        return v['kind'].encode()+struct.pack('<III',v['count'],0,len(v['data']))+v['data']
    def node(n,start):
        name=n['name'].encode();p=b''.join(prop(v) for v in n['properties']);pos=start+13+len(name)+len(p);out=[]
        for c in n['children']:b=node(c,pos);out.append(b);pos+=len(b)
        if n['children']:out.append(bytes(13));pos+=13
        return struct.pack('<III',pos,len(n['properties']),len(p))+bytes([len(name)])+name+p+b''.join(out)
    out=fbx.MAGIC+struct.pack('<I',7400)
    for n in roots:out+=node(n,len(out))
    return out+bytes(13)


def source(path,clusters=1):
    path.write_bytes(source_fixture(clusters,1/clusters));_,roots,_=fbx.parse(path);N=lambda name,p=(),c=():{'name':name,'properties':list(p),'children':list(c)}
    objects=next(n for n in roots if n['name']=='Objects');g=next(n for n in objects['children'] if n['name']=='Geometry')
    def vec(width,values):return {'kind':'d','count':len(values),'data':struct.pack('<'+str(len(values))+'d',*values)}
    for name,label,width,values in [('LayerElementNormal','Normals',3,[0.,0.,1.]*3),('LayerElementUV','UV',2,[0.,0.,1.,0.,0.,1.])]:
        g['children'].append(N(name,[0],[N('MappingInformationType',['ByControlPoint']),N('ReferenceInformationType',['Direct']),N(label,[vec(width,values)])]))
    next(n for n in roots if n['name']=='Connections')['children'].append(N('C',['OO',20,21]));path.write_bytes(encode(roots));return roots


def body():
    frame=bytearray(160);frame[:4]=b'Bone';struct.pack_into('<16f',frame,32,*I);struct.pack_into('<3f',frame,96,1,1,1);struct.pack_into('<4f',frame,120,0,0,0,1);struct.pack_into('<6i',frame,136,0,-1,-1,-1,-1,0)
    frames=mapping({0x14:u32(0),0x15:u32(1),0x16:bytes(frame),0x18:mapping({0x15:u32(1),0x16:struct.pack('<16f',*I)})})
    vertices=b''.join(struct.pack('<8f',*p,0,0,1,*uv)+bytes(4)+struct.pack('<3f',1,0,0) for p,uv in [((0,0,0),(0,1)),((1,0,0),(1,1)),((0,1,0),(0,0))])
    fmt=mapping({0x14:u32(48),0x15:u32(5),0x16:u32(1),0x17:meshes.PNUV32+struct.pack('<IHHIHH',0x80,0xa4,32,0x90,3,36)})
    return mapping({0x14:editing.encode_value('string','[LOCAL]MESH\\0'),0x3c:u32(3),0x1a:frames,0x3f:frames,0x3d:mapping({0x14:b'\1',0x15:u32(3),0x16:struct.pack('<3H',0,2,1)}),0x3e:mapping({0x14:fmt,0x15:u32(3),0x16:vertices})})


def library(raw,with_model=False,model_parent=-1,map_value=0):
    f=Node(raw,0,len(raw)).mapping();v=f[0x3e].mapping();i=f[0x3d].mapping();vb=v[0x16].raw();ib=i[0x16].raw();vertex_offset=(len(ib)+15)//16*16
    ref=lambda offset,raw:struct.pack('<6I',offset,0,len(raw),len(raw),0,0)
    strings=b'LOCAL\0[LOCAL]MESH\\0\0mesh\0';strref=lambda offset,size:struct.pack('<II',size,offset)
    fields={k:n.raw() for k,n in f.items()};fields[0x14]=strref(6,13);fields[0x15]=strref(20,4);fields[0x3e]=editing.rewrite_map(f[0x3e],{0x16:ref(vertex_offset,vb)});fields[0x3d]=editing.rewrite_map(f[0x3d],{0x16:ref(0,ib)})
    members=[typed(0x410035,fields)]
    if with_model:
        model_ref=strref(len(strings),14);strings+=b'[LOCAL]MODEL\\0\0'
        label_ref=strref(len(strings),5);strings+=b'model\0'
        frame=bytearray(180);frame[:4]=b'Bone';struct.pack_into('<16f',frame,32,*I);struct.pack_into('<4f',frame,96,0,0,0,1);struct.pack_into('<3f',frame,124,1,1,1);struct.pack_into('<5i',frame,160,0,model_parent,-1,-1,0)
        frames=mapping({0x14:u32(0),0x15:u32(1),0x16:bytes(frame),0x18:b'\1'})
        component=typed(0x410067,{0x1f:mapping({0x14:strref(6,13)})});lod=typed(0x412e00,{0xa:array([component]),0xc:frames,0xe:array([typed(0x4100a0,{0x16:u32(1),0x17:struct.pack('<i',map_value)})])})
        members.append(typed(0x41004b,{0x14:model_ref,0x15:label_ref,0x20:struct.pack('<f',1),0x21:frames,0x1f:array([lod]),0x26:mapping({}),0x27:mapping({}),0x29:mapping({})}))
    meta=mapping({0x13:u32(8),0x14:strref(0,5),0x15:array(members),0x18:array([]),0x1c:array([])});align=lambda x:x+bytes((-len(x))%16);parts=list(map(align,[strings,meta,ib+bytes(vertex_offset-len(ib))+vb]))
    return content_libraries.SIGNATURE+struct.pack('<IIII',8,*map(len,parts))+bytes(8)+b''.join(parts)


class SourceCorrespondenceTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve();self.source=self.root/'source.fbx';source(self.source);self.raw=body()

    def test_local_geometry_and_json_replay_bind_to_real_clb_bytes(self):
        clb=self.root/'local.clb';clb.write_bytes(library(self.raw));r=corr.inspect(self.source,clb,'[LOCAL]MESH\\0');self.assertTrue(r['assessment']['local_mesh_correspondence_verified']);self.assertFalse(r['assessment']['source_world_bind_space_verified'])
        self.assertEqual(corr.verify(json.loads(json.dumps(r))),r['assessment'])
        changed=copy.deepcopy(r);changed['assessment']['source_world_bind_space_verified']=True
        with self.assertRaisesRegex(FormatError,'assessment'):corr.verify(changed)

    def test_source_bone_weights_cannot_be_reassigned_to_another_bone(self):
        source(self.source,2)
        with self.assertRaisesRegex(FormatError,'bone-weight'):corr.assess(self.source,self.raw)

    def test_uv_flip_and_source_positions_are_required(self):
        f=Node(self.raw,0,len(self.raw)).mapping();v=f[0x3e].mapping()
        for offset in (0,28):
            data=bytearray(v[0x16].raw());struct.pack_into('<f',data,offset,9)
            raw=editing.rewrite_map(Node(self.raw,0,len(self.raw)),{0x3e:editing.rewrite_map(f[0x3e],{0x16:bytes(data)})})
            with self.assertRaisesRegex(FormatError,'correspondence'):corr.assess(self.source,raw)

    def test_winding_duplicate_triangles_and_zero_objects_do_not_pass(self):
        f=Node(self.raw,0,len(self.raw)).mapping()
        for values in ([0,1,2],[0,2,1,0,2,1],[]):
            raw=editing.rewrite_map(Node(self.raw,0,len(self.raw)),{0x3d:editing.rewrite_map(f[0x3d],{0x15:u32(len(values)),0x16:struct.pack('<'+'H'*len(values),*values)})})
            with self.assertRaises(FormatError):corr.assess(self.source,raw)

    def test_wrong_default_frame_parent_is_detected(self):
        f=Node(self.raw,0,len(self.raw)).mapping();frames=f[0x1a].mapping();data=bytearray(frames[0x16].raw());struct.pack_into('<i',data,144,0)
        raw=editing.rewrite_map(Node(self.raw,0,len(self.raw)),{0x1a:editing.rewrite_map(f[0x1a],{0x16:bytes(data)})})
        with self.assertRaisesRegex(FormatError,'topology'):corr.assess(self.source,raw)

    def test_source_graph_keeps_second_chain_under_its_own_root(self):
        node=lambda name:{'name':'Model','properties':[0,name,'LimbNode'],'children':[]}
        objects={i:node(name) for i,name in [(1,'A'),(2,'aChild'),(3,'B'),(4,'bChild'),(5,'Armature')]}
        links=[['OO',1,5],['OO',2,1],['OO',3,5],['OO',4,3]];expected,root,reference,nodes=corr.topology(objects,links,['A','aChild','B','bChild'])
        self.assertEqual(expected,[[-1,2,1],[0,-1,-1],[-1,-1,3],[2,-1,-1]]);self.assertEqual((root,reference),(0,5))
        links[-1]=['OO',4,6];objects[6]=node('OtherArmature')
        with self.assertRaisesRegex(FormatError,'multiple spaces'):corr.topology(objects,links,['A','aChild','B','bChild'])

    def test_ambiguous_duplicate_source_polygons_are_not_arbitrarily_matched(self):
        raw=fbx.rewrite_arrays(self.source.read_bytes(),{20:{'PolygonVertexIndex':b'i'+struct.pack('<III',6,0,24)+struct.pack('<6i',0,1,-3,0,1,-3)}},'Geometry');self.source.write_bytes(raw)
        with self.assertRaisesRegex(FormatError,'ambiguous'):corr.assess(self.source,self.raw)

    def test_source_transform_pivots_need_explicit_support(self):
        node={'name':'Model','properties':[1,'Bone','LimbNode'],'children':[{'name':'Properties70','properties':[],'children':[{'name':'P','properties':['PreRotation','Vector','','',1.,0.,0.],'children':[]}]}]}
        with self.assertRaisesRegex(FormatError,'pivots'):corr.local_matrix(node)


if __name__=='__main__':unittest.main()
