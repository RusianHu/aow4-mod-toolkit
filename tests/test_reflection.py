"""Independent machine-code fixtures and synthetic schema evidence; no game data."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import reflection,semantics,editing
from aow4mod.core import FormatError,Node,Resource,sha,read_acp
from test_aow4_mod_audit import acp,resource,u32

try:
    import capstone
except ImportError:capstone=None


class FakeImage:
    base=0x140000000
    def __init__(self,code):
        self.code=bytes.fromhex(code)
        self.md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64);self.md.detail=True
    def read(self,rva,size):return self.code[rva:rva+size]


@unittest.skipUnless(capstone,'analysis extra is optional')
class TrackerTests(unittest.TestCase):
    def events(self,code):
        image=FakeImage(code)
        return reflection.events(image,0,len(image.code))

    def test_integer_serializer_tracks_member_archive_and_stack_arguments(self):
        # save this/archive, member=this+0x170, width4/default2, field1/label pointer.
        code='48 89 cf 48 89 d6 48 83 ec 38 48 8d 97 70 01 00 00 48 89 f1 48 8b 06 '
        code+='41 b8 04 00 00 00 41 b9 02 00 00 00 c7 44 24 20 01 00 00 00 '
        code+='4c 8d 15 20 00 00 00 4c 89 54 24 28 ff 50 40 c3'
        event=self.events(code)[0]
        self.assertEqual(event['slot'],0x40)
        self.assertEqual(event['args']['rdx'],('member',0x170))
        self.assertEqual(reflection.integer(event['args']['r9'],32),2)
        self.assertEqual(reflection.integer(event['stack'][0x20],32),1)
        self.assertEqual(event['stack'][0x28][0],'rva')
        self.assertFalse(event['conditional'])

    def test_partial_registers_do_not_invent_known_upper_bits(self):
        for code in ('b0 02 89 c0 49 89 c1 e8 00 00 00 00',
                     'b4 02 49 89 c1 e8 00 00 00 00',
                     'b0 02 83 c0 01 49 89 c1 e8 00 00 00 00'):
            event=self.events(code)[0]
            self.assertIsNone(reflection.integer(event['args']['r9'],32))
        event=self.events('b0 02 44 0f b6 c8 e8 00 00 00 00')[0]
        self.assertEqual(reflection.integer(event['args']['r9'],32),2)

    def test_calls_clobber_volatile_registers_and_outgoing_stack(self):
        events=self.events('41 b9 02 00 00 00 c7 44 24 20 01 00 00 00 e8 00 00 00 00 e8 00 00 00 00')
        self.assertIsNone(events[1]['args']['r9'])
        self.assertIsNone(events[1]['stack'][0x20])

    def test_overlapping_stack_write_invalidates_previous_value(self):
        event=self.events('c7 44 24 20 01 00 00 00 c6 44 24 21 02 e8 00 00 00 00')[0]
        self.assertIsNone(event['stack'][0x20])

    def test_conditional_paths_are_not_straight_line_evidence(self):
        self.assertTrue(self.events('85 c0 74 02 90 90 e8 00 00 00 00')[0]['conditional'])
        self.assertTrue(self.events('eb 02 90 90 e8 00 00 00 00')[0]['conditional'])
        self.assertTrue(self.events('c3 e8 00 00 00 00')[0]['conditional'])

    def test_byte_stack_write_cannot_prove_a_32_bit_argument(self):
        event=self.events('c6 44 24 20 01 e8 00 00 00 00')[0]
        self.assertIsNone(reflection.integer(event['stack'][0x20],32))


class SchemaTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve()
        self.game=self.root/'game';self.game.mkdir();(self.game/'ResourceED.exe').write_bytes(b'synthetic tool')
        self.tool_hash=sha(b'synthetic tool');self.path=self.root/'schema.json'
        self.field={'name':'ExampleInteger','storage':'integer','width':4,'default_hex':'ffffffff',
                    'storage_validated':True,'confidence':'method_and_corpus','observed':2}
        self.schema={'format':reflection.FORMAT,'tool_sha256':self.tool_hash,
                     'extractor_sha256':sha(Path(reflection.__file__).read_bytes()),
                     'fields':{'0x00471C6D/0x01':self.field},'types':{},'enums':{},'summary':{},'coverage':'fixture'}
        self.save()
        self.mod=self.root/'mod';self.mod.mkdir();self.source=self.mod/'mod.acp';self.source.write_bytes(acp(resource()))

    def save(self):self.path.write_text(json.dumps(self.schema),encoding='utf8')

    def test_hash_binding_and_extractor_version(self):
        with self.assertRaises(FormatError):reflection.load(self.path,'other build')
        self.schema['extractor_sha256']='old extractor';self.save()
        with self.assertRaises(FormatError):reflection.load(self.path,self.tool_hash)

    def test_refresh_archives_only_schema_files_outside_game(self):
        old=copy.deepcopy(self.schema);self.schema['summary']={'new':True}
        reflection.save(self.schema,self.path,self.game,True)
        history=list((self.root/'schema-history').glob('*.json'))
        self.assertEqual(len(history),1)
        self.assertEqual(json.loads(history[0].read_text(encoding='utf-8')),old)
        other=self.root/'other.json';other.write_text('{"important":"data"}', encoding='utf-8')
        with self.assertRaises(FormatError):reflection.save(self.schema,other,self.game,True)
        with self.assertRaises(FormatError):reflection.save(self.schema,self.game/'schema.json',self.game)

    def test_default_reports_both_integer_interpretations(self):
        raw=resource(value=None);r=Resource.parse(Node(raw,0,len(raw)))
        report=semantics.inspect_resource(r,self.game,'Example',self.path)
        self.assertFalse(report['schema_status']['verified'])
        self.assertTrue(report['schema_status']['extracted_verified'])
        field=report['properties'][0]
        self.assertEqual(field['integer_interpretations'],{'unsigned':2**32-1,'signed':-1})
        self.assertNotIn('effective_value',field)

    def test_storage_edit_requires_explicit_signedness_and_binds_schema(self):
        with self.assertRaises(FormatError):
            editing.make_plan(self.source,'1','0x471c6d/1',2,game=self.game,schema_path=self.path)
        plan=editing.make_plan(self.source,'1','0x471c6d/1',-1,'i32',game=self.game,schema_path=self.path)
        self.assertEqual(plan['validation_level'],'evidenced_storage_codec')
        editing.apply_plan(plan,self.root/'staged')
        _,items=read_acp(self.root/'staged/mod.acp')
        self.assertEqual(items[0]['resource'].leaves()['0x00471C6D/0x01'],bytes.fromhex('ffffffff'))
        self.field['default_hex']='00000000';self.save()
        with self.assertRaises(FormatError):editing.apply_plan(plan,self.root/'stale')

    def test_candidate_field_never_automatically_editable(self):
        self.field['storage_validated']=False;self.save()
        with self.assertRaises(FormatError):
            editing.make_plan(self.source,'1','0x471c6d/1',2,'u32',game=self.game,schema_path=self.path)

    def test_bool_is_strict_and_existing_width_must_match(self):
        self.field.update(storage='bool',width=1,default_hex='00');self.save()
        with self.assertRaises(FormatError):
            editing.make_plan(self.source,'1','0x471c6d/1',True,game=self.game,schema_path=self.path)
        self.source.write_bytes(acp(resource(value=b'\0')))
        plan=editing.make_plan(self.source,'1','0x471c6d/1',True,game=self.game,schema_path=self.path)
        self.assertEqual(plan['operations'][0]['replacement_hex'],'01')
        with self.assertRaises(FormatError):editing.encode_value('bool',1)

    def test_codec_boundaries_and_search_pagination(self):
        self.assertEqual(editing.encode_value('u64',2**64-1),b'\xff'*8)
        self.assertEqual(editing.encode_value('i16',-32768),b'\0\x80')
        for codec,value in [('i8',128),('u8',-1),('u64',2**64),('u16',True)]:
            with self.assertRaises(FormatError):editing.encode_value(codec,value)
        result=reflection.search(self.schema,'Example',limit=1,offset=0,validated_only=True)
        self.assertEqual(result['total'],1)
        self.assertNotIn('declarations',result['items'][0])


if __name__=='__main__':unittest.main()
