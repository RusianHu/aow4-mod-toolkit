import copy
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import maintenance_preview as preview
import test_launcher as fixtures
from aow4mod import launcher
from aow4mod.core import FormatError,read_acp
from aow4mod.filesystem import inventory,file_hash,save_record


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.LauncherTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.workspace=self.f.root/'workspace';self.workspace.mkdir();self.release=self.workspace/'release';self.release.mkdir()
        self.content=self.release/'content';shutil.copytree(self.f.base,self.content)
        self.path=self.release/'manifest.json';save_record(self.path,{'test':'synthetic'})
        self.package=read_acp(next(self.f.base.glob('*.acp')))[0]['package_id']
        self.manifest={'files':{k:v['sha256'] for k,v in inventory(self.content).items()},'_manifest_sha256':file_hash(self.path),
          '_review_required':True,'version':'0.1','native_validation_requested':True,'native_validation_summary':{'errors':0,'mismatches':0}}
        self.workflow={'origin':str(self.f.base),'package_id':self.package,'game':str(self.f.root/'game'),
            'build':{'manifest':'release/manifest.json','manifest_sha256':file_hash(self.path)}}
        self.destination=self.f.data/'Mods/Preview';self.job=self.workspace/'previews/test'
        database_path=launcher.database_path;inspect=launcher.inspect;binding=launcher.launcher_binding
        mocks=[patch.object(preview.maintenance,'read',return_value=(self.workspace,self.workflow,self.workspace/'project.json',{},self.content,'hash')),
               patch.object(preview.maintenance,'current_assessment',return_value=self.workspace),
               patch.object(preview.maintenance,'context_root',return_value=self.workspace/'context'),
               patch.object(preview.publication,'load_release',side_effect=lambda *a,**k:(copy.deepcopy(self.manifest),self.content)),
               patch.object(preview.synchronization,'load_release',side_effect=lambda *a,**k:(copy.deepcopy(self.manifest),self.content)),
               patch.object(launcher,'database_path',side_effect=lambda p=None:database_path(p or self.f.db)),
               patch.object(launcher,'launcher_binding',side_effect=lambda p=None:binding(p or self.f.app)),
               patch.object(launcher,'inspect',side_effect=lambda p=None,*a,**k:inspect(p or self.f.db,launcher_asar=self.f.app))]
        for mock in mocks:mock.start();self.addCleanup(mock.stop)

    def prepare(self,note='Launcher lifecycle only; findings remain unresolved'):
        return preview.prepare(self.workspace,self.destination,self.job,note)

    def test_same_identity_switch_and_withdraw_restore_original_rows_order_files(self):
        before=inventory(self.f.base);self.prepare();result=preview.apply(self.job)
        self.assertEqual(result['phase'],'enabled');self.assertEqual(result['package_id'],self.package)
        x=launcher.inspect();enabled=[m for m in x['members'] if m['enabled']]
        self.assertEqual([m['path'] for m in enabled],[str(self.destination)])
        self.assertEqual(enabled[0]['package_id'],self.package)
        result=preview.withdraw(self.job);self.assertEqual(result['phase'],'withdrawn')
        self.assertFalse(self.destination.exists());self.assertEqual(inventory(self.f.base),before)
        self.assertEqual(launcher.inspect()['registered_mods'],1)

    def test_review_scope_and_new_destination_are_mandatory(self):
        with self.assertRaisesRegex(FormatError,'review note'):self.prepare(None)
        self.assertFalse(self.job.exists())
        with self.assertRaisesRegex(FormatError,'new distinct'):preview.prepare(self.workspace,self.f.base,self.job,'note')

    def test_native_error_or_missing_validation_cannot_be_waived(self):
        self.manifest['native_validation_summary']['errors']=1
        with self.assertRaisesRegex(FormatError,'without errors'):self.prepare()
        self.manifest['native_validation_summary']['errors']=0;self.manifest['native_validation_requested']=False
        with self.assertRaisesRegex(FormatError,'requires native'):self.prepare()

    def test_changed_installation_recipe_is_refused_before_live_writes(self):
        self.prepare();before=file_hash(self.f.db)
        with (self.job/'installation-plan.json').open('a') as f:f.write('\n')
        with self.assertRaisesRegex(FormatError,'installation plan changed'):preview.apply(self.job)
        self.assertEqual(file_hash(self.f.db),before);self.assertFalse(self.destination.exists())

    def test_later_live_change_does_not_get_overwritten_or_installed(self):
        self.prepare()
        with self.f.sql() as con:con.execute("UPDATE unrelated SET note='later user edit'")
        before=file_hash(self.f.db)
        with self.assertRaisesRegex(FormatError,'launcher changed'):preview.apply(self.job)
        self.assertEqual(file_hash(self.f.db),before);self.assertFalse(self.destination.exists())

    def test_failed_observation_can_withdraw_owned_variant_without_old_db_restore(self):
        self.prepare();preview.apply(self.job)
        with patch.object(preview.launcher_session,'prepare'),patch.object(preview.launcher_session,'run',return_value={'passed':False}):
            result=preview.observe(self.job)
        self.assertEqual(result['phase'],'observation_failed')
        self.assertEqual(preview.withdraw(self.job)['phase'],'withdrawn')

    def test_enabled_preview_is_not_replayed(self):
        self.prepare();preview.apply(self.job)
        with self.assertRaisesRegex(FormatError,'already attempted'):preview.apply(self.job)
        preview.withdraw(self.job)

    def test_withdrawal_does_not_overwrite_unrelated_user_configuration_values(self):
        self.prepare();preview.apply(self.job)
        data=json.loads(self.f.cfg.read_text(encoding='utf-8'));data['unknown']={'user_changed':True};save_record(self.f.cfg,data)
        with self.assertRaisesRegex(FormatError,'needs review'):preview.withdraw(self.job)
        self.assertEqual(json.loads(self.f.cfg.read_text(encoding='utf-8'))['unknown'],{'user_changed':True})
        self.assertFalse(self.destination.exists())


if __name__=='__main__':unittest.main()
