from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import native_context,pack_imports,editing
from aow4mod.core import FormatError,Node
from test_pack_imports import named_pack
from test_aow4_mod_audit import resource,string


class NativeContextTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.game=self.root/'game';(self.game/'Content').mkdir(parents=True)
        self.leaf=self.root/'Leaf.rpk';self.leaf.write_bytes(named_pack([resource(7)],'LEAF.RPK'))
        self.base=named_pack([resource(1)],'BASE.RPK')

    def test_identical_aliases_deduplicate_but_different_same_names_are_ambiguous(self):
        alias=self.root/'alias.rpk';alias.write_bytes(self.leaf.read_bytes())
        self.assertEqual(len(native_context.bind([self.leaf,alias])),1)
        alias.write_bytes(named_pack([resource(8)],'LEAF.RPK'))
        with self.assertRaisesRegex(FormatError,'Ambiguous'):native_context.bind([self.leaf,alias])

    def test_frozen_local_cycles_are_detected_without_game_or_dll(self):
        base=pack_imports.rewrite(self.base,['LEAF.RPK'])
        self.leaf.write_bytes(pack_imports.rewrite(self.leaf.read_bytes(),['BASE.RPK']))
        with self.assertRaisesRegex(FormatError,'import cycle'):
            native_context.check_frozen_cycles(base,native_context.bind([self.leaf]))

    def test_required_owned_imports_refuse_missing_targets_but_optional_absence_is_advisory(self):
        with self.assertRaisesRegex(FormatError,'Missing required'):
            native_context.preflight(pack_imports.rewrite(self.base,['MISSING.RPK']),[],self.game,True)
        node=Node(self.base,176,len(self.base));payload=editing.rewrite_map(node,{0x1f:struct.pack('<I',1)+string('OPTIONAL.RPK')})
        data=self.base[:12]+struct.pack('<I',len(payload))+self.base[16:176]+payload
        result=native_context.preflight(data,[],self.game,True)
        self.assertEqual(len(result['missing_advisory_imports']),1)
        self.assertEqual(result['graph']['cycles'],[])

    def test_original_name_ambiguity_and_context_base_shadow_are_refused(self):
        directory=self.game/'Content'
        (directory/'a').mkdir();(directory/'b').mkdir()
        (directory/'a/Leaf.rpk').write_bytes(self.leaf.read_bytes())
        (directory/'b/Leaf.rpk').write_bytes(named_pack([resource(9)],'LEAF.RPK'))
        with self.assertRaisesRegex(FormatError,'Ambiguous original'):
            native_context.preflight(pack_imports.rewrite(self.base,['LEAF.RPK']),[],self.game)
        self.leaf.write_bytes(self.base)
        with self.assertRaisesRegex(FormatError,'shadows'):
            native_context.preflight(self.base,native_context.bind([self.leaf]),self.game)


if __name__=='__main__':unittest.main()
