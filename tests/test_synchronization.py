from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import authoring,synchronization as sync
from aow4mod.core import FormatError,Node,read_acp,sha
from aow4mod.filesystem import inventory
from test_aow4_mod_audit import acp,rpk,resource,mapping,typed


class SynchronizationTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.dest=self.root/'installed';self.dest.mkdir();self.acp=self.dest/'installed.acp'
        raw=acp(resource());fields=Node(raw,19,len(raw)).typed()[1][0x40009d].mapping()
        self.package=str(uuid.uuid4());values={k:v.raw() for k,v in fields.items()};values[0x2a]=uuid.UUID(self.package).bytes_le
        self.acp.write_bytes(raw[:19]+typed(0x40009d,{0x40009d:mapping(values)}))
        (self.dest/'artwork.psd').write_bytes(b'author source artwork')
        self.game=self.root/'game';(self.game/'Content').mkdir(parents=True)
        (self.game/'Content/base.rpk').write_bytes(rpk([resource()]))
        project=self.root/'project';authoring.import_project(self.dest,project)
        authoring.build_project(project,self.root/'release',self.game,'1.1')
        self.manifest=self.root/'release/manifest.json';self.backup=self.root/'backup'

    def plan(self,**kwargs):return sync.make_plan(self.manifest,kwargs.pop('destination',self.dest),self.backup,self.game,**kwargs)

    def test_sync_preserves_source_artwork_and_rollback_restores_exact_tree(self):
        before=inventory(self.dest);plan=self.plan();result=sync.apply_plan(plan)
        self.assertEqual(read_acp(self.acp)[0]['version_minor'],1)
        self.assertEqual((self.dest/'artwork.psd').read_bytes(),b'author source artwork')
        self.assertEqual(inventory(self.backup/'original'),before)
        self.assertEqual(sync.status(result['receipt'])['observed_state'],'installed')
        after=inventory(self.dest);sync.rollback(result['receipt'])
        self.assertEqual(inventory(self.dest),before)
        self.assertEqual(inventory(self.backup/'post-sync'),after)
        self.assertEqual(sync.status(result['receipt'])['observed_state'],'original')

    def test_new_install_rolls_back_to_absence(self):
        destination=self.root/'new-install';plan=self.plan(destination=destination)
        result=sync.apply_plan(plan);self.assertTrue(destination.is_dir())
        sync.rollback(result['receipt']);self.assertFalse(destination.exists())

    def test_empty_existing_destination_remains_a_directory_after_rollback(self):
        destination=self.root/'empty';destination.mkdir()
        result=sync.apply_plan(self.plan(destination=destination))
        sync.rollback(result['receipt']);self.assertTrue(destination.is_dir());self.assertEqual(inventory(destination),{})

    def test_unmanaged_runtime_requires_policy_and_prune_is_backed_up(self):
        (self.dest/'legacy.rpk').write_bytes(b'old runtime fixture')
        with self.assertRaisesRegex(FormatError,'Unmanaged runtime'):self.plan()
        keep=self.plan(extra_runtime='keep');self.assertNotIn('legacy.rpk',keep['files_removed'])
        plan=self.plan(extra_runtime='prune');self.assertIn('legacy.rpk',plan['files_removed'])
        result=sync.apply_plan(plan);self.assertFalse((self.dest/'legacy.rpk').exists())
        self.assertEqual((self.backup/'original/legacy.rpk').read_bytes(),b'old runtime fixture')
        sync.rollback(result['receipt']);self.assertTrue((self.dest/'legacy.rpk').exists())

    def test_previously_managed_obsolete_files_are_removed_without_touching_artwork(self):
        (self.dest/'legacy.rpk').write_bytes(b'old')
        state={'format':'aow4mod-sync-state/1','package_id':self.package,'managed_files':{'legacy.rpk':sha(b'old')}}
        (self.dest/sync.STATE).write_text(json.dumps(state), encoding='utf-8')
        plan=self.plan();self.assertEqual(plan['files_removed'],['legacy.rpk'])
        result=sync.apply_plan(plan);self.assertTrue((self.dest/'artwork.psd').exists())

    def test_changed_destination_blocks_apply_and_post_sync_edits_block_rollback(self):
        plan=self.plan();(self.dest/'artwork.psd').write_bytes(b'changed')
        with self.assertRaises(FormatError):sync.apply_plan(plan)
        self.assertFalse(self.backup.exists())
        result=sync.apply_plan(self.plan());(self.dest/'artwork.psd').write_bytes(b'user edit after sync')
        with self.assertRaisesRegex(FormatError,'no longer matches'):sync.rollback(result['receipt'])
        self.assertEqual(sync.status(result['receipt'])['observed_state'],'diverged')

    def test_second_rename_failure_restores_original_and_keeps_backup(self):
        plan=self.plan();before=inventory(self.dest);rename=os.rename
        def fail_install(src,dst):
            if Path(src).name=='new' and Path(dst)==self.dest:raise OSError('simulated install failure')
            return rename(src,dst)
        with patch('aow4mod.synchronization.os.rename',side_effect=fail_install):
            with self.assertRaisesRegex(OSError,'simulated'):sync.apply_plan(plan)
        self.assertEqual(inventory(self.dest),before)
        self.assertEqual(inventory(self.backup/'original'),before)
        self.assertEqual(sync.status(self.backup/'receipt.json')['observed_state'],'original')

    def test_wrong_package_and_protected_paths_are_rejected(self):
        raw=self.acp.read_bytes();fields=Node(raw,19,len(raw)).typed()[1][0x40009d].mapping()
        values={k:v.raw() for k,v in fields.items()};values[0x2a]=uuid.uuid4().bytes_le
        self.acp.write_bytes(raw[:19]+typed(0x40009d,{0x40009d:mapping(values)}))
        with self.assertRaisesRegex(FormatError,'different Mod'):self.plan()
        with self.assertRaises(FormatError):self.plan(destination=self.game/'Content/new')
        with self.assertRaises(FormatError):sync.make_plan(self.manifest,self.root/'fresh',self.root/'fresh/backup',self.game)

    def test_review_gate_requires_explicit_plan_exception(self):
        manifest=json.loads(self.manifest.read_text(encoding='utf-8'));manifest['release_gate']['review_required']=True
        self.manifest.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'requires review'):self.plan()
        self.assertTrue(self.plan(allow_review=True)['release_review_required'])

    def test_unsafe_managed_paths_and_modified_backups_are_rejected(self):
        state={'format':'aow4mod-sync-state/1','package_id':self.package,'managed_files':{'../outside':sha(b'bad')}}
        (self.dest/sync.STATE).write_text(json.dumps(state), encoding='utf-8')
        with self.assertRaises(FormatError):self.plan()
        (self.dest/sync.STATE).unlink();result=sync.apply_plan(self.plan())
        (self.backup/'original/artwork.psd').write_bytes(b'corrupted')
        with self.assertRaisesRegex(FormatError,'backup changed'):sync.rollback(result['receipt'])


if __name__=='__main__':unittest.main()
