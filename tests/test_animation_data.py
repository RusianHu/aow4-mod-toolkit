import copy
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import animation_data,editing,publication
from aow4mod.core import Node,FormatError


def mapping(fields):return editing.rewrite_map(Node(b'\0',0,1),fields)


def fixture():
    track=mapping({0x42:struct.pack('<I',5),0x14:editing.encode_value('string','Bone'),0x13:struct.pack('<Q',1000000),
        **{key:struct.pack('<H',2) for key in (0x1c,0x1f,0x25,0x28)}})
    pool=lambda rows:mapping({0x15:struct.pack('<I',2),0x16:rows})
    pools={'rotation':pool(b''.join(struct.pack('<I4f',t,0,0,0,1) for t in (0,1000000))),
        'translation':pool(b''.join(struct.pack('<I3f',t,0,0,0) for t in (0,1000000))),
        'scale':pool(b''.join(struct.pack('<I3f',t,1,1,1) for t in (0,1000000))),
        'compressed_rotation':mapping({0x15:mapping({0x1e:struct.pack('<I',2),0x1f:struct.pack('<4h',0,0,0,32767)*2})})}
    default=mapping({0x21:mapping({1:b'\0'})})
    source={'bone_names':['Bone'],'model_names':['Bone'],'seconds':1.0}
    return default,[track],pools,source


class AnimationDataTests(unittest.TestCase):
    def test_complete_take_preserves_shared_streams_and_derives_native_fps(self):
        default,tracks,pools,source=fixture();body=animation_data.derive(default,tracks,pools,'CLI_ANIM','Walk')
        result=animation_data.analyze(body,source)
        self.assertEqual(result['pool_counts'],dict.fromkeys(pools,2))
        self.assertEqual(result['derived_fps'],1)
        self.assertFalse(result['pose_sampling_verified'])
        self.assertEqual(Node(body,0,len(body)).mapping()[0x15].string(),'Walk')

    def test_out_of_pool_offsets_and_duplicate_or_missing_bones_are_rejected(self):
        default,tracks,pools,source=fixture()
        bad=editing.rewrite_map(Node(tracks[0],0,len(tracks[0])),{0x1b:struct.pack('<I',2)})
        for selected in ([bad],tracks*2):
            with self.assertRaises(FormatError):animation_data.analyze(animation_data.derive(default,selected,pools,'CLI_ANIM','Walk'),source)
        source['bone_names']=['Missing']
        with self.assertRaisesRegex(FormatError,'names differ'):animation_data.analyze(animation_data.derive(default,tracks,pools,'CLI_ANIM','Walk'),source)

    def test_nonfinite_or_nonunit_rotations_and_changed_duration_are_rejected(self):
        default,tracks,pools,source=fixture()
        for value in (float('nan'),2):
            changed=dict(pools);changed['rotation']=mapping({0x15:struct.pack('<I',2),0x16:struct.pack('<I4f',0,0,0,0,value)*2})
            with self.assertRaises(FormatError):animation_data.analyze(animation_data.derive(default,tracks,changed,'CLI_ANIM','Walk'),source)
        source['seconds']=2
        with self.assertRaisesRegex(FormatError,'duration differs'):animation_data.analyze(animation_data.derive(default,tracks,pools,'CLI_ANIM','Walk'),source)

    def test_key_pool_counts_bytes_and_time_order_are_checked(self):
        default,tracks,pools,source=fixture()
        for stream in (bytes(1),struct.pack('<I3f',1000000,0,0,0)+struct.pack('<I3f',0,0,0,0)):
            changed=dict(pools);changed['translation']=mapping({0x15:struct.pack('<I',2),0x16:stream})
            with self.assertRaises(FormatError):animation_data.analyze(animation_data.derive(default,tracks,changed,'CLI_ANIM','Walk'),source)

    def test_source_provenance_and_gate_cannot_be_removed_independently(self):
        manifest={'files':{'Libraries/CLI_ANIM.clb':'x'},'source_files':{'SourceAnimations/CLI_ANIM/.aow4mod-animation.json':{'sha256':'s'}},
            'draft_animation_assets':[{'file':'Libraries/CLI_ANIM.clb','source_receipt':'SourceAnimations/CLI_ANIM/.aow4mod-animation.json','source_receipt_sha256':'s'}],
            'release_gate':{'review_required':True,'issues':[{'issue':'draft_animation_asset_requires_binding_pose_review','file':'Libraries/CLI_ANIM.clb'}]}}
        publication.check_animation_drafts(manifest,manifest['files'])
        for key in ('draft_animation_assets','release_gate'):
            changed=copy.deepcopy(manifest);changed.pop(key)
            with self.assertRaises(FormatError):publication.check_animation_drafts(changed,manifest['files'])


if __name__=='__main__':unittest.main()
