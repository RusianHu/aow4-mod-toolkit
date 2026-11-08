from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import maps,native,native_maps
from aow4mod.core import FormatError
from aow4mod.filesystem import file_hash
from test_aow4_mod_audit import mapping,typed,u32


def map_bytes(world=b'opaque terrain/entities',extra=False):
    header=mapping({0:typed(0x474225,{0x400006a:mapping({}),0x460009:mapping({})})})
    fields={0x14:world,0x460003:mapping({}),0x470037:mapping({})}
    if extra:fields[0x4c]=b'added data'
    return struct.pack('<4s8I',b'GMP\0',36,1,len(header),0x470037,0,0,0,0)+header+mapping(fields)


class MapEnvelopeTests(unittest.TestCase):
    def test_explicit_header_length_partitions_header_and_map(self):
        raw=map_bytes();decoded=maps.decode(raw)
        self.assertEqual(decoded['map_class'],0x470037);self.assertEqual(decoded['header_class'],0x474225)
        self.assertEqual(decoded['map'][0x14].raw(),b'opaque terrain/entities')

    def test_unknown_envelope_class_reserved_and_truncations_are_refused(self):
        raw=map_bytes();cases=[raw[:16],raw[:36],raw[:-1]]
        for offset,value in ((4,40),(8,2),(12,0xffffffff),(16,0x460005),(20,1)):
            bad=bytearray(raw);struct.pack_into('<I',bad,offset,value);cases.append(bytes(bad))
        for bad in cases:
            with self.subTest(bytes=len(bad)),self.assertRaises(FormatError):maps.decode(bad)

    def test_comparison_does_not_ignore_opaque_or_added_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            a=Path(directory).resolve()/'a.tam';b=Path(directory).resolve()/'b.tam';a.write_bytes(map_bytes());b.write_bytes(map_bytes())
            self.assertTrue(maps.compare(a,b)['all_compared_data_equal'])
            b.write_bytes(map_bytes(b'changed world',True));result=maps.compare(a,b)
            self.assertEqual(result['changed_map_fields'],['0x14','0x4C']);self.assertFalse(result['all_compared_data_equal'])

    def test_native_receipt_requires_real_owner_saved_artifacts_and_source_binding(self):
        p={'sha256':'input','language_sha256':'language'}
        good={**p,'map_class':'00470037','save_result':0,'callback_result':0,'content_present':True,'artifacts':{k:{} for k in ('before','after','saved')}}
        native.validate_payload('map-roundtrip',good,p)
        for key,value in (('map_class','OTHER'),('artifacts',{}),('sha256','changed'),('save_result',1)):
            with self.subTest(key=key),self.assertRaises(FormatError):native.validate_payload('map-roundtrip',{**good,key:value},p)

    def test_native_map_context_must_match_the_requested_libraries_and_packs(self):
        p={'sha256':'input','language_sha256':'language','libraries':[{'file':'local.clb','sha256':'hash'}],
           'packs':[{'file':'local.rpk','name':'LOCAL.RPK','sha256':'hash'}]}
        good={**p,'map_class':'00470037','save_result':0,'callback_result':0,'content_present':True,
              'context_libraries':p['libraries'],'context_packs':p['packs'],'artifacts':{k:{} for k in ('before','after','saved')}}
        native.validate_payload('map-roundtrip',good,p)
        for key in ('context_libraries','context_packs'):
            with self.subTest(key=key),self.assertRaisesRegex(FormatError,'lookup context'):native.validate_payload('map-roundtrip',{**good,key:[]},p)


class MapRoundtripTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        (self.root/'inputs').mkdir();self.source=self.root/'inputs/map.tam';self.source.write_bytes(map_bytes())
        self.game=self.root/'game';(self.game/'Language/EN').mkdir(parents=True);(self.game/'Language/EN/EN.MO').write_bytes(b'lang');(self.game/native.DLL).write_bytes(b'dll')
        self.output=self.root/'output';self.calls=0;self.modify_first=False;self.bad_body=False;self.requests=[]
        self.binding={'game':str(self.game),'dll_sha256':'fixture','backend_sha256':'fixture','profile':{'name':'fixture'}}
        mocked=patch.object(native,'run_worker',side_effect=self.worker);mocked.start();self.addCleanup(mocked.stop)
        mocked=patch.object(native_maps.native_validation,'diagnostics',return_value=[]);mocked.start();self.addCleanup(mocked.stop)

    def worker(self,game,operation,params,cache,timeout):
        self.requests.append(params)
        self.calls+=1;run=self.root/f'run-{self.calls}';run.mkdir();data=Path(params['path']).read_bytes()
        if self.modify_first and self.calls==1:data=map_bytes(b'normalized world')
        body=maps.decode(data)['body'].raw();artifacts={}
        for key,value in (('before',body),('after',b'changed object' if self.bad_body else body),('saved',data)):
            path=run/('saved.tam' if key=='saved' else key+'.bin');path.write_bytes(value);artifacts[key]={'file':str(path),'sha256':file_hash(path)}
        return {'binding':self.binding,'run':str(run),'payload':{'artifacts':artifacts}}

    def test_two_process_file_witnesses_preserve_source_and_saved_data(self):
        before=self.source.read_bytes();result=native_maps.run(self.source,self.game,self.output,self.root/'cache')
        self.assertEqual(self.calls,2);self.assertEqual(before,self.source.read_bytes());self.assertEqual(result['status'],'data_preserved')
        self.assertTrue(result['independent_reload_comparison']['all_compared_data_equal'])

    def test_stable_second_save_does_not_erase_original_normalization_finding(self):
        self.modify_first=True;result=native_maps.run(self.source,self.game,self.output,self.root/'cache')
        self.assertEqual(result['status'],'review_required');self.assertFalse(result['comparison']['all_compared_data_equal'])
        self.assertTrue(result['independent_reload_comparison']['all_compared_data_equal'])

    def test_changed_native_body_aborts_new_diagnostic_output(self):
        self.bad_body=True
        with self.assertRaisesRegex(FormatError,'object changed'):native_maps.run(self.source,self.game,self.output,self.root/'cache')
        self.assertFalse(self.output.exists())

    def test_both_native_file_reads_receive_identical_bound_lookup_inputs(self):
        from test_content_libraries import library_bytes
        from test_aow4_mod_audit import rpk,resource
        folder=self.root/'context';folder.mkdir();library=folder/'local.clb';library.write_bytes(library_bytes())
        pack=folder/'local.rpk';pack.write_bytes(rpk([resource()]))
        result=native_maps.run(self.source,self.game,self.output,self.root/'cache',libraries=[library],packs=[pack])
        self.assertEqual(self.requests[0]['libraries'],self.requests[1]['libraries'])
        self.assertEqual(self.requests[0]['packs'],self.requests[1]['packs'])
        self.assertEqual(result['context_libraries'],self.requests[0]['libraries'])
        self.assertEqual(result['context_packs'],self.requests[0]['packs'])
        self.assertEqual(result['context_packs'][0]['sha256'],file_hash(pack))


if __name__=='__main__':unittest.main()
