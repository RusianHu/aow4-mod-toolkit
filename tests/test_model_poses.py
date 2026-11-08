import copy
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import model_poses,model_animation_bindings,publication
from aow4mod.core import Node,FormatError
from test_animation_data import mapping


def frame(name,parent=-1,sibling=-1,child=-1,bone=0,scale=(1,1,1),translation=(0,0,0),rotation=(0,0,0,1)):
    raw=bytearray(180);raw[:len(name)]=name.encode()
    struct.pack_into('<16f',raw,32,1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)
    struct.pack_into('<10f',raw,96,*rotation,*translation,*scale)
    struct.pack_into('<4i',raw,160,bone,parent,sibling,child)
    return bytes(raw)


def skeleton(records,root=0):
    raw=mapping({0x14:struct.pack('<i',root),0x15:struct.pack('<I',len(records)),0x16:b''.join(records),0x18:b'\1'})
    return Node(raw,0,len(raw))


class ModelPoseTests(unittest.TestCase):
    def test_bind_pose_parent_scale_compensation_and_rotation(self):
        q=2**-0.5
        node=skeleton([frame('Root',child=1,bone=-1,scale=(2,3,4),translation=(10,20,30),rotation=(0,0,q,q)),frame('Child',parent=0,translation=(1,0,0))])
        data=model_poses.frames(node);matrices=model_poses.expected(data,{'tracks':{}},0)
        for a,b in zip(matrices[1][12:15],(10,22,30)):self.assertAlmostEqual(a,b,places=5)
        self.assertAlmostEqual(sum(v*v for v in matrices[1][:3]),1,places=5)

    def test_cycles_unreachable_duplicate_names_zero_scale_and_bad_bones_are_refused(self):
        cases=[
            [frame('Root',child=1,bone=-1),frame('Child',parent=1)],
            [frame('Root'),frame('Other',bone=1)],
            [frame('Root',child=1,bone=-1),frame('root',parent=0)],
            [frame('Root',scale=(0,1,1))],
            [frame('Root',bone=-1)],
        ]
        for records in cases:
            with self.subTest(records=len(records)),self.assertRaises(FormatError):model_poses.frames(skeleton(records))

    def test_additional_offsets_and_unsupported_record_layout_are_not_silently_used(self):
        raw=bytearray(frame('Root'));struct.pack_into('<f',raw,136,1)
        with self.assertRaisesRegex(FormatError,'offsets'):model_poses.frames(skeleton([bytes(raw)]))
        with self.assertRaises(FormatError):model_poses.frames(skeleton([frame('Root')[:156]]))

    def test_binding_recipe_changes_only_the_declared_default_link(self):
        model={'cid':str(0x41004b),'20_FName':'[LOCAL]MODEL\\0','keep':'value','36_FDefaultAnimation':{
            'cid':str(0x41006d),'cname':'TREAnimationLink','20_FName':'[LOCAL]ANIM\\0','23_FUserName':'Old','extra':'retained'}}
        clip={'cid':str(0x410005),'20_FName':'[LOCAL]ANIM\\1','21_FUserName':'New'}
        baseline={'21_FList':{'1_this':[model,clip]}};before=copy.deepcopy(baseline)
        desired,change=model_animation_bindings.derive(baseline,model['20_FName'],clip['20_FName'])
        self.assertEqual(baseline,before);self.assertEqual(desired['21_FList']['1_this'][0]['keep'],'value')
        self.assertEqual(change['after_link']['extra'],'retained');self.assertEqual(change['after_link']['20_FName'],clip['20_FName'])
        with self.assertRaises(FormatError):model_animation_bindings.derive(desired,model['20_FName'],clip['20_FName'])

    def test_model_binding_provenance_and_skin_review_gate_are_required(self):
        manifest={'source_files':{'SourceModelAnimations/LOCAL/model-0.json':{'sha256':'s'}},
            'draft_model_animation_assets':[{'file':'Libraries/LOCAL.clb','model':'[LOCAL]MODEL\\0','animation':'[LOCAL]ANIM\\1',
                'source_receipt':'SourceModelAnimations/LOCAL/model-0.json','source_receipt_sha256':'s'}],
            'release_gate':{'review_required':True,'issues':[{'issue':'draft_model_animation_requires_skinning_review','file':'Libraries/LOCAL.clb'}]}}
        expected={'Libraries/LOCAL.clb':'a'};publication.check_model_animation_drafts(manifest,expected)
        for key in ('draft_model_animation_assets','release_gate'):
            changed=copy.deepcopy(manifest);changed.pop(key)
            with self.assertRaises(FormatError):publication.check_model_animation_drafts(changed,expected)


if __name__=='__main__':unittest.main()
