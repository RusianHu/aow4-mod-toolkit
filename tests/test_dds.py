import io
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import dds,compressed_textures as compressed,images,texture_toolchain
from aow4mod.core import FormatError


def sample(dxgi=99,count=4):
    sizes=dds.sizes(8,8,count)
    return dds.encode(8,8,dxgi,[bytes([i])*dds.level_bytes(w,h,dxgi) for i,(w,h) in enumerate(sizes)])


class DDSTests(unittest.TestCase):
    def mutate(self,data,offset,value):
        result=bytearray(data);struct.pack_into('<I',result,offset,value);return bytes(result)

    def test_typed_bc7_mips_roundtrip(self):
        r=dds.parse(sample());self.assertEqual(r['native_format'],192);self.assertEqual(r['color_space'],2)
        self.assertEqual([(l['width'],l['height'],len(l['data'])) for l in r['levels']],[(8,8,64),(4,4,16),(2,2,16),(1,1,16)])

    def test_signed_bc5_and_bc1_sizes(self):
        r=dds.parse(sample(84));self.assertTrue(r['signed']);self.assertEqual(r['native_format'],172)
        self.assertEqual(len(dds.parse(sample(72))['levels'][0]['data']),32)

    def test_pillow_legacy_view_keeps_typed_srgb_block_data(self):
        for dxgi in (72,78):
            r=dds.parse(sample(dxgi,count=1));image=compressed.pillow_decode(r,0)
            self.assertEqual(image.size,(8,8));self.assertEqual(r['color_space'],2)

    def test_tight_rgba_reference_levels(self):
        r=dds.parse(sample(29));self.assertEqual(r['block_bytes'],0);self.assertEqual(len(r['levels'][-1]['data']),4)

    def test_legacy_dxt5_is_supported_without_guessing_srgb(self):
        raw=sample(77);raw=self.mutate(raw,84,int.from_bytes(b'DXT5','little'));raw=raw[:128]+raw[148:]
        r=dds.parse(raw);self.assertEqual(r['dxgi'],77);self.assertEqual(r['color_space'],1)

    def test_trailing_bytes_and_truncation_are_rejected(self):
        for data in (sample()+b'x',sample()[:-1],sample()[:140]):
            with self.assertRaises(FormatError):dds.parse(data)

    def test_typeless_arrays_cubes_and_premultiplied_alpha_rejected(self):
        for offset,value in [(128,97),(132,4),(136,4),(140,2),(144,2),(112,0x200)]:
            with self.subTest(offset=offset,value=value),self.assertRaises(FormatError):dds.parse(self.mutate(sample(),offset,value))

    def test_header_sizes_pitch_and_mip_flags_must_agree(self):
        for offset,value in [(4,120),(76,28),(20,1),(28,13),(108,0x1000)]:
            with self.subTest(offset=offset),self.assertRaises(FormatError):dds.parse(self.mutate(sample(),offset,value))

    def test_partial_small_level_view_is_only_for_cpu_decode(self):
        r=dds.parse(sample());view=dds.one_level(r,3)
        with self.assertRaises(FormatError):dds.parse(view)
        self.assertEqual(dds.parse(view,allow_partial_base=True)['width'],1)

    def test_nonmultiple_base_and_dimension_bound(self):
        for width in (7,2049):
            with self.assertRaises(FormatError):dds.parse(self.mutate(sample(),16,width))

    def test_mip_payload_lengths_are_derived_from_blocks(self):
        with self.assertRaises(FormatError):dds.encode(8,8,99,[b'not a block'])
        with self.assertRaises(FormatError):dds.sizes(1,1,2)

    def test_unknown_tool_is_not_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp).resolve()/'texconv.exe';path.write_bytes(b'untrusted')
            with self.assertRaises(FormatError):texture_toolchain.inspect(path)


class CompressionPolicyTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()

    def png(self,size=(8,8),color=(128,128,255,255)):
        p=self.root/'source.png';images.pillow().new('RGBA',size,color).save(p);return p

    def test_no_implicit_compression_resize(self):
        with self.assertRaisesRegex(FormatError,'divisible'):compressed.compression_inputs(self.png((9,7)),'bc7','color','full')

    def test_bc1_refuses_smooth_alpha(self):
        with self.assertRaisesRegex(FormatError,'smooth'):compressed.compression_inputs(self.png(color=(20,100,200,50)),'bc1','color','full')

    def test_bc5_requires_positive_z_opaque_normals(self):
        for color in ((128,128,0,255),(128,128,255,50)):
            with self.assertRaises(FormatError):compressed.compression_inputs(self.png(color=color),'bc5s','normal-xyz','full')

    def test_codec_roles_are_not_interchangeable(self):
        for codec,role in [('bc5s','color'),('bc7','normal-xyz')]:
            with self.assertRaises(FormatError):compressed.compression_inputs(self.png(),codec,role,'none')

    def test_reference_mips_and_target_role_are_explicit(self):
        raw,origin,levels,target,role=compressed.compression_inputs(self.png(),'bc5s','normal-xyz','full')
        self.assertEqual((target,role),(84,'normal-xy-snorm'));self.assertEqual(len(levels),4);self.assertEqual(dds.parse(raw)['dxgi'],28)

    def proof(self,image):
        p=self.root/'pixels.rgba';p.write_bytes(image.tobytes())
        from aow4mod.filesystem import file_hash
        return {'rgba':str(p),'rgba_sha256':file_hash(p),'width':image.width,'height':image.height}

    def test_quality_limit_rejects_lossy_pixels(self):
        expected=images.pillow().new('RGBA',(4,4),(0,0,0,255));actual=images.pillow().new('RGBA',(4,4),(50,0,0,255))
        with self.assertRaises(FormatError):compressed.quality([expected],[self.proof(actual)],False,32,8)

    def test_signed_xy_normal_angle_quality(self):
        expected=images.pillow().new('RGBA',(4,4),(128,128,255,255));decoded=images.pillow().new('RGBA',(4,4),(128,128,128,255))
        result=compressed.quality([expected],[self.proof(decoded)],True,32,8);self.assertLess(result[0]['max_normal_angle_degrees'],0.01)

    def test_role_cannot_override_dds_srgb_metadata(self):
        with self.assertRaises(FormatError):compressed.document('OWN',dds.parse(sample()),'data')


if __name__=='__main__':unittest.main()
