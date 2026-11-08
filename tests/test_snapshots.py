from pathlib import Path
import copy
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from aow4mod import snapshots, cli
from aow4mod.core import FormatError
from test_aow4_mod_audit import acp,rpk,resource


def snapshot(original,modified,original_type="0x00471C71"):
    return {"format":snapshots.FORMAT,"mod":{"sha256":"same","package_id":"test"},
            "game":{"steam_build_id":"test"},"mod_gameplay_sha256":"same",
            "originals":{"TEST:1":{"id":"1","name":"Example","type_id":original_type,"fields":original}},
            "modified":{"TEST:1":{"id":"1","name":"Example","type_id":"0x00471C71","fields":modified}}}


class SnapshotTests(unittest.TestCase):
    def test_unchanged_mod_and_updated_original_proposes_only_untouched_field(self):
        old=snapshot({"a":"00","b":"10"},{"a":"01","b":"10"})
        new=snapshot({"a":"02","b":"20"},{"a":"01","b":"10"})
        result=snapshots.compare(old,new)
        classes={d['property']:d['classification'] for d in result['resources'][0]['fields']}
        self.assertEqual(classes['a'],'overlapping_change_needs_review')
        self.assertEqual(classes['b'],'safe_rebase_candidate')
        self.assertEqual(result['summary']['manual_conflicts'],1)

    def test_new_mod_edit_is_not_discarded_as_safe_rebase(self):
        old=snapshot({"a":"00"},{"a":"00"})
        new=snapshot({"a":"01"},{"a":"02"})
        self.assertEqual(snapshots.compare(old,new)['summary']['safe_rebase_candidates'],0)
        self.assertEqual(snapshots.compare(old,new)['summary']['manual_conflicts'],1)

    def test_metadata_only_change_keeps_baseline_usable(self):
        old=snapshot({"a":"00"},{"a":"01"})
        new=copy.deepcopy(old);new['mod']['sha256']='changed-description'
        result=snapshots.compare(old,new)
        self.assertTrue(result['metadata_changed'])
        self.assertFalse(result['gameplay_changed'])
        self.assertEqual(result['resources'],[])

    def test_type_change_blocks_safe_rebase(self):
        old=snapshot({"a":"00"},{"a":"00"})
        new=snapshot({"a":"01"},{"a":"00"},"0x00473702")
        result=snapshots.compare(old,new)
        self.assertEqual(result['summary']['safe_rebase_candidates'],0)
        self.assertGreater(result['summary']['manual_conflicts'],0)

    def test_different_package_identity_is_rejected(self):
        old=snapshot({},{});new=copy.deepcopy(old);new['mod']['package_id']='different'
        with self.assertRaises(FormatError):snapshots.compare(old,new)

    def test_snapshot_capture_and_cli_index_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();game=root/'game';(game/'Content').mkdir(parents=True)
            (game/'Content/test.rpk').write_bytes(rpk([resource()]))
            mod=root/'mod';mod.mkdir();(mod/'mod.acp').write_bytes(acp(resource(value=None)))
            captured=snapshots.capture(mod,game)
            self.assertEqual(len(captured['originals']),1)
            self.assertEqual(len(captured['modified']),1)
            with redirect_stdout(io.StringIO()):
                code=cli.main(['index','--game',str(game),'--index',str(root/'index.sqlite'),'--links'])
            self.assertEqual(code,0)


if __name__=='__main__':unittest.main()
