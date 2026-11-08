import copy
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import project_preview as preview
import test_launcher as fixtures
from aow4mod import launcher
from aow4mod.core import FormatError,read_acp
from aow4mod.filesystem import inventory,file_hash,save_record


class ProjectPreviewTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.LauncherTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.workspace=self.f.root/'workspace';self.workspace.mkdir();self.release=self.workspace/'release';self.release.mkdir()
        self.content=self.release/'content';shutil.copytree(self.f.new,self.content)
        self.path=self.release/'manifest.json';save_record(self.path,{'test':'synthetic'})
        self.package=read_acp(next(self.content.glob('*.acp')))[0]['package_id']
        self.manifest={'files':{k:v['sha256'] for k,v in inventory(self.content).items()},'_manifest_sha256':file_hash(self.path),
                      '_review_required':True,'version':'0.1','native_validation_requested':True,
                      'native_validation_summary':{'errors':0,'mismatches':0}}
        self.destination=self.f.data/'Mods/Project Preview';self.job=self.workspace/'preview'
        self.game=self.f.root/'game';self.game.mkdir()
        database_path=launcher.database_path;inspect=launcher.inspect;binding=launcher.launcher_binding
        mocks=[patch.object(preview,'find_game',return_value=self.game),
               patch.object(preview.publication,'load_release',side_effect=lambda *a,**k:(copy.deepcopy(self.manifest),self.content)),
               patch.object(preview.synchronization,'load_release',side_effect=lambda *a,**k:(copy.deepcopy(self.manifest),self.content)),
               patch.object(launcher,'database_path',side_effect=lambda p=None:database_path(p or self.f.db)),
               patch.object(launcher,'launcher_binding',side_effect=lambda p=None:binding(p or self.f.app)),
               patch.object(launcher,'inspect',side_effect=lambda p=None,*a,**k:inspect(p or self.f.db,launcher_asar=self.f.app))]
        for mock in mocks:mock.start();self.addCleanup(mock.stop)

    def prepare(self,note='Launcher only; findings stay unresolved'):
        return preview.prepare(self.path,self.destination,self.job,self.game,note)

    def test_fresh_project_appends_and_withdraws_without_touching_existing_members(self):
        before=inventory(self.f.base);self.prepare();self.assertEqual(preview.apply(self.job)['phase'],'enabled')
        members=launcher.inspect()['members'];self.assertEqual([m['path'] for m in members],[str(self.f.base),str(self.destination)])
        self.assertTrue(all(m['enabled'] for m in members))
        self.assertEqual(preview.withdraw(self.job)['phase'],'withdrawn')
        self.assertEqual(launcher.inspect()['registered_mods'],1);self.assertFalse(self.destination.exists())
        self.assertEqual(inventory(self.f.base),before);self.assertEqual(self.f.cfg.read_bytes(),self.f.original)

    def test_existing_identity_or_destination_cannot_be_replaced(self):
        package=self.package
        with self.f.sql() as con:con.execute('UPDATE mods SET metadataId=?',(package,))
        with self.assertRaisesRegex(FormatError,'already registered'):self.prepare()
        with self.assertRaisesRegex(FormatError,'new direct child'):
            preview.prepare(self.path,self.f.base,self.job,self.game,'note')
        self.assertFalse(self.job.exists())

    def test_review_note_does_not_waive_native_errors_or_missing_validation(self):
        with self.assertRaisesRegex(FormatError,'review note'):self.prepare(None)
        for key,value in [('errors',1),('mismatches',1)]:
            self.manifest['native_validation_summary'][key]=value
            with self.assertRaisesRegex(FormatError,'requires native'):self.prepare()
            self.manifest['native_validation_summary'][key]=0
        self.manifest['native_validation_requested']=False
        with self.assertRaisesRegex(FormatError,'requires native'):self.prepare()
        self.assertFalse(self.destination.exists())

    def test_changed_plan_or_database_is_refused_before_installation(self):
        self.prepare();plan=self.job/'installation-plan.json';raw=plan.read_bytes();plan.write_bytes(raw+b'\n')
        with self.assertRaisesRegex(FormatError,'installation plan changed'):preview.apply(self.job)
        plan.write_bytes(raw)
        with self.f.sql() as con:con.execute("UPDATE unrelated SET note='later edit'")
        before=file_hash(self.f.db)
        with self.assertRaisesRegex(FormatError,'launcher changed'):preview.apply(self.job)
        self.assertEqual(file_hash(self.f.db),before);self.assertFalse(self.destination.exists())

    def test_failed_observation_withdraws_but_active_preview_cannot_be_replayed(self):
        self.prepare();preview.apply(self.job)
        with self.assertRaisesRegex(FormatError,'already attempted'):preview.apply(self.job)
        with patch.object(preview.launcher_session,'prepare'),patch.object(preview.launcher_session,'run',return_value={'passed':False}):
            self.assertEqual(preview.observe(self.job)['phase'],'observation_failed')
        self.assertEqual(preview.withdraw(self.job)['phase'],'withdrawn')

    def test_withdrawal_does_not_require_original_release_but_preserves_later_user_edits(self):
        self.prepare();preview.apply(self.job);self.path.unlink()
        data=json.loads(self.f.cfg.read_text(encoding='utf-8'));data['user_later_edit']=True;save_record(self.f.cfg,data)
        with self.assertRaisesRegex(FormatError,'needs review'):preview.withdraw(self.job)
        self.assertTrue(json.loads(self.f.cfg.read_text(encoding='utf-8'))['user_later_edit']);self.assertFalse(self.destination.exists())

    def test_independent_reopens_verify_members_without_claiming_gameplay(self):
        self.prepare();preview.apply(self.job)
        with patch.object(preview.launcher_session,'prepare'),patch.object(preview.launcher_session,'run',return_value={'passed':True}):
            self.assertEqual(preview.observe(self.job)['phase'],'observed')
            preview.withdraw(self.job)
            result=preview.observe(self.job,final=True)
        self.assertEqual(result['phase'],'verified_withdrawn');self.assertFalse(result['gameplay_tested']);self.assertFalse(result['published'])


if __name__=='__main__':unittest.main()
