import base64
import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import material_parameters as params,textures,publication,content_libraries,images
from aow4mod.core import FormatError
from test_aow4_mod_audit import mapping,typed,array,u32


def texture_binary(width=3,height=2,levels=1,pixels=None):
    pixels=pixels or bytes(range(((width*4+7)//8*8)*height))
    packed=bytes([0xf0,len(pixels)-15])+pixels
    strings=bytearray()
    def text(value):
        raw=value.encode();start=len(strings);strings.extend(raw+b'\0');return struct.pack('<II',len(raw),start)
    surface=typed(0x410024,{20:u32(width),21:u32(height),22:struct.pack('<6I',0,2,len(pixels),len(packed),0,0),23:u32(83),24:b'\x20',25:b'\1'})
    member=typed(0x41003d,{20:text('[TEX]TX\\0'),21:text('Texture'),30:u32(width),31:u32(height),32:u32(0x20053),33:u32(levels),34:u32(2),35:b'\0',36:u32(2),40:array([surface])})
    metadata=mapping({19:u32(8),20:text('TEX'),21:array([member]),28:b'\x01\x01\0\0'})
    align=lambda data:bytes(data)+bytes(-len(data)%16)
    strings,metadata,data=map(align,(strings,metadata,packed))
    return content_libraries.SIGNATURE+struct.pack('<4I',8,len(strings),len(metadata),len(data))+bytes(8)+strings+metadata+data


class TextureBinaryTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve();self.path=self.root/'TEX.clb'

    def test_binary_pixels_include_odd_width_alignment(self):
        self.path.write_bytes(texture_binary());actual=textures.binary_surface(self.path,'[TEX]TX\\0')
        self.assertEqual(actual['padded_pixels'],bytes(range(32)));self.assertEqual((actual['width'],actual['height']),(3,2))

    def test_multiple_mips_are_not_misrepresented_as_single_surface(self):
        self.path.write_bytes(texture_binary(levels=2))
        with self.assertRaises(FormatError):textures.binary_surface(self.path,'[TEX]TX\\0')

    def test_wrong_locator_is_refused(self):
        self.path.write_bytes(texture_binary())
        with self.assertRaises(FormatError):textures.binary_surface(self.path,'[TEX]TXA\\0')

    def test_corrupt_pixel_stream_is_refused(self):
        self.path.write_bytes(texture_binary());data=bytearray(self.path.read_bytes());library=content_libraries.read(self.path)
        data[library.data_offset+1]=255;self.path.write_bytes(data)
        with self.assertRaises(FormatError):textures.binary_surface(self.path,'[TEX]TX\\0')

    def test_texture_document_keeps_actual_size_without_atlas_packing(self):
        class Image:
            width=3;height=2
            size=(3,2)
            def tobytes(self):return bytes(range(24))
        doc=textures.document('OWN',Image());row=doc['21_FList']['1_this'][0]
        self.assertEqual((row['30_FWidth'],row['31_FHeight'],row['33_FLevels']),('3','2','1'))
        self.assertEqual(row['20_FName'],'[OWN]TX\\0')
        surface=row['40_FSurfaces']['1_scope']['0_E']
        self.assertEqual(len(base64.b64decode(surface['22_FStream'])),32)


def effect_binary(mismatch=False,duplicate=False):
    strings=bytearray()
    def text(value):
        raw=value.encode();start=len(strings);strings.extend(raw+b'\0');return struct.pack('<II',len(raw),start)
    declarations=[]
    for name,code,editor in [('RGBA',0x8010c,0x4134c8),('RGB',0x8010b,0x4134c8),('Flag',0x80121,0x401607)]:
        declarations.append(typed(0x411694,{40:text('RGB' if duplicate else name),41:text(name),44:u32(0x401607 if mismatch else editor),101:u32(code)}))
    member=typed(0x411669,{20:text('[EFF]EFFECT\\0'),21:text('Effect'),53:array(declarations)})
    metadata=mapping({19:u32(8),20:text('EFF'),21:array([member]),28:b'\x01\x01\0\0'});align=lambda b:bytes(b)+bytes(-len(b)%16)
    strings,metadata,data=map(align,(strings,metadata,b'opaque'))
    return content_libraries.SIGNATURE+struct.pack('<4I',8,len(strings),len(metadata),len(data))+bytes(8)+strings+metadata+data


class EffectDeclarationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.path=Path(tmp.name).resolve()/'EFF.clb'

    def test_rgb_and_rgba_share_editor_but_not_component_count(self):
        self.path.write_bytes(effect_binary());d=params.declarations(self.path,'[EFF]EFFECT\\0')
        self.assertEqual((d['RGB']['lanes'],d['RGBA']['lanes']),(3,4));self.assertEqual(d['Flag']['codec'],'bool')

    def test_editor_and_data_type_must_both_match(self):
        self.path.write_bytes(effect_binary(mismatch=True));d=params.declarations(self.path,'[EFF]EFFECT\\0')
        self.assertIsNone(d['RGB']['codec']);self.assertIsNone(d['RGBA']['codec'])

    def test_duplicate_declaration_names_are_rejected(self):
        self.path.write_bytes(effect_binary(duplicate=True))
        with self.assertRaises(FormatError):params.declarations(self.path,'[EFF]EFFECT\\0')


class MaterialConstantTests(unittest.TestCase):
    def test_bool_uses_integer_bits_and_preserves_unused_lanes(self):
        d={'codec':'bool','lanes':1};raw=struct.pack('<I',1)+bytes(range(12))
        self.assertTrue(params.decode_constant(raw,d));self.assertEqual(params.encode_constant(raw,False,d),b'\0'*4+raw[4:])

    def test_bool_rejects_float_one_and_integer_request(self):
        d={'codec':'bool','lanes':1}
        with self.assertRaises(FormatError):params.decode_constant(struct.pack('<4f',1,0,0,0),d)
        with self.assertRaises(FormatError):params.encode_constant(bytes(16),1,d)

    def test_rgb_preserves_fourth_lane(self):
        d={'codec':'float','lanes':3};raw=struct.pack('<4f',1,1,1,0.75);after=params.encode_constant(raw,[0.2,0.3,0.4],d)
        self.assertEqual(after[12:],raw[12:]);self.assertEqual(len(params.decode_constant(after,d)),3)

    def test_numeric_overflow_underflow_nonfinite_and_bool_rejected(self):
        d={'codec':'float','lanes':1}
        for value in (True,float('nan'),float('inf'),1e50,1e-70):
            with self.subTest(value=value),self.assertRaises(FormatError):params.encode_constant(bytes(16),value,d)

    def test_vector_size_is_declared_not_guessed_from_editor(self):
        d={'codec':'float','lanes':3}
        with self.assertRaises(FormatError):params.encode_constant(bytes(16),[1,2,3,4],d)

    def document(self):
        return {'20_FName':'OWN','22_FDependencies':['SYSTEM.CLB'],'21_FList':{'1_this':[
            {'cid':str(0x41166f),'20_FName':'[OWN]MAT\\0','keep':'unchanged','51_FProperties':{'1_this':[
                {'cid':str(0x411696),'40_FName':'Color','100_&FConstant':base64.b64encode(struct.pack('<4f',1,1,1,0.75)).decode()}]}}]}}

    def test_derive_preserves_every_unrequested_field(self):
        before=self.document();d,changes,targets,imports=params.derive(before,'[OWN]MAT\\0',[{'name':'Color','value':[0,0.5,1]}],{'Color':{'codec':'float','lanes':3}},Path.cwd())
        self.assertEqual(params.material(d,'[OWN]MAT\\0')['keep'],'unchanged');self.assertEqual(before,self.document())
        self.assertEqual(changes[0]['after'],[0,0.5,1]);self.assertEqual(targets,[]);self.assertIsNone(imports)

    def test_duplicate_case_and_dynamic_expressions_are_not_silently_replaced(self):
        definitions={'Color':{'codec':'float','lanes':3}};before=self.document()
        cases=[[{'name':'color','value':[0,0,0]}],[{'name':'Color','value':[0,0,0]}]*2]
        for requests in cases:
            with self.assertRaises(FormatError):params.derive(before,'[OWN]MAT\\0',requests,definitions,Path.cwd())
        before['21_FList']['1_this'][0]['51_FProperties']['1_this'][0]['cid']='12345'
        with self.assertRaises(FormatError):params.derive(before,'[OWN]MAT\\0',[{'name':'Color','value':[0,0,0]}],definitions,Path.cwd())

    def test_noop_does_not_create_an_unnecessary_plan(self):
        with self.assertRaises(FormatError):params.derive(self.document(),'[OWN]MAT\\0',[{'name':'Color','value':[1,1,1]}],{'Color':{'codec':'float','lanes':3}},Path.cwd())


class MaterialDraftPolicyTests(unittest.TestCase):
    def manifest(self):
        return {'source_files':{'SourceMaterials/OWN/.aow4mod-material.json':{'sha256':'receipt'}},
                'draft_material_assets':[{'file':'Libraries/OWN.clb','source_receipt':'SourceMaterials/OWN/.aow4mod-material.json','source_receipt_sha256':'receipt'}],
                'release_gate':{'review_required':True,'issues':[{'issue':'draft_material_asset_requires_texture_variant_review','file':'Libraries/OWN.clb'}]}}

    def test_policy_carries_unverified_texture_variant_scope(self):
        publication.check_material_drafts(self.manifest(),['Libraries/OWN.clb'])

    def test_omitted_provenance_is_rejected(self):
        m=self.manifest();m['draft_material_assets']=[]
        with self.assertRaises(FormatError):publication.check_material_drafts(m,['Libraries/OWN.clb'])

    def test_missing_review_gate_is_rejected(self):
        m=self.manifest();m['release_gate']['issues']=[]
        with self.assertRaises(FormatError):publication.check_material_drafts(m,['Libraries/OWN.clb'])

    def test_rebound_source_receipt_is_rejected(self):
        m=self.manifest();m['draft_material_assets'][0]['source_receipt_sha256']='other'
        with self.assertRaises(FormatError):publication.check_material_drafts(m,['Libraries/OWN.clb'])


if __name__=='__main__':unittest.main()
