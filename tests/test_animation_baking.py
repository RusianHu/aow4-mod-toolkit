from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import animation_baking,animation_sources,fbx
from aow4mod.core import FormatError
from test_animation_sources import fixture


class AnimationBakingTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve();self.path=self.root/'source.fbx'

    def test_uniform_grid_preserves_linear_source_timing_and_footer(self):
        stop=animation_sources.FBX_SECOND
        self.path.write_bytes(fixture(times=(0,stop//3,stop),attributes=True,values=(0,2,4))+b'private-footer')
        with self.assertRaisesRegex(FormatError,'uniformly baked'):animation_sources.inspect(self.path)
        with self.assertRaisesRegex(FormatError,'error bound'):animation_baking.bake(self.path,4)
        before=self.path.read_bytes();raw,report=animation_baking.bake(self.path,4,max_error=0.2)
        self.assertEqual(before,self.path.read_bytes());self.assertTrue(raw.endswith(b'private-footer'))
        self.assertEqual(report['frames'],5);self.assertEqual(report['curves'],9)
        output=self.root/'baked.fbx';output.write_bytes(raw);animation_sources.inspect(output)
        _,roots,_=fbx.parse(output)
        curve=next(o for r in roots if r['name']=='Objects' for o in r['children'] if o['name']=='AnimationCurve')
        values=next(c['properties'][0] for c in curve['children'] if c['name']=='KeyValueFloat')
        self.assertEqual(struct.unpack('<5f',values['data']),(0,1.5,2.5,3.25,4))

    def test_nonlinear_modes_bad_fps_and_missing_attributes_are_rejected(self):
        for mode in (2,8):
            self.path.write_bytes(fixture(attributes=True,interpolation=mode))
            with self.assertRaisesRegex(FormatError,'linear'):animation_baking.bake(self.path,24)
        self.path.write_bytes(fixture())
        with self.assertRaisesRegex(FormatError,'attributes'):animation_baking.bake(self.path,24)
        for fps in (0,121,float('nan'),True):
            with self.assertRaises(FormatError):animation_baking.bake(self.path,fps)


if __name__=='__main__':unittest.main()
