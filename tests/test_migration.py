from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import snapshots,migration
from aow4mod.core import FormatError,read_acp
from test_aow4_mod_audit import acp,rpk,resource,u32


class MigrationTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.game=self.root/'game';(self.game/'Content').mkdir(parents=True)
        self.pack=self.game/'Content/test.rpk';self.pack.write_bytes(rpk([resource()]))
        self.mod=self.root/'mod';self.mod.mkdir();self.acp=self.mod/'mod.acp';self.acp.write_bytes(acp(resource()))

    def test_safe_original_update_is_applied_only_to_stage(self):
        old=snapshots.capture(self.mod,self.game);original=self.acp.read_bytes()
        self.pack.write_bytes(rpk([resource(value=u32(2))]))
        plan=migration.make_rebase_plan(old,self.mod,self.game)
        self.assertTrue(plan['applicable'])
        out=self.root/'rebased';migration.apply_rebase(plan,out)
        _,changes=read_acp(out/'mod.acp')
        self.assertEqual(changes[0]['resource'].leaves()['0x00471C6D/0x01'],u32(2))
        self.assertEqual(self.acp.read_bytes(),original)

    def test_author_edit_is_never_automatically_overwritten(self):
        self.acp.write_bytes(acp(resource(value=u32(3))))
        old=snapshots.capture(self.mod,self.game)
        self.pack.write_bytes(rpk([resource(value=u32(2))]))
        plan=migration.make_rebase_plan(old,self.mod,self.game)
        self.assertFalse(plan['applicable'])
        self.assertEqual(plan['status'],'review_required')
        with self.assertRaises(FormatError):migration.apply_rebase(plan,self.root/'blocked')

    def test_new_original_change_after_planning_invalidates_plan(self):
        old=snapshots.capture(self.mod,self.game)
        self.pack.write_bytes(rpk([resource(value=u32(2))]))
        plan=migration.make_rebase_plan(old,self.mod,self.game)
        self.pack.write_bytes(rpk([resource(value=u32(4))]))
        with self.assertRaises(FormatError):migration.apply_rebase(plan,self.root/'stale')

    def test_same_snapshot_has_nothing_to_apply(self):
        old=snapshots.capture(self.mod,self.game)
        plan=migration.make_rebase_plan(old,self.mod,self.game)
        self.assertEqual(plan['status'],'no_changes')
        self.assertFalse(plan['applicable'])


if __name__=='__main__':unittest.main()
