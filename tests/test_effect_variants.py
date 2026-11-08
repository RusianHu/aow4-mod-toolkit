from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import content_libraries,effect_variants
from aow4mod.core import FormatError
from test_aow4_mod_audit import mapping,typed,array,u32


def library(duplicate=False):
    pool=bytearray()
    def text(value):
        raw=value.encode();offset=len(pool);pool.extend(raw+b'\0');return struct.pack('<II',len(raw),offset)
    root_name=text('TEST');features=[('NOBONES',256),('NOUV2',16384),('UV2',32768),('BONESW4',1024)]
    if duplicate:features.append(('nobones',512))
    values=[typed(0x411677,{40:text(name),41:u32(mask)}) for name,mask in features]
    codes=[typed(0x411667,{31:u32(mask)}) for mask in (256|16384,256|32768,1024|16384,256|16384|0x100000)]
    function=typed(0x411665,{30:mapping({2:mapping(dict(enumerate(codes)))}),31:text('MainVS'),32:u32(1),33:array(values),34:u32(1)})
    effect=typed(0x411669,{20:text('[TEST]EFFECT\\0'),50:array([function])})
    metadata=mapping({19:u32(8),20:root_name,21:array([effect]),29:b'\1'})
    align=lambda raw:bytes(raw)+bytes(-len(raw)%16)
    pool,metadata=align(pool),align(metadata)
    return content_libraries.SIGNATURE+struct.pack('<IIII',8,len(pool),len(metadata),0)+bytes(8)+pool+metadata


class EffectVariantsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.path=Path(temp.name).resolve()/'TEST.clb';self.path.write_bytes(library())

    def test_function_specific_masks_are_filtered_and_unmapped_bits_stay_visible(self):
        result=effect_variants.inspect(self.path,'[test]effect\\0',1,1,['nobones','nouv2'],[],1)
        self.assertEqual(result['total'],2);self.assertEqual(result['next_offset'],1)
        next_page=effect_variants.inspect(self.path,'[TEST]EFFECT\\0',1,1,['NOBONES','NOUV2'],[],10,1)
        self.assertEqual(next_page['items'][0]['unmapped_mask_bits'],0x100000)
        self.assertFalse(result['runtime_selection_verified']);self.assertFalse(result['program_pool_mapping_verified'])

    def test_stage_pass_and_missing_required_feature_do_not_select_unrelated_functions(self):
        for kw in ({'stage':0},{'pass_id':99},{'require':['MISSING']}):
            with self.subTest(kw=kw):self.assertEqual(effect_variants.inspect(self.path,'[TEST]EFFECT\\0',**kw)['total'],0)

    def test_forbidden_and_conflicting_requests_are_explicit(self):
        result=effect_variants.inspect(self.path,'[TEST]EFFECT\\0',require=['NOBONES'],forbid=['NOUV2'])
        self.assertEqual(result['total'],1);self.assertIn('UV2',result['items'][0]['enabled_features'])
        with self.assertRaisesRegex(FormatError,'required and forbidden'):
            effect_variants.inspect(self.path,'[TEST]EFFECT\\0',require=['NOBONES'],forbid=['nobones'])

    def test_ambiguous_feature_names_and_missing_effect_are_refused(self):
        self.path.write_bytes(library(True))
        with self.assertRaisesRegex(FormatError,'Ambiguous'):effect_variants.inspect(self.path,'[TEST]EFFECT\\0')
        with self.assertRaisesRegex(FormatError,'Select one'):effect_variants.inspect(self.path,'[TEST]EFFECT\\9')


if __name__=='__main__':unittest.main()
