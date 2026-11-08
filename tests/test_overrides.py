from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import overrides
from aow4mod.core import Node,FormatError,read_acp
from test_aow4_mod_audit import acp,rpk,resource,typed,mapping,string


def located_resource(rid):
    raw=resource(rid=rid);kind,fields=Node(raw,0,len(raw)).typed()
    return typed(kind,{**{k:v.raw() for k,v in fields.items()},0x14:string(f'[#{rid:016X}]0\\0')})


class OverrideTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.mod=self.root/'mod';self.mod.mkdir();self.acp=self.mod/'mod.acp';self.acp.write_bytes(acp(resource()))
        self.pack=self.root/'original.rpk';self.pack.write_bytes(rpk([located_resource(2)]))

    def test_add_to_existing_pack_preserves_old_object(self):
        before=read_acp(self.acp)[1][0]['resource'].node.raw();original=self.acp.read_bytes()
        plan=overrides.make_plan(self.mod,'2',None,self.pack)
        out=self.root/'edited';overrides.apply_plan(plan,out)
        _,after=read_acp(out/'mod.acp')
        self.assertEqual(len(after),2)
        self.assertEqual(after[0]['resource'].node.raw(),before)
        self.assertEqual(after[1]['resource'].rid,2)
        self.assertEqual(self.acp.read_bytes(),original)

    def test_add_first_override_to_empty_package(self):
        raw=self.acp.read_bytes();_,outer=Node(raw,19,len(raw)).typed();fields=outer[0x40009d].mapping()
        self.acp.write_bytes(raw[:19]+typed(0x40009d,{0x40009d:mapping({k:v.raw() for k,v in fields.items() if k!=0x26})}))
        plan=overrides.make_plan(self.mod,'2',None,self.pack)
        out=self.root/'edited';overrides.apply_plan(plan,out)
        self.assertEqual(len(read_acp(out/'mod.acp')[1]),1)

    def test_duplicate_and_stale_original_are_rejected(self):
        with self.assertRaises(FormatError):overrides.make_plan(self.mod,'1',None,self.pack)
        plan=overrides.make_plan(self.mod,'2',None,self.pack)
        self.pack.write_bytes(rpk([located_resource(3)]))
        with self.assertRaises(FormatError):overrides.apply_plan(plan,self.root/'stale')


if __name__=='__main__':unittest.main()
