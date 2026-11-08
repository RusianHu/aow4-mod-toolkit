from pathlib import Path
import importlib.util
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import localization
from aow4mod.core import FormatError


@unittest.skipUnless(importlib.util.find_spec('polib'),'optional localization dependency; run tests in project venv')
class LocalizationTests(unittest.TestCase):
    def setUp(self):
        import polib
        self.polib=polib;t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.po=self.root/'test.po'

    def save(self,entries):
        po=self.polib.POFile();po.metadata={'Content-Type':'text/plain; charset=UTF-8','Language':'zh_CN',
            'Plural-Forms':'nplurals=1; plural=0;'}
        for e in entries:po.append(self.polib.POEntry(**e))
        po.save(str(self.po));return po

    def test_unicode_compilation_roundtrip_and_source_unchanged(self):
        self.save([{'msgid':'TEST@NAME','msgstr':'中文名称'}]);before=self.po.read_bytes()
        result=localization.compile_file(self.po,self.root/'test.mo')
        self.assertEqual(result['compiled_entries'],1)
        self.assertEqual(self.po.read_bytes(),before)
        self.assertEqual(localization.validate(self.root/'test.mo')['summary']['errors'],0)

    def test_context_ids_and_placeholder_integrity(self):
        self.save([{'msgctxt':'TEST@DESC','msgid':'Damage {Damage.Amount}','msgstr':'伤害 {Damage.Amount}'}])
        data,_=localization.compile_bytes(self.po,'context')
        self.assertEqual(self.polib.mofile(data)[0].msgid,'TEST@DESC')
        self.save([{'msgctxt':'TEST@DESC','msgid':'Damage {Damage.Amount}','msgstr':'伤害'}])
        self.assertEqual(localization.validate(self.po,'context')['summary']['errors'],1)

    def test_duplicate_keys_block_compilation(self):
        self.save([{'msgid':'TEST@NAME','msgstr':'a'},{'msgid':'TEST@NAME','msgstr':'b'}])
        with self.assertRaises(FormatError):localization.compile_bytes(self.po)

    def test_fuzzy_omission_is_reported_and_strict_mode_blocks_it(self):
        self.save([{'msgid':'A','msgstr':'ok'},{'msgid':'B','msgstr':'maybe','flags':['fuzzy']}])
        data,result=localization.compile_bytes(self.po)
        self.assertEqual(result['compiled_entries'],1);self.assertEqual(result['omitted_entries'],1)
        with self.assertRaises(FormatError):localization.compile_bytes(self.po,require_complete=True)

    def test_plural_and_overwrite_protection(self):
        self.save([{'msgid':'ITEM','msgid_plural':'ITEMS','msgstr_plural':{0:'物品'}}])
        output=self.root/'test.mo';localization.compile_file(self.po,output)
        with self.assertRaises(FileExistsError):localization.compile_file(self.po,output)


if __name__=='__main__':unittest.main()
