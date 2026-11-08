import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import language_context,native
from aow4mod.core import FormatError


@unittest.skipUnless(importlib.util.find_spec('polib'),'optional localization dependency')
class LanguageContextTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve()

    def catalog(self,relative,text='English'):
        import polib
        path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True)
        po=polib.POFile();po.metadata={'Content-Type':'text/plain; charset=UTF-8'}
        po.append(polib.POEntry(msgid='EXAMPLE@NAME',msgstr=text));path.write_bytes(po.to_binary());return path

    def test_english_and_default_are_selected_without_loading_other_languages(self):
        a=self.catalog('Language/default.mo');b=self.catalog('Language/EN/example.mo')
        self.catalog('Language/ZH/example.mo','中文');self.catalog('unrelated/unused.mo')
        self.assertEqual(set(language_context.english(self.root)),{a,b})
        self.assertEqual(len(language_context.bind([a,b])),2)

    def test_same_filename_ambiguity_and_original_namespace_are_refused(self):
        a=self.catalog('a/example.mo');b=self.catalog('b/EXAMPLE.MO')
        self.assertEqual(len(language_context.bind([a,b])),1)
        self.catalog('b/EXAMPLE.MO','Different')
        with self.assertRaisesRegex(FormatError,'Ambiguous'):language_context.bind([a,b])
        reserved=self.catalog('Language/EN/EN.MO')
        with self.assertRaisesRegex(FormatError,'shadow'):language_context.bind([reserved])

    def test_malformed_mo_and_missing_native_context_binding_are_refused(self):
        path=self.root/'bad.mo'
        for data in (b'x',struct.pack('<I',0x950412de)+b'broken'):
            path.write_bytes(data)
            with self.assertRaisesRegex(FormatError,'Invalid native MO'):language_context.bind([path])
        with self.assertRaisesRegex(FormatError,'MO context'):
            native.validate_payload('roundtrip',{}, {'languages':[{'file':'expected','sha256':'hash'}]})


if __name__=='__main__':unittest.main()
