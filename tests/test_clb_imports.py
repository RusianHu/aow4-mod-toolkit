import copy
from contextlib import contextmanager
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import clb_imports,content_libraries
from aow4mod.authoring import string_vector
from aow4mod.core import FormatError,Node
from aow4mod.filesystem import file_hash,inventory
from test_content_libraries import library_bytes
from test_aow4_mod_audit import mapping


def fixture(dependencies=None,value=1,flag=None,flag_suffix=b''):
    data=library_bytes(value=value)
    _,strings,metadata,payload=struct.unpack_from('<4I',data,8);start=32+strings
    fields={k:v.raw() for k,v in Node(data,start,start+metadata).mapping().items()};fields[28]=b'\x01\x01\0\0'
    if dependencies is not None:fields[22]=string_vector(dependencies)
    if flag is not None:fields[29]=bytes([flag])+flag_suffix
    rewritten=mapping(dict(sorted(fields.items())))
    rewritten+=bytes(-len(rewritten)%16);header=bytearray(data[:32]);struct.pack_into('<I',header,16,len(rewritten))
    return bytes(header)+data[32:start]+rewritten+data[start+metadata:]


class CLBImportTests(unittest.TestCase):
    def test_addition_preserves_order_and_case_and_does_not_duplicate(self):
        before={'20_FName':'OWN','22_FDependencies':['System_Core.clb'],'other':{'data':[1,2]}};saved=copy.deepcopy(before)
        after,change=clb_imports.derive(before,['SYSTEM_CORE.CLB','SYSTEM_EFFECTS.CLB'])
        self.assertEqual(before,saved);self.assertEqual(after['other'],before['other'])
        self.assertEqual(change['added'],['SYSTEM_EFFECTS.CLB'])
        self.assertEqual(change['after'],['System_Core.clb','SYSTEM_EFFECTS.CLB'])
        self.assertEqual(clb_imports.derive(after,['system_effects.clb'])[1]['added'],[])

    def test_self_import_duplicate_and_unsafe_or_wrong_type_names_are_refused(self):
        for values in (['OWN.clb'],['a.clb','A.clb'],['../a.clb'],['C:\\a.clb'],['x.txt'],['CON.clb'],[''],[1],[]):
            with self.subTest(values=values),self.assertRaises(FormatError):
                clb_imports.derive({'20_FName':'OWN'},values)
        for existing in ('bad',{'bad':'layout'},['x.clb','X.clb']):
            with self.assertRaises(FormatError):clb_imports.derive({'20_FName':'OWN','22_FDependencies':existing},['new.clb'])

    def test_dependency_vector_is_bounded_and_reports_literal_declarations(self):
        good=string_vector(['SYSTEM_CORE.CLB','外部.clb']);fake=lambda raw:types.SimpleNamespace(root={22:Node(raw,0,len(raw))})
        self.assertEqual(clb_imports.names(fake(good)),['SYSTEM_CORE.CLB','外部.clb'])
        for raw in (b'',struct.pack('<I',513),struct.pack('<I',1),struct.pack('<II',1,9)+b'a',struct.pack('<II',1,1)+b'\0',struct.pack('<II',1,1)+b'\xff',struct.pack('<I',0)+b'x'):
            with self.subTest(raw=raw),self.assertRaises(FormatError):clb_imports.names(fake(raw))

    def test_actual_binary_comparison_rejects_unrelated_member_and_payload_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            a,b=Path(directory).resolve()/'source.clb',Path(directory).resolve()/'candidate.clb';a.write_bytes(fixture());b.write_bytes(fixture(['SYSTEM_CORE.CLB']))
            report=clb_imports.compare(a,b,['SYSTEM_CORE.CLB']);self.assertTrue(report['unrelated_serialized_data_equal'])
            self.assertEqual(clb_imports.inspect(b)['dependencies'],['SYSTEM_CORE.CLB'])
            b.write_bytes(fixture(['SYSTEM_CORE.CLB'],value=2))
            with self.assertRaises(FormatError):clb_imports.compare(a,b,['SYSTEM_CORE.CLB'])
            bad=bytearray(fixture(['SYSTEM_CORE.CLB']));bad[-1]^=1;b.write_bytes(bad)
            with self.assertRaises(FormatError):clb_imports.compare(a,b,['SYSTEM_CORE.CLB'])

    def test_incomplete_plan_is_a_format_error(self):
        with self.assertRaises(FormatError):clb_imports.validate({'format':clb_imports.FORMAT,'generator_sha256':clb_imports.fingerprint()})

    def test_only_known_zero_alignment_of_the_same_root_bool_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            a,b=Path(directory).resolve()/'source.clb',Path(directory).resolve()/'candidate.clb';a.write_bytes(fixture(flag=0));b.write_bytes(fixture(['SYSTEM_CORE.CLB'],flag=0))
            self.assertTrue(clb_imports.compare(a,b,['SYSTEM_CORE.CLB'])['root_bool_alignment_changed'])
            for flag,suffix in ((1,b''),(0,b'\0\1')):
                b.write_bytes(fixture(['SYSTEM_CORE.CLB'],flag=flag,flag_suffix=suffix))
                with self.assertRaises(FormatError):clb_imports.compare(a,b,['SYSTEM_CORE.CLB'])


class CLBImportStageTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name).resolve()
        self.mod=self.root/'mod';(self.mod/'Libraries').mkdir(parents=True);self.source=self.mod/'mod.acp';self.source.write_bytes(b'fixture')
        self.library=self.mod/'Libraries/FIXTURE.clb';self.library.write_bytes(fixture())
        self.binary=self.root/'FIXTURE.clb';self.binary.write_bytes(fixture(['SYSTEM_CORE.CLB']));self.game=self.root/'game';self.game.mkdir();self.output=self.root/'stage'
        self.plan={'source':str(self.source),'source_files':inventory(self.mod),'library':str(self.library),'compiled_file':str(self.binary),
                   'compiled_sha256':file_hash(self.binary),'game':str(self.game),'relative_path':'Libraries/FIXTURE.clb','preservation':{'unrelated_serialized_data_equal':True},'coverage':'fixture'}
        mocked=patch.object(clb_imports,'validate',return_value={'added':['SYSTEM_CORE.CLB']});mocked.start();self.addCleanup(mocked.stop)

    def test_apply_preserves_source_and_does_not_claim_runtime_link_success(self):
        before=inventory(self.mod);r=clb_imports.apply_plan(self.plan,self.output)
        self.assertEqual(inventory(self.mod),before);self.assertFalse(r['runtime_links_verified'])
        self.assertEqual((self.output/'Libraries/FIXTURE.clb').read_bytes(),self.binary.read_bytes())

    def test_source_changed_between_validation_and_copy_is_refused(self):
        original=clb_imports.copy_directory
        @contextmanager
        def race(*args):
            (self.mod/'later.txt').write_bytes(b'new user file')
            with original(*args) as result:yield result
        with patch.object(clb_imports,'copy_directory',race),self.assertRaisesRegex(FormatError,'source changed before staging'):
            clb_imports.apply_plan(self.plan,self.output)
        self.assertFalse(self.output.exists())

    def test_changed_candidate_is_refused_before_staging(self):
        self.binary.write_bytes(b'new candidate')
        with self.assertRaisesRegex(FormatError,'candidate changed'):clb_imports.apply_plan(self.plan,self.output)
        self.assertFalse(self.output.exists())


if __name__=='__main__':unittest.main()
