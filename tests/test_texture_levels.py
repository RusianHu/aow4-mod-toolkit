import copy
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import images,textures,texture_levels as levels,material_parameters,clb_streams
from aow4mod.core import FormatError,Node


@unittest.skipUnless(importlib.util.find_spec('PIL'),'optional Pillow dependency')
class TextureLevelTests(unittest.TestCase):
    def image(self,size,pixels):
        image=images.pillow().new('RGBA',size);image.putdata(pixels);return image

    def test_full_npot_chain_and_one_axis(self):
        self.assertEqual(levels.dimensions(9,7,'full'),[(9,7),(4,3),(2,1),(1,1)])
        self.assertEqual(levels.dimensions(1,7,'full'),[(1,7),(1,3),(1,1)])

    def test_no_mips_preserves_base_bytes_and_hidden_rgb(self):
        image=self.image((1,1),[(90,200,25,0)]);result=levels.generate(image,'color','none')
        self.assertEqual(result[0].tobytes(),image.tobytes());self.assertEqual(len(result),1)

    def test_color_filter_uses_linear_light(self):
        image=self.image((2,1),[(0,0,0,255),(255,255,255,255)])
        self.assertEqual(levels.generate(image,'color','full')[1].getpixel((0,0)),(188,188,188,255))

    def test_data_filter_averages_channels_independently(self):
        image=self.image((2,1),[(0,0,0,0),(255,255,255,255)])
        self.assertEqual(levels.generate(image,'data','full')[1].getpixel((0,0)),(128,128,128,128))

    def test_transparent_color_does_not_bleed(self):
        image=self.image((2,1),[(255,0,0,255),(0,0,255,0)])
        self.assertEqual(levels.generate(image,'color','full')[1].getpixel((0,0)),(255,0,0,128))

    def test_all_transparent_footprint_is_deterministic(self):
        image=self.image((2,1),[(255,20,70,0),(0,255,255,0)])
        self.assertEqual(levels.generate(image,'color','full')[1].getpixel((0,0)),(0,0,0,0))

    def test_odd_edge_is_not_discarded(self):
        image=self.image((3,1),[(0,0,0,255),(0,0,0,255),(255,255,255,255)])
        self.assertEqual(levels.generate(image,'data','full')[1].getpixel((0,0)),(85,85,85,255))

    def test_exact_integer_footprints_cover_source_area(self):
        for source in range(1,40):
            target=max(1,source//2)
            self.assertTrue(all(sum(w for _,w in row)==source for row in levels.contributors(source,target)))

    def test_uniform_color_is_preserved_through_all_levels(self):
        image=images.pillow().new('RGBA',(17,9),(30,90,200,125))
        result=levels.generate(image,'color','full')
        self.assertTrue(all(set(im.get_flattened_data())=={(30,90,200,125)} for im in result))

    def test_normal_mips_are_normalized_and_linear(self):
        image=self.image((2,1),[(255,128,128,255),(128,128,255,255)]);result=levels.generate(image,'normal-xyz','full')
        normal=result[1].getpixel((0,0));self.assertGreater(normal[0],210);self.assertGreater(normal[2],210)
        vector=[v/127.5-1 for v in normal[:3]];self.assertAlmostEqual(sum(v*v for v in vector),1,delta=0.02)
        doc=textures.document('NORMAL',image,'normal-xyz','full')
        self.assertEqual(doc['21_FList']['1_this'][0]['36_&FLegacyColorSpace'],'1')

    def test_degenerate_base_normal_is_refused(self):
        image=images.pillow().new('RGBA',(1,1),(128,128,128,255))
        with self.assertRaises(FormatError):levels.generate(image,'normal-xyz','none')

    def test_cancelling_normals_are_not_replaced_with_arbitrary_z(self):
        image=self.image((2,1),[(255,128,128,255),(0,127,127,255)])
        with self.assertRaises(FormatError):levels.generate(image,'normal-xyz','full')

    def test_normal_alpha_is_not_used_as_color_opacity(self):
        image=self.image((2,1),[(128,128,255,0),(128,128,255,255)])
        self.assertEqual(levels.generate(image,'normal-xyz','full')[1].getpixel((0,0)),(128,128,255,128))

    def test_every_surface_matches_chain_dimensions(self):
        image=images.pillow().new('RGBA',(9,7),'blue');doc=textures.document('OWN',image,'color','full');tex=doc['21_FList']['1_this'][0]
        self.assertEqual(tex['33_FLevels'],'4');self.assertEqual(list(tex['40_FSurfaces']['1_scope']),['0_E','1_E','2_E','3_E'])
        self.assertEqual(tex['40_FSurfaces']['1_scope']['3_E']['20_FDescription.Width'],'1')

    def test_invalid_modes_and_dimensions_fail(self):
        for args in [(0,4,'full'),(1025,4,'full'),(4,4,'automatic')]:
            with self.assertRaises(FormatError):levels.dimensions(*args)


class RawMipStreamTests(unittest.TestCase):
    def test_raw_texture_mode_is_explicit_and_exact_sized(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp).resolve()/'raw.bin';file.write_bytes(bytes(range(32)));library=SimpleNamespace(path=file,data_offset=0,data_size=32)
            data=struct.pack('<6I',0,0,32,32,0,0);node=Node(data,0,24)
            with self.assertRaises(FormatError):clb_streams.read(library,node,32)
            actual,_=clb_streams.read(library,node,32,allow_uncompressed=True);self.assertEqual(actual,bytes(range(32)))
            data=struct.pack('<6I',0,0,32,31,0,0)
            with self.assertRaises(FormatError):clb_streams.read(library,Node(data,0,24),32,allow_uncompressed=True)


class MaterialRoleTests(unittest.TestCase):
    def document(self,name):
        return {'20_FName':'OWN','22_FDependencies':['SYSTEM.CLB'],'21_FList':{'1_this':[
            {'cid':str(0x41166f),'20_FName':'[OWN]MAT\\0','50_FEffectLink':{'20_FName':'[SYSTEM_EFFECTS]EFFECT\\0'},
             '51_FProperties':{'1_this':[{'cid':str(0x411695),'40_FName':name,'100_FTextureLink':{'cid':str(0x410634),'20_FName':'old'}}]}}]}}

    def test_default_normal_does_not_accept_srgb(self):
        data=struct.pack('<I',83);library=SimpleNamespace(members=[{'identity':{'locator':'[T]TX\\0'},'fields':{32:Node(data,0,4)}}])
        surface={'locator':'[T]TX\\0','color_space':2,'levels':[{'width':1,'height':1,'padded_pixels':bytes(8)}]}
        with patch.object(textures,'binary_surfaces',return_value=surface),patch.object(material_parameters.content_libraries,'read',return_value=library):
            with self.assertRaisesRegex(FormatError,'Linear'):material_parameters.derive(self.document('NormalTexture'),'[OWN]MAT\\0',
                [{'name':'NormalTexture','library':str(Path.cwd()/'test.clb'),'texture':'[T]TX\\0'}],{'NormalTexture':{'codec':'texture'}},Path.cwd())

    def test_default_diffuse_does_not_accept_linear_data(self):
        data=struct.pack('<I',83);library=SimpleNamespace(members=[{'identity':{'locator':'[T]TX\\0'},'fields':{32:Node(data,0,4)}}])
        surface={'locator':'[T]TX\\0','color_space':1,'levels':[{'width':1,'height':1,'padded_pixels':bytes(8)}]}
        with patch.object(textures,'binary_surfaces',return_value=surface),patch.object(material_parameters.content_libraries,'read',return_value=library):
            with self.assertRaisesRegex(FormatError,'sRGB'):material_parameters.derive(self.document('DiffuseTexture'),'[OWN]MAT\\0',
                [{'name':'DiffuseTexture','library':str(Path.cwd()/'test.clb'),'texture':'[T]TX\\0'}],{'DiffuseTexture':{'codec':'texture'}},Path.cwd())


if __name__=='__main__':unittest.main()
