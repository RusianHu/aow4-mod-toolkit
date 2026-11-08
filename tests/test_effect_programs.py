import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import clb_streams,content_libraries,effect_programs,native,native_effect_programs
from aow4mod.core import FormatError,sha
from test_aow4_mod_audit import mapping,typed,array,u32
from test_shaders import program


def literal_block(data):
    n=len(data);out=bytearray([min(15,n)<<4])
    if n>=15:
        n-=15
        while n>=255:out.append(255);n-=255
        out.append(n)
    return bytes(out)+data


def library(offset_delta=0,size_delta=0,wrong_stage=False,duplicate=False,mode=2,empty=False,features=None,feature_mask=None,feature_raw=None):
    pixel=program([],0);vertex=program([('POSITION',0,0,3,7,7)]);pool=pixel+vertex
    alternative=program([('COLOR',0,0,3,15,15)],0)
    if duplicate=='conflict':pool+=alternative
    encoded=literal_block(pool);strings=bytearray()
    def text(value):
        raw=value.encode();p=len(strings);strings.extend(raw+b'\0');return struct.pack('<II',len(raw),p)
    name=text('TEST');locator=text('[TEST]EFFECT\\0');pass_name=text('OPAQUE');expression=text('(BlendMode==0)')
    key0=bytes(64);key1=bytes([1])*64 if feature_mask is None else bytes([1])*32+feature_mask.to_bytes(32,'little')
    pixel_len=len(pixel);r0=key0+struct.pack('<II',offset_delta,pixel_len+size_delta)
    other=key0+struct.pack('<II',len(pixel)+len(vertex),len(alternative)) if duplicate=='conflict' else r0
    r1=key1+struct.pack('<II',0 if wrong_stage else pixel_len,len(vertex))
    counts={60:u32(0 if empty else 2 if duplicate else 1),61:b'' if empty else r0+other if duplicate else r0,
            62:u32(0 if empty else 1),63:b'' if empty else r1}
    p=typed(0x41166a,{0x41166a:mapping({0:pass_name,1:expression,**counts})})
    ref=struct.pack('<6I',0,mode,len(pool),len(encoded),0,0)
    extra={}
    if features is not None:extra[70]=u32(len(features))+b''.join(u32(len(n.encode()))+n.encode() for n in features)
    if feature_raw is not None:extra[70]=feature_raw
    effect=typed(0x411669,{20:locator,51:array([p]),72:u32(len(pool)),73:ref,**extra})
    metadata=mapping({19:u32(8),20:name,21:array([effect]),29:b'\1'});align=lambda b:bytes(b)+bytes(-len(b)%16)
    strings,metadata,data=align(strings),align(metadata),align(encoded)
    return content_libraries.SIGNATURE+struct.pack('<4I',8,len(strings),len(metadata),len(data))+bytes(8)+strings+metadata+data


class CLBShaderStreamTests(unittest.TestCase):
    def test_literal_extensions_and_overlapping_matches_decode_exactly(self):
        for data in (b'',b'abc',bytes(range(255))*3):self.assertEqual(clb_streams.lz4_block(literal_block(data),len(data)),data)
        # One literal, overlapping match of 12, five final literals.
        self.assertEqual(clb_streams.lz4_block(b'\x18a\x01\x00\x50bcdef',18),b'a'*13+b'bcdef')

    def test_bad_offsets_lengths_outputs_and_terminal_sequences_are_refused(self):
        cases=[(b'\xf0',20),(b'\x50abc',5),(b'\x10a\x00\x00',5),(b'\x10a\x02\x00',5),
               (b'\x10a\x01\x00',5),(b'\x30abc',4),(b'\x1fa\x01\x00\xff',40)]
        for data,size in cases:
            with self.subTest(data=data),self.assertRaises(FormatError):clb_streams.lz4_block(data,size)


class EffectProgramTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve();self.path=self.root/'TEST.clb';self.path.write_bytes(library())

    def test_lookup_records_map_exact_programs_and_profile_checks_use_selected_program(self):
        r=effect_programs.inspect(self.path,'[test]effect\\0',stage=1,profile='pnuv32')
        self.assertEqual(r['program_count'],2);self.assertEqual(r['lookup_count'],2);self.assertEqual(r['unreferenced_programs'],[])
        self.assertEqual(r['items'][0]['program_ordinal'],1);self.assertTrue(r['items'][0]['vertex_profile_check']['requirements_covered'])
        self.assertTrue(r['serialized_program_mapping_verified']);self.assertFalse(r['runtime_selection_verified'])
        key=r['items'][0]['descriptor_key'];self.assertEqual(effect_programs.inspect(self.path,'[TEST]EFFECT\\0',key=key)['total'],1)

    def test_wrong_container_boundary_size_stage_conflicting_keys_and_compression_are_refused(self):
        for kwargs in ({'offset_delta':1},{'size_delta':1},{'wrong_stage':True},{'duplicate':'conflict'},{'mode':7}):
            self.path.write_bytes(library(**kwargs))
            with self.subTest(kwargs=kwargs),self.assertRaises(FormatError):effect_programs.decode(self.path,'[TEST]EFFECT\\0')

    def test_identical_duplicate_records_preserve_order_and_all_native_witness_bytes(self):
        self.path.write_bytes(library(duplicate=True));r=effect_programs.inspect(self.path,'[TEST]EFFECT\\0',stage=0)
        self.assertEqual(r['lookup_count'],3);self.assertEqual(r['identical_duplicate_records'],1)
        self.assertEqual([x['duplicate_of'] for x in r['items']],[None,0])
        self.assertEqual(r['items'][0]['program_sha256'],r['items'][1]['program_sha256'])

    def test_global_feature_names_select_the_exact_program_and_keep_unknown_bits(self):
        self.path.write_bytes(library(features=['NOBONES','NOUV2','WIND'],feature_mask=3))
        r=effect_programs.inspect(self.path,'[TEST]EFFECT\\0',require=['nobones','NOUV2'],forbid=['WIND'],profile='pnuv32')
        self.assertEqual(r['total'],1);self.assertEqual(r['items'][0]['program_ordinal'],1)
        self.assertEqual(r['items'][0]['enabled_features'],['NOBONES','NOUV2'])
        self.assertEqual(r['profile_summary']['requirements_covered'],1)
        self.assertEqual(effect_programs.inspect(self.path,'[TEST]EFFECT\\0',require=['ABSENT'])['total'],0)
        with self.assertRaises(FormatError):effect_programs.inspect(self.path,'[TEST]EFFECT\\0',require=['wind'],forbid=['WIND'])
        self.path.write_bytes(library(features=['NOBONES'],feature_mask=1<<255))
        r=effect_programs.inspect(self.path,'[TEST]EFFECT\\0',stage=1)
        self.assertEqual(r['items'][0]['enabled_features'],[])
        self.assertEqual(int(r['items'][0]['unmapped_feature_bits'],16),1<<255)

    def test_feature_vector_bounds_encoding_and_ambiguous_names_are_refused(self):
        for raw in (b'',u32(257),u32(1),u32(1)+u32(9)+b'a',u32(1)+u32(1)+b'\xff',u32(1)+u32(1)+b'\0',u32(0)+b'x'):
            self.path.write_bytes(library(feature_raw=raw))
            with self.subTest(raw=raw),self.assertRaises(FormatError):effect_programs.decode(self.path,'[TEST]EFFECT\\0')
        self.path.write_bytes(library(features=['WIND','wind']))
        with self.assertRaises(FormatError):effect_programs.decode(self.path,'[TEST]EFFECT\\0')

    def test_empty_selections_and_empty_tables_do_not_certify_runtime_or_vertex_coverage(self):
        r=effect_programs.inspect(self.path,'[TEST]EFFECT\\0',pass_index=9,profile='pnuv32');self.assertTrue(r['selection_empty']);self.assertTrue(r['profile_summary']['zero_vertices_not_evaluated'])
        self.path.write_bytes(library(empty=True));r=effect_programs.inspect(self.path,'[TEST]EFFECT\\0')
        self.assertFalse(r['serialized_program_mapping_verified']);self.assertTrue(r['zero_lookup_not_evaluated']);self.assertEqual(len(r['unreferenced_programs']),2)
        with self.assertRaises(FormatError):effect_programs.inspect(self.path,'[TEST]EFFECT\\0',key='aa')

    def test_native_pool_and_tables_revalidate_against_clb_without_loading_dll(self):
        decoded=effect_programs.decode(self.path,'[TEST]EFFECT\\0');lib=content_libraries.read(self.path);pool,_=clb_streams.read(lib,lib.members[0]['fields'][73],lib.members[0]['fields'][72].uint(4));proof=self.root/'proof';proof.mkdir();(proof/'pool.bin').write_bytes(pool)
        _,wrapper=lib.members[0]['fields'][51].array()[0].typed();p=wrapper[0x41166a].mapping();tables=[]
        for stage in (0,1):
            name=f'pass-000-stage-{stage}.bin';raw=p[61+stage*2].raw();(proof/name).write_bytes(raw)
            tables.append({'pass':0,'stage':stage,'count':1,'file':name,'sha256':sha(raw)})
        report={'format':'aow4mod-native-effect-programs/1','sha256':decoded['sha256'],'locator':decoded['locator'],
                'pool_artifact':{'file':'pool.bin','sha256':sha(pool)},'tables':tables,'program_count':2,'lookup_count':2}
        native_effect_programs.validate(report,self.path,proof)
        v2={**report,'format':'aow4mod-native-effect-programs/2','feature_names':[]}
        native_effect_programs.validate(v2,self.path,proof)
        with self.assertRaises(FormatError):native_effect_programs.validate({**v2,'format':report['format']},self.path,proof)
        for change in ({'feature_names':['WIND']},{'feature_names':None}):
            with self.assertRaises(FormatError):native_effect_programs.validate({**v2,**change},self.path,proof)
        for change in ({'tables':tables[:1]},{'pool_artifact':{'file':'../bad','sha256':'0'*64}},{'program_count':1}):
            with self.assertRaises(FormatError):native_effect_programs.validate({**report,**change},self.path,proof)
        path=proof/tables[0]['file'];bad=bytearray(path.read_bytes());bad[0]^=1;path.write_bytes(bad);forged=copy.deepcopy(report);forged['tables'][0]['sha256']=sha(bad)
        with self.assertRaises(FormatError):native_effect_programs.validate(forged,self.path,proof)

    def test_native_payload_binds_locator_and_source_callback_evidence(self):
        params={'sha256':'s','language_sha256':'l','locator':'[TEST]EFFECT\\0'};payload={'sha256':'s','language_sha256':'l','locator':params['locator'],'callback_result':0,'content_present':True,'artifact_directory':'dir','pool_artifact':{},'tables':[],'feature_names':[]}
        native.validate_payload('effect-programs',payload,params)
        for change in ({'locator':'other'},{'content_present':False},{'tables':None},{'feature_names':None},{'feature_names':['a']*257},{'feature_names':[1]}):
            with self.assertRaises(FormatError):native.validate_payload('effect-programs',{**payload,**change},params)


if __name__=='__main__':unittest.main()
