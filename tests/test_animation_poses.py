import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import animation_data,animation_poses
from aow4mod import editing
from aow4mod.filesystem import file_hash
from aow4mod.core import FormatError
from test_animation_data import fixture,mapping


class AnimationPoseTests(unittest.TestCase):
    def body(self):
        default,tracks,pools,source=fixture()
        return animation_data.derive(default,tracks,pools,'CLI_POSE','Walk')

    def test_components_missing_values_and_end_boundaries(self):
        body=self.body();decoded=animation_poses.decode(body)
        for time in (0,500000,1000000):
            result=animation_poses.sample(decoded,'Bone',time,True)
            self.assertEqual(result['rotation'],[0,0,0,1]);self.assertEqual(result['scale'],[1,1,1])
        decoded['tracks']['Bone']['keys']['translation']=[]
        self.assertIsNone(animation_poses.sample(decoded,'Bone',0,True)['translation'])

    def test_shortest_quaternion_arc_and_nearest_policy(self):
        q=animation_poses.slerp((0,0,0,1),(0,0,0,-1),0.5)
        self.assertAlmostEqual(abs(q[3]),1)
        d=animation_poses.decode(self.body());d['tracks']['Bone']['keys']['translation']=[(0,0,0,0),(1000000,10,0,0)]
        self.assertEqual(animation_poses.sample(d,'Bone',500000,False)['translation'],[10,0,0])
        self.assertEqual(animation_poses.sample(d,'Bone',250000,True)['translation'],[2.5,0,0])

    def test_distinct_translation_rotation_and_empty_scale_layout(self):
        default,tracks,pools,source=fixture()
        tracks=[mapping({0x42:struct.pack('<I',5),0x14:editing.encode_value('string','Bone'),0x13:struct.pack('<Q',1000000),
            0x1c:struct.pack('<H',3),0x1f:struct.pack('<H',2),0x25:struct.pack('<H',2),0x23:struct.pack('<f',1e-6)})]
        pools['translation']=mapping({0x15:struct.pack('<I',3),0x16:b''.join(struct.pack('<I3f',t,x,0,0) for t,x in [(0,0),(500000,10),(1000000,0)])})
        pools['scale']=b'\0'
        body=animation_data.derive(default,tracks,pools,'CLI_POSE','Distinct')
        decoded=animation_poses.decode(body)
        self.assertEqual(len(decoded['tracks']['Bone']['keys']['rotation']),2)
        self.assertEqual(len(decoded['tracks']['Bone']['keys']['translation']),3)
        result=animation_poses.sample(decoded,'Bone',250000,True)
        self.assertEqual(result['translation'],[5,0,0]);self.assertIsNone(result['scale'])
        self.assertEqual(animation_data.analyze(body,source)['derived_fps'],2)

    def test_native_slerp_threshold_does_not_use_the_wider_linear_approximation(self):
        import math
        angle=math.radians(2);a=(0,0,0,1);b=(0,0,math.sin(angle/2),math.cos(angle/2))
        q=animation_poses.slerp(a,b,0.5)
        self.assertAlmostEqual(sum(v*v for v in q),1,places=7)

    def test_verifier_recomputes_values_coverage_and_serialized_preservation(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder).resolve();raw=self.body();objects={}
            for role in ('before','after'):
                path=p/(role+'.bin');path.write_bytes(raw);objects[role]={'file':str(path),'sha256':file_hash(path)}
            decoded=animation_poses.decode(raw)
            payload={'objects':objects,'interpolate':True,'samples':[{'name':'Bone','time_us':0,'guards_preserved':True,**animation_poses.sample(decoded,'Bone',0,True)}]}
            self.assertTrue(animation_poses.assess(payload,['Bone'],[0])['sampled_channels_verified'])
            bad=copy.deepcopy(payload);bad['samples'][0]['translation'][0]=10
            self.assertEqual(animation_poses.assess(bad,['Bone'],[0])['channel_issue_count'],1)
            bad=copy.deepcopy(payload);bad['samples']=[]
            with self.assertRaises(FormatError):animation_poses.assess(bad,['Bone'],[0])
            bad=copy.deepcopy(payload);bad['interpolate']=False
            with self.assertRaises(FormatError):animation_poses.assess(bad,['Bone'],[0])


if __name__=='__main__':unittest.main()
