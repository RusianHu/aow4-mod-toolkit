import base64
import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import animation_precision,animation_data,animation_poses,source_motion,editing
from aow4mod.core import Node,FormatError
from test_animation_sources import fixture as source_fixture
from test_animation_data import fixture,mapping


class SourceMotionTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve();self.source=self.root/'source.fbx';self.source.write_bytes(source_fixture(attributes=True))

    def document(self):
        return {'21_FList':{'1_this':[{'cid':str(0x410005),'20_FName':'[LOCAL]ANIM\\0','31_FPeriod':'1000000','33_FAnimations':{
            '36_FRotationKeys':{'21_FCount':'2','22_FList':base64.b64encode(struct.pack('<I4f',0,0,0,0,1)+struct.pack('<I4f',999980,0,0,0,1)).decode()},
            '38_FCompressedRotationKeys':{'21_FRotations':{'31_FStream':'preserved'}},
            '1_this':[{'20_FName':'Bone','19_FPeriod':'1000000','31_Data.FCount':'2','35_Data.FTimeToIndex':'0.000001','37_Data.FCount':'2'}]}}]}}

    def test_precision_uses_source_times_and_duplicates_only_final_quaternion(self):
        before=self.document();old=copy.deepcopy(before);after,change=animation_precision.derive(before,'[LOCAL]ANIM\\0',self.source);a=after['21_FList']['1_this'][0]['33_FAnimations'];track=a['1_this'][0];keys=list(struct.iter_unpack('<I4f',base64.b64decode(a['36_FRotationKeys']['22_FList'])))
        self.assertEqual(before,old);self.assertEqual([k[0]for k in keys],[0,1000000,1000001]);self.assertEqual(keys[-1][1:],keys[-2][1:]);self.assertEqual(track['31_Data.FCount'],'3');self.assertNotIn('37_Data.FCount',track);self.assertEqual(a['38_FCompressedRotationKeys'],old['21_FList']['1_this'][0]['33_FAnimations']['38_FCompressedRotationKeys']);self.assertEqual(change['tracks'][0]['maximum_retime_us'],20)

    def guarded_body(self,guard_time=1000001,guard_value=1,preceding=1000000,referenced=True,role='rotation'):
        default,tracks,pools,src=fixture();track=Node(tracks[0],0,len(tracks[0]));updates={0x25:None,0x23:None}
        if role=='rotation':updates[0x1f]=struct.pack('<H',3 if referenced else 2)
        else:updates[0x1c]=struct.pack('<H',3)
        tracks=[editing.rewrite_map(track,updates)]
        if role=='rotation':rows=struct.pack('<I4f',0,0,0,0,1)+struct.pack('<I4f',preceding,0,0,0,1)+struct.pack('<I4f',guard_time,0,0,0,guard_value)
        else:rows=struct.pack('<I3f',0,0,0,0)+struct.pack('<I3f',preceding,0,0,0)+struct.pack('<I3f',guard_time,0,0,0)
        pools[role]=mapping({0x15:struct.pack('<I',3),0x16:rows})
        return animation_data.derive(default,tracks,pools,'LOCAL','Guard')

    def test_guard_decoding_keeps_clip_end_inside_the_raw_range(self):
        d=animation_poses.decode(self.guarded_body());self.assertEqual(animation_poses.sample(d,'Bone',1000000,True)['rotation'],[0,0,0,1])

    def test_future_or_different_or_unreferenced_guard_is_rejected(self):
        for kwargs in ({'guard_time':1000002},{'guard_value':-1},{'preceding':999999},{'referenced':False},{'role':'translation'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(FormatError):animation_poses.decode(self.guarded_body(**kwargs))

    def test_source_grid_includes_keys_end_and_asymmetric_interpolation_samples(self):
        times=source_motion.grid(self.source,1000000);self.assertEqual(times,[0,333333,500000,666667,1000000]);self.assertEqual(source_motion.value(([0,10],[2,6]),2.5),3)

    def test_nonlinear_curves_and_wrong_duration_or_key_counts_do_not_convert(self):
        self.source.write_bytes(source_fixture(attributes=True,interpolation=8))
        with self.assertRaisesRegex(FormatError,'linear'):animation_precision.derive(self.document(),'[LOCAL]ANIM\\0',self.source)
        self.source.write_bytes(source_fixture(attributes=True));d=self.document();d['21_FList']['1_this'][0]['31_FPeriod']='900000'
        with self.assertRaisesRegex(FormatError,'duration'):animation_precision.derive(d,'[LOCAL]ANIM\\0',self.source)
        d=self.document();d['21_FList']['1_this'][0]['33_FAnimations']['1_this'][0]['31_Data.FCount']='1'
        with self.assertRaises(FormatError):animation_precision.derive(d,'[LOCAL]ANIM\\0',self.source)


if __name__=='__main__':unittest.main()
