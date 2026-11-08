import base64
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import asset_yaml, content_libraries, images, native
from aow4mod.core import FormatError, sha
from aow4mod.filesystem import file_hash, inventory
from test_aow4_mod_audit import acp, resource, mapping, typed, array, u32


def atlas_bytes(name):
    strings=bytearray()
    def text(value):
        raw=value.encode();offset=len(strings);strings.extend(raw+b'\0');return struct.pack('<II',len(raw),offset)
    title=text(name);locator=text(f'[{name}]TXA\\0');label=text(name)
    member=typed(0x413403,{0x14:locator,0x15:label,0x413403:mapping({})})
    metadata=mapping({0x13:u32(8),0x14:title,0x15:array([member]),0x18:array([]),0x1c:array([])})
    align=lambda x:bytes(x)+bytes((-len(x))%16)
    strings,metadata,pixels=map(align,[strings,metadata,b'synthetic binary payload'])
    return content_libraries.SIGNATURE+struct.pack('<4I',8,len(strings),len(metadata),len(pixels))+bytes(8)+strings+metadata+pixels


@unittest.skipUnless(importlib.util.find_spec('PIL') and importlib.util.find_spec('yaml'),'optional assets dependencies')
class ImageTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.Image=Image;temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.source=self.root/'odd.png'
        self.image=Image.new('RGBA',(17,3));self.image.putdata([(i*3%256,i*7%256,i*11%256,i*13%256) for i in range(51)])
        self.image.save(self.source)

    def test_rgba_png_palette_and_half_alpha_are_preserved(self):
        image,proof=images.read_png(self.source);self.assertEqual(image.tobytes(),self.image.tobytes())
        self.assertEqual(proof['rgba_sha256'],sha(self.image.tobytes()))
        palette=self.Image.new('P',(3,1));palette.putpalette([255,0,0,0,255,0,0,0,255]+[0]*759)
        palette.putdata([0,1,2]);palette.save(self.root/'palette.png',transparency=bytes([0,128,255]))
        image,_=images.read_png(self.root/'palette.png')
        self.assertEqual(image.tobytes(),bytes([255,0,0,0,0,255,0,128,0,0,255,255]))

    def test_large_high_bit_depth_animated_and_color_managed_pngs_are_refused(self):
        self.Image.new('L',(1025,1)).save(self.root/'large.png')
        self.Image.new('I;16',(1,1)).save(self.root/'16bit.png')
        self.image.save(self.root/'profile.png',icc_profile=b'unsupported profile')
        self.image.save(self.root/'animated.png',save_all=True,append_images=[self.Image.new('RGBA',self.image.size,'red')],duration=100)
        for name in ['large.png','16bit.png','profile.png','animated.png']:
            with self.subTest(name=name),self.assertRaises(FormatError):images.read_png(self.root/name)

    def test_surface_rows_use_eight_byte_alignment_including_odd_widths(self):
        for width in [1,2,3,7,8,9,16,17,31,32,33,1024]:
            image=self.Image.new('RGBA',(width,3),(25,128,254,11));raw=images.padded_rgba(image)
            self.assertEqual(len(raw),((width*4+7)//8*8)*3)
            decoded,_=images.unpack_surface(images.surface(width,3,raw));self.assertEqual(decoded.tobytes(),image.tobytes())
        with self.assertRaises(FormatError):images.surface(17,3,bytes(240))
        with self.assertRaises(FormatError):images.surface(True,3,bytes(24))

    def test_layout_is_deterministic_nonoverlapping_and_bounds_every_border(self):
        sizes=[(7,5),(17,3),(3,9),(1,1)];a=images.layout(sizes);self.assertEqual(a,images.layout(sizes))
        width,height,positions=a;occupied=set()
        for (w,h),(x,y) in zip(sizes,positions):
            self.assertGreaterEqual(x,1);self.assertGreaterEqual(y,1);self.assertLess(x+w,width);self.assertLess(y+h,height)
            cells={(px,py) for px in range(x-1,x+w+1) for py in range(y-1,y+h+1)}
            self.assertFalse(cells&occupied);occupied|=cells
        for invalid in [[],[(1,1)]*65,[(0,1)],[(1024,1024)]*4]:
            with self.subTest(invalid=invalid[:2]),self.assertRaises(FormatError):images.layout(invalid)

    def test_document_geometry_borders_source_pixels_and_alpha_mask(self):
        doc,canvas,placements=images.document('CLI_TEST',[self.image],['odd.png']);item=placements[0]
        crop=canvas.crop((item['x'],item['y'],item['x']+17,item['y']+3));self.assertEqual(crop.tobytes(),self.image.tobytes())
        self.assertEqual(canvas.getpixel((item['x']-1,item['y']-1)),self.image.getpixel((0,0)))
        scope=doc['21_FList']['1_this'][0]['4273155_scope'];layer=scope['2_FLayers']['1_ECAST_PTR_OBJ(FLayers[Cnt])']
        mask=base64.b64decode(layer['1_FCustomData']['4199426_scope']['1_FStreamHandle'])
        self.assertTrue(all(bool(mask[i//8]&(1<<(i%8)))==bool(alpha) for i,alpha in enumerate(canvas.getchannel('A').tobytes())))
        for names in [['a.png','A.png'],['../bad.png']]:
            with self.assertRaises(FormatError):images.document('CLI_TEST',[self.image]*len(names),names)

    def test_native_png_must_match_pixels_even_when_worker_claims_success(self):
        value=images.surface(17,3,images.padded_rgba(self.image));wrong=self.root/'wrong.png'
        self.Image.new('RGBA',(17,3),'red').save(wrong)
        result={'payload':{'output_file':str(wrong),'output_sha256':file_hash(wrong)}}
        with patch('aow4mod.images.native.run_worker',return_value=result):
            with self.assertRaisesRegex(FormatError,'changed dimensions or RGBA'):images.native_png(value,self.root,self.root,self.root/'proof')

    def test_namespace_identity_reads_only_root_and_rejects_collisions(self):
        path=self.root/'unrelated_filename.clb';path.write_bytes(atlas_bytes('EXISTING'))
        with patch('aow4mod.content_libraries.read',side_effect=AssertionError('full parser not needed')):
            self.assertEqual(content_libraries.identity(path)['name'],'EXISTING')
            with self.assertRaisesRegex(FormatError,'already exists'):images.namespace_check('EXISTING',self.root,self.root/'game')


@unittest.skipUnless(importlib.util.find_spec('PIL') and importlib.util.find_spec('yaml'),'optional assets dependencies')
class ImagePlanTests(unittest.TestCase):
    # Integration fixtures emulate native transport, while validators independently parse
    # real synthetic CLB envelopes and decode PNG witnesses.
    def setUp(self):
        ImageTests.setUp(self);self.mod=self.root/'mod';self.mod.mkdir();(self.mod/'mod.acp').write_bytes(acp(resource()))
        self.game=self.root/'game';(self.game/'Content').mkdir(parents=True)
        self.binding={'game':str(self.game),'dll_sha256':next(iter(native.PROFILES)),
            'backend_sha256':native.backend_fingerprint(),'profile':next(iter(native.PROFILES.values()))}
        self.public=native.public_binding(self.binding);self.counter=0
        for target,function in [('native.profile_for',lambda game:self.binding),
            ('native_assets.compile_library',self.compile),('native_assets.export_library',self.export),('native_png',self.png)]:
            mock=patch('aow4mod.images.'+target,side_effect=function);mock.start();self.addCleanup(mock.stop)

    def compile(self,path,game,output,*args):
        doc=asset_yaml.load(Path(path).read_bytes());name=doc['20_FName'];output=Path(output);output.mkdir()
        binary=output/(name+'.clb');binary.write_bytes(atlas_bytes(name));text=output/(name+'.yaml');text.write_text(asset_yaml.dump(doc), encoding='utf-8')
        return {'format':'aow4mod-native-asset-compile/1','binding':self.public,'source_sha256':file_hash(path),
            'output':str(output),'files':[{'name':p.name,'sha256':file_hash(p)} for p in [binary,text]],
            'yaml_fields_preserved':True,'member_identities_preserved':True,'diagnostics':[]}

    def export(self,path,game,output,*args):
        output=Path(output);output.mkdir();binary=output/Path(path).name;shutil.copyfile(path,binary)
        source_yaml=next(Path(path).parent.glob('*.yaml'));text=output/source_yaml.name;shutil.copyfile(source_yaml,text)
        return {'binding':self.public,'source_sha256':file_hash(path),'output':str(output),
            'comparison':content_libraries.compare(path,binary),
            'files':[{'name':p.name,'sha256':file_hash(p)} for p in [binary,text]],'diagnostics':[]}

    def png(self,value,game,cache,work,*args):
        work=Path(work);work.mkdir();image,data=images.unpack_surface(value)
        packet=work/'surface.bin';packet.write_bytes(data);png=work/'native.png';image.save(png)
        return {'binding':self.public,'input':str(packet),'input_sha256':sha(data),'width':image.width,'height':image.height,
            'png_file':str(png),'png_sha256':file_hash(png),'rgba_sha256':sha(image.tobytes()),'pixels_equal':True,'diagnostics':[]}

    def plan(self):return images.make_plan(self.mod,[self.source],'CLI_TEST',self.game,self.root/'cache')

    def test_apply_preserves_acp_and_author_png_and_rechecks_witnesses(self):
        plan=self.plan();before=inventory(self.mod);output=self.root/'staged';result=images.apply_plan(plan,output)
        self.assertEqual((output/'mod.acp').read_bytes(),(self.mod/'mod.acp').read_bytes())
        self.assertEqual(file_hash(output/plan['sprites'][0]['source_relative']),file_hash(self.source))
        self.assertEqual(inventory(self.mod),before);self.assertTrue(result['native_pixels_verified'])

    def test_source_mod_and_input_mutations_block_apply(self):
        plan=self.plan();self.image.save(self.source,icc_profile=b'new metadata')
        with self.assertRaisesRegex(FormatError,'PNG source changed'):images.apply_plan(plan,self.root/'bad-input')
        (self.mod/'new.bin').write_bytes(b'changed')
        with self.assertRaisesRegex(FormatError,'Mod changed'):images.apply_plan(plan,self.root/'bad-mod')

    def test_plan_layout_and_native_witness_mutations_are_refused(self):
        plan=self.plan();bad=copy.deepcopy(plan);bad['sprites'][0]['x']+=1
        with self.assertRaisesRegex(FormatError,'layout'):images.apply_plan(bad,self.root/'bad-layout')
        Path(plan['atlas_native_png']['png_file']).write_bytes(b'changed')
        with self.assertRaisesRegex(FormatError,'witness files changed'):images.apply_plan(plan,self.root/'bad-proof')

    def test_existing_author_source_is_not_overwritten_on_atomic_failure(self):
        destination=self.mod/'SourceImages/CLI_TEST/odd.png';destination.parent.mkdir(parents=True);destination.write_bytes(b'author work')
        plan=self.plan();output=self.root/'bad-output'
        with self.assertRaisesRegex(FormatError,'different author data'):images.apply_plan(plan,output)
        self.assertFalse(output.exists());self.assertEqual(destination.read_bytes(),b'author work')


if __name__=='__main__':unittest.main()
