import base64
import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import skeletal_sources,skeletal_data,skeletal_imports,skin_meshes,fbx,editing,publication,content_libraries
from aow4mod.core import Node,FormatError
from test_animation_sources import fixture,prop
from test_skinning import fixture as mesh_fixture
from test_aow4_mod_audit import mapping,typed,array,u32

I=[1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.]


def doubles(values):return b'd'+struct.pack('<III',len(values),0,len(values)*8)+struct.pack('<'+str(len(values))+'d',*values)


def source_fixture(clusters=1,weight=0.5,duplicate=False):
    record=lambda kind,i,name,sub,children=():(kind,[prop('L',i),prop('S',name),prop('S',sub)],list(children))
    extra=[record('Geometry',20,'Panel','Mesh',[('Vertices',[doubles([0.,0.,0.,1.,0.,0.,0.,1.,0.])],[]),('PolygonVertexIndex',[prop('i',[0,1,-3])],[])]),record('Model',21,'Panel','Mesh'),record('Deformer',22,'Skin','Skin')]
    con=[('C',[prop('S','OO'),prop('L',22),prop('L',20)],[])]
    for i in range(clusters):
        cid=100+i;bone=1 if i==0 else 200+i
        if i:extra.append(record('Model',bone,'ExtraBone'+str(i),'LimbNode'))
        extra.append(record('Deformer',cid,'Cluster'+str(i),'Cluster',[('Indexes',[prop('i',[0,0,2] if duplicate else [0,1,2])],[]),('Weights',[doubles([weight]*3)],[]),('Transform',[doubles(I)],[]),('TransformLink',[doubles(I)],[])]))
        con.extend([('C',[prop('S','OO'),prop('L',cid),prop('L',22)],[]),('C',[prop('S','OO'),prop('L',bone),prop('L',cid)],[])])
    return fixture(extra=extra,extra_connections=con,attributes=True)


class SkeletalImportTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve();self.path=self.root/'source.fbx'

    def test_source_weight_changes_are_explicit_and_other_arrays_footer_remain(self):
        self.path.write_bytes(source_fixture()+b'private-footer');original=self.path.read_bytes()
        with self.assertRaisesRegex(FormatError,'explicit top-four'):skeletal_sources.source(self.path)
        raw,details=skeletal_sources.normalize(self.path,0);other=self.root/'prepared.fbx';other.write_bytes(raw)
        self.assertEqual(self.path.read_bytes(),original);self.assertTrue(raw.endswith(b'private-footer'));self.assertEqual(skeletal_sources.source(other)[3]['weight_findings'],0)
        self.assertEqual(details['maximum_dropped_fraction'],0);self.assertEqual(details['changed_weight_entries'],3)
        a=fbx.parse(self.path)[1];b=fbx.parse(other)[1]
        def strip(nodes):
            return [{k:strip(v) if k=='children' else v for k,v in n.items()} for n in nodes if n['name']!='Weights']
        self.assertEqual(strip(a),strip(b))

    def test_top_four_loss_bound_is_respected_and_ties_are_deterministic(self):
        self.path.write_bytes(source_fixture(5,0.2))
        with self.assertRaisesRegex(FormatError,'dropped-weight bound'):skeletal_sources.normalize(self.path,0.19)
        raw,details=skeletal_sources.normalize(self.path,0.21);self.assertAlmostEqual(details['maximum_dropped_fraction'],0.2)
        out=self.root/'out.fbx';out.write_bytes(raw);_,rows,_,info=skeletal_sources.source(out)
        self.assertEqual(info['maximum_influences'],4);self.assertEqual(rows[104]['weights'],[0,0,0]);self.assertEqual(rows[100]['weights'],[0.25]*3)
        self.assertEqual(raw,skeletal_sources.normalize(self.path,0.21)[0])

    def test_duplicate_weights_and_unknown_rewrite_targets_are_refused(self):
        self.path.write_bytes(source_fixture(duplicate=True))
        with self.assertRaisesRegex(FormatError,'duplicate'):skeletal_sources.source(self.path,True)
        self.path.write_bytes(source_fixture())
        with self.assertRaisesRegex(FormatError,'coverage'):fbx.rewrite_arrays(self.path.read_bytes(),{999:{'Weights':doubles([1.])}},'Deformer')

    def test_unused_palette_hole_is_allowed_but_shader_read_hole_is_refused(self):
        m=mesh_fixture()['21_FList']['1_this'][0];raw=bytearray(base64.b64decode(m['26_FFrames']['22_Frames']));struct.pack_into('<i',raw,3*160+136,-1);m['26_FFrames']['22_Frames']=base64.b64encode(raw).decode()
        data=skin_meshes.decode(skin_meshes.yaml_body(m));self.assertIsNone(data['bone_names'][3])
        struct.pack_into('<i',raw,160+136,-1);m['26_FFrames']['22_Frames']=base64.b64encode(raw).decode()
        with self.assertRaisesRegex(FormatError,'shader-read'):skin_meshes.decode(skin_meshes.yaml_body(m))

    def test_only_two_proven_reserved_frame_bytes_are_projected(self):
        before=skin_meshes.yaml_body(mesh_fixture()['21_FList']['1_this'][0]);fields=Node(before,0,len(before)).mapping();frames=fields[0x1a].mapping();raw=bytearray(frames[0x16].raw())
        def change(offset):
            changed=bytearray(raw);changed[offset]=7
            return editing.rewrite_map(Node(before,0,len(before)),{0x1a:editing.rewrite_map(fields[0x1a],{0x16:bytes(changed)})})
        same=skin_meshes.compare_body(before,change(158));self.assertTrue(same['data_equal']);self.assertFalse(same['byte_identical']);self.assertTrue(same['normalized_mesh_frame_padding'])
        self.assertFalse(skin_meshes.compare_body(before,change(156))['data_equal'])
        self.assertFalse(skin_meshes.compare_body(before,change(157))['data_equal'])
        with self.assertRaises(FormatError):skin_meshes.frame_data(bytes(159))

    def test_clb_padding_projection_retains_meaningful_flag_changes(self):
        def library(offset,value,cls=0x410035,version=3):
            strings=b'LOCAL\0[LOCAL]MESH\\0\0mesh\0';ref=lambda i,n:struct.pack('<II',n,i)
            frames=bytearray(160);frames[offset]=value;member=typed(cls,{0x14:ref(6,13),0x15:ref(20,4),0x3c:u32(version),0x1a:mapping({0x14:u32(0),0x15:u32(1),0x16:bytes(frames)})})
            meta=mapping({0x13:u32(8),0x14:ref(0,5),0x15:array([member]),0x18:array([]),0x1c:array([])});align=lambda x:x+bytes((-len(x))%16);parts=list(map(align,[strings,meta,b'opaque']))
            return content_libraries.SIGNATURE+struct.pack('<IIII',8,*map(len,parts))+bytes(8)+b''.join(parts)
        a=self.root/'a.clb';b=self.root/'b.clb';a.write_bytes(library(158,0));b.write_bytes(library(158,9));r=content_libraries.compare(a,b)
        self.assertTrue(r['all_compared_data_equal']);self.assertNotEqual(r['source_metadata_sha256'],r['saved_metadata_sha256']);self.assertTrue(r['normalized_mesh_frame_padding'])
        b.write_bytes(library(156,9));self.assertFalse(content_libraries.compare(a,b)['all_compared_data_equal'])
        for cls,version in [(0x41006c,3),(0x410035,4)]:
            a.write_bytes(library(158,0,cls,version));b.write_bytes(library(158,9,cls,version));self.assertFalse(content_libraries.compare(a,b)['all_compared_data_equal'])

    def test_model_frame_forest_gets_owned_root_and_dense_model_bones(self):
        m=mesh_fixture()['21_FList']['1_this'][0];raw=bytearray(base64.b64decode(m['26_FFrames']['22_Frames']))
        for i,(parent,sibling,child) in enumerate([(-1,1,2),(-1,-1,3),(0,-1,-1),(1,-1,-1)]):
            struct.pack_into('<3f',raw,i*160+96,1,1,1);struct.pack_into('<4f',raw,i*160+120,0,0,0,1);struct.pack_into('<3i',raw,i*160+144,parent,sibling,child)
        m['26_FFrames']['22_Frames']=base64.b64encode(raw).decode();frames,palette=skeletal_data.model_frames(m)
        self.assertEqual(frames['21_FCount'],'5');self.assertEqual(palette,[0,1,2,3])

    def test_source_provenance_and_bind_space_gate_cannot_disappear(self):
        manifest={'source_files':{'SourceSkeletons/LOCAL/.aow4mod-skeleton.json':{'sha256':'s'}},'draft_skeletal_assets':[{'file':'Libraries/LOCAL.clb','model':'[LOCAL]MODEL\\0','source_receipt':'SourceSkeletons/LOCAL/.aow4mod-skeleton.json','source_receipt_sha256':'s'}],'release_gate':{'review_required':True,'issues':[{'issue':'skeletal_model_requires_bind_space_bounds_variant_review','file':'Libraries/LOCAL.clb'}]}}
        publication.check_skeletal_drafts(manifest,{'Libraries/LOCAL.clb':'a'})
        for key in ('draft_skeletal_assets','release_gate'):
            changed=copy.deepcopy(manifest);changed.pop(key)
            with self.assertRaises(FormatError):publication.check_skeletal_drafts(changed,{'Libraries/LOCAL.clb':'a'})


if __name__=='__main__':unittest.main()
