import copy
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import maintenance_review,session_evidence
from aow4mod.core import FormatError
from aow4mod.filesystem import file_hash,save_record
import test_launcher as fixtures


class ReviewTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.state={'build':{'manifest_sha256':'candidate'},'assessment':{'artifacts':[],'source_files':{}},'package_id':'package'}
        self.manifest={'release_gate':{'review_required':True},'native_validation_summary':{'review_required':True,'errors':0,'mismatches':0,'diagnostic_count':135},
                       'environment_summary':{'review_required':True,'actionable_issues':49},'maintenance_validation_summary':{'review_required':False,'findings':[]}}

    def create(self):
        maintenance_review.create(self.root/'review',self.state,self.manifest,'Findings remain unresolved; no gameplay or publication approval.')
        return self.root/'review'

    def test_review_preserves_all_findings_and_does_not_grant_approval(self):
        path=self.create();result=maintenance_review.validate(path,self.state,self.manifest)
        self.assertTrue(result['release_review_required']);self.assertFalse(result['compatibility_resolved']);self.assertFalse(result['publication_authorized'])
        self.assertEqual(result['dispositions'][2]['diagnostics'],135)
        self.assertEqual(result['dispositions'][-1]['status'],'not_evaluated')

    def test_forged_conclusion_cannot_elevate_scope_or_clear_diagnostics(self):
        path=self.create();record=json.loads((path/'review.json').read_text(encoding='utf-8'));record['conclusion']['compatibility_resolved']=True
        record['conclusion']['dispositions'][2]['diagnostics']=0;save_record(path/'review.json',record)
        with self.assertRaisesRegex(FormatError,'cannot clear'):maintenance_review.validate(path,self.state,self.manifest)

    def test_changed_candidate_or_assessment_invalidates_previous_review(self):
        path=self.create();changed=copy.deepcopy(self.state);changed['build']['manifest_sha256']='another'
        with self.assertRaisesRegex(FormatError,'stale/different'):maintenance_review.validate(path,changed,self.manifest)
        changed=copy.deepcopy(self.state);changed['assessment']['source_files']['edited']=True
        with self.assertRaisesRegex(FormatError,'stale/different'):maintenance_review.validate(path,changed,self.manifest)

    def test_missing_note_is_not_a_review(self):
        with self.assertRaisesRegex(FormatError,'Review note'):maintenance_review.create(self.root/'review',self.state,self.manifest,' ')


class SessionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.LauncherTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.root/'archive';(self.root/'after').mkdir(parents=True);(self.root/'official').mkdir()
        shutil.copyfile(self.f.db,self.root/'after/0000.bin');shutil.copyfile(self.f.cfg,self.root/'after/0001.bin')
        before=[{'path':str(self.f.db)},{'path':str(self.f.cfg)}]
        save_record(self.root/'session.json',{'format':'aow4mod-launcher-session/1','data':str(self.f.data),'before_mutable_files':before})
        result={'format':'aow4mod-launcher-session/1','passed':True,'protected_inputs_unchanged':True,'host_files_unchanged':True,
                'checkpoint':{'files':[{'path':str(self.f.db),'sha256':file_hash(self.root/'after/0000.bin')},{'path':str(self.f.cfg),'sha256':file_hash(self.root/'after/0001.bin')}]}}
        save_record(self.root/'result.json',result)
        renderer={'snapshots':[{'url':'file:///official/dist/renderer/primary.html','ready':'complete','text':'Age of Wonders 4','buttons':[{'testid':'gameButton-play'}],'links':[{'href':'#/mods'},{'href':'#/installed-mods'}]}]}
        save_record(self.root/'official/renderer.json',renderer)
        (self.root/'official/stdout.log').write_text('Initializing Steam API with App ID: 1669000\nSteam API initialized successfully\n', encoding='utf-8')
        (self.root/'official/stderr.log').write_text('', encoding='utf-8')

    def test_saved_database_and_logs_are_read_independently(self):
        result=session_evidence.session(self.root)
        self.assertTrue(result['steam_renderer_verified']);self.assertFalse(result['gameplay_verified'])
        self.assertEqual(result['database']['enabled_paths'],[str(self.f.base)])

    def test_success_boolean_cannot_replace_missing_steam_log_evidence(self):
        (self.root/'official/stdout.log').write_text('', encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'Steam renderer/log'):session_evidence.session(self.root)

    def test_refreshed_checkpoint_hash_cannot_hide_db_config_disagreement(self):
        with sqlite3.connect(self.root/'after/0000.bin') as con:con.execute('UPDATE playsets_mods SET enabled=0')
        path=self.root/'result.json';record=json.loads(path.read_text(encoding='utf-8'));record['checkpoint']['files'][0]['sha256']=file_hash(self.root/'after/0000.bin');save_record(path,record)
        with self.assertRaisesRegex(FormatError,'differs from actual saved members'):session_evidence.session(self.root)
