from pathlib import Path
import copy
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import editing
from aow4mod.core import Node,FormatError,read_acp,read_rpk
from test_aow4_mod_audit import acp,rpk,resource,mapping,u32


class EditingTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.mod=self.root/'mod';self.mod.mkdir();self.acp=self.mod/'mod.acp';self.acp.write_bytes(acp(resource()))

    def plan(self,path=None,codec='u32',value=2):
        return editing.make_plan(path or self.acp,'1','0x00471C6D/0x01',value,codec,allow_opaque=True)

    def test_noop_roundtrip_preserves_noncanonical_offset_table(self):
        raw=mapping({1:b'ab',2:b'cd'})
        self.assertEqual(editing.rewrite_map(Node(raw,0,len(raw)),{1:b'ab'}),raw)

    def test_acp_apply_is_staged_and_preserves_source(self):
        original=self.acp.read_bytes();plan=self.plan();out=self.root/'edited'
        receipt=editing.apply_plan(plan,out)
        self.assertEqual(self.acp.read_bytes(),original)
        self.assertEqual(receipt['verified_changed_fields'],1)
        _,changes=read_acp(out/'mod.acp')
        self.assertEqual(changes[0]['resource'].leaves()['0x00471C6D/0x01'],u32(2))

    def test_rpk_length_and_other_resource_are_preserved(self):
        p=self.mod/'example.rpk';p.write_bytes(rpk([resource(),resource(rid=2,name='Keep')]))
        _,before=read_rpk(p);out=self.root/'rpk-edited'
        editing.apply_plan(self.plan(p,'string','longer text'*50),out)
        _,after=read_rpk(out/p.name)
        self.assertEqual(before[1].node.raw(),after[1].node.raw())
        self.assertNotEqual(before[0].node.raw(),after[0].node.raw())

    def test_unset_and_readd_property(self):
        out=self.root/'unset';editing.apply_plan(self.plan(codec='unset',value=None),out)
        _,changes=read_acp(out/'mod.acp')
        self.assertNotIn('0x00471C6D/0x01',changes[0]['resource'].leaves())
        second=self.root/'readded';editing.apply_plan(self.plan(out/'mod.acp'),second)
        _,changes=read_acp(second/'mod.acp')
        self.assertEqual(changes[0]['resource'].leaves()['0x00471C6D/0x01'],u32(2))

    def test_source_change_and_plan_tampering_are_rejected(self):
        plan=self.plan();tampered=copy.deepcopy(plan);tampered['operations'][0]['replacement_hex']='03000000'
        with self.assertRaises(FormatError):editing.apply_plan(tampered,self.root/'bad')
        self.acp.write_bytes(acp(resource(value=u32(4))))
        with self.assertRaises(FormatError):editing.apply_plan(plan,self.root/'stale')

    def test_source_output_and_unknown_codec_are_protected(self):
        with self.assertRaises(FormatError):editing.apply_plan(self.plan(),self.mod/'inside')
        with self.assertRaises(FormatError):editing.make_plan(self.acp,'1','0x00471C6D/0x01',2)
        for value in (-1,True,1.5,2**32):
            with self.assertRaises(FormatError):editing.encode_value('u32',value)


if __name__=='__main__':unittest.main()
