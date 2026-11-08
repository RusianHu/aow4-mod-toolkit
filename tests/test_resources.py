import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import resources,catalog
from aow4mod.core import FormatError,read_rpk,read_acp,references
from test_aow4_mod_audit import acp,rpk,resource
from test_cloning import obj,A,B


class ResourceActionTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.game=self.root/'game';(self.game/'Content').mkdir(parents=True)
        (self.game/'Content/base.rpk').write_bytes(rpk([resource()]))
        self.mod=self.root/'mod';self.mod.mkdir();self.acp=self.mod/'mod.acp';self.acp.write_bytes(acp(resource()))
        self.rpk=self.mod/'local.rpk';self.rpk.write_bytes(rpk([obj(A,'First',B,b'untouched'),obj(B,'Second')]))
        self.index=self.root/'index.sqlite';catalog.build(self.index,self.game,with_links=True)

    def plan(self,rid,action,**kwargs):return resources.make_plan(self.mod,f'{rid:016X}',action,index=self.index,game=self.game,**kwargs)

    def test_rename_refreshes_recognized_labels_without_changing_ids_or_opaque_fields(self):
        original=self.rpk.read_bytes();plan=self.plan(B,'rename',name='Renamed 内部名称');out=self.root/'renamed'
        receipt=resources.apply_plan(plan,out);_,rs=read_rpk(out/'local.rpk')
        self.assertEqual(rs[1].name,'Renamed 内部名称');self.assertEqual(rs[1].rid,B)
        self.assertEqual(references(rs[0])[0]['name'],'Renamed 内部名称')
        self.assertEqual(rs[0].leaves()['0x00471C6D/0x02'],b'untouched')
        self.assertEqual(self.rpk.read_bytes(),original)
        self.assertEqual(receipt['proof']['reference_labels_refreshed'],1)

    def test_remove_unreferenced_local_definition_preserves_other_locator(self):
        plan=self.plan(A,'remove');out=self.root/'removed';resources.apply_plan(plan,out)
        _,rs=read_rpk(out/'local.rpk')
        self.assertEqual([r.rid for r in rs],[B])
        self.assertEqual(rs[0].fields[0x14].string(),f'[#{B:016X}]0\\1')
        self.assertFalse(plan['review_required'])

    def test_removal_with_references_requires_an_explicit_staged_exception(self):
        with self.assertRaisesRegex(FormatError,'incoming references'):self.plan(B,'remove')
        plan=self.plan(B,'remove',allow_dangling=True)
        self.assertTrue(plan['review_required'])
        out=self.root/'dangling';resources.apply_plan(plan,out)
        self.assertEqual(references(read_rpk(out/'local.rpk')[1][0])[0]['id'],f'{B:016X}')

    def test_unknown_id_occurrences_are_never_silently_removed(self):
        self.rpk.write_bytes(rpk([obj(A,'First',opaque=struct.pack('<Q',B)),obj(B,'Second')]))
        with self.assertRaisesRegex(FormatError,'unrecognized'):self.plan(B,'remove',allow_dangling=True)

    def test_revert_drops_override_without_deleting_original(self):
        plan=self.plan(1,'revert');out=self.root/'reverted';resources.apply_plan(plan,out)
        self.assertEqual(read_acp(out/'mod.acp')[1],[])
        self.assertEqual(len(read_rpk(self.game/'Content/base.rpk')[1]),1)
        self.assertEqual((out/'local.rpk').read_bytes(),self.rpk.read_bytes())
        with self.assertRaises(FormatError):self.plan(1,'remove')

    def test_changed_inputs_and_altered_output_hashes_block_application(self):
        plan=self.plan(B,'rename',name='New');changed=copy.deepcopy(plan)
        changed['output_hashes'][str(self.rpk.resolve())]='bad'
        with self.assertRaises(FormatError):resources.apply_plan(changed,self.root/'tampered')
        self.rpk.write_bytes(rpk([obj(A,'Changed'),obj(B,'Second')]))
        with self.assertRaises(FormatError):resources.apply_plan(plan,self.root/'stale')


if __name__=='__main__':unittest.main()
