import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import authoring,maintenance,maintenance_release,migration,publication,snapshots
from aow4mod.core import FormatError,read_acp
from aow4mod.filesystem import file_hash,inventory,save_record
import test_migration as migration_fixtures
from test_aow4_mod_audit import acp,rpk,resource,u32


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture=migration_fixtures.MigrationTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root;self.game=self.fixture.game;self.source=self.fixture.mod
        # Maintenance identity is explicit even for synthetic ACPs.
        from aow4mod import editing
        from aow4mod.core import Node
        import uuid
        raw=self.fixture.acp.read_bytes()
        self.fixture.acp.write_bytes(raw[:19]+editing.rewrite_wrapper(Node(raw,19,len(raw)),0x40009d,{0x2a:uuid.uuid4().bytes_le}))
        self.baseline=self.root/'baseline.json';save_record(self.baseline,snapshots.capture(self.source,self.game))
        self.output=self.root/'workflow'

    def start(self,baseline=True):return maintenance.start(self.source,self.output,self.game,self.baseline if baseline else None)

    def test_start_stage_preserves_original_and_reports_no_invented_repair(self):
        before=inventory(self.source);result=self.start();self.assertEqual(result['phase'],'needs_staging')
        staged=maintenance.stage(self.output);self.assertEqual(staged['phase'],'ready_to_build')
        self.assertEqual(staged['stage'],'unchanged_copy_no_automatic_repair');self.assertEqual(inventory(self.source),before)

    def test_upstream_untouched_field_rebase_is_recomputed_and_only_staged(self):
        self.fixture.pack.write_bytes(rpk([resource(value=u32(2))]));self.start()
        maintenance.stage(self.output)
        _,_,_,_,source,_=maintenance.read(self.output)
        self.assertEqual(read_acp(source/'mod.acp')[1][0]['resource'].leaves()['0x00471C6D/0x01'],u32(2))
        self.assertEqual(read_acp(self.fixture.acp)[1][0]['resource'].leaves()['0x00471C6D/0x01'],u32(0))

    def test_author_overlap_is_review_not_automatic_overwrite(self):
        from aow4mod import editing
        from aow4mod.core import Node
        raw=self.fixture.acp.read_bytes();package=read_acp(self.fixture.acp)[0]['package_id']
        changed=acp(resource(value=u32(3)))
        import uuid
        self.fixture.acp.write_bytes(changed[:19]+editing.rewrite_wrapper(Node(changed,19,len(changed)),0x40009d,{0x2a:uuid.UUID(package).bytes_le}))
        save_record(self.baseline,snapshots.capture(self.source,self.game))
        self.fixture.pack.write_bytes(rpk([resource(value=u32(2))]));self.start()
        result=maintenance.stage(self.output)
        self.assertEqual(result['phase'],'ready_to_build_with_review')
        self.assertTrue(any(f['issue']=='maintenance_author_upstream_overlap' for f in result['assessment']['findings']))

    def test_missing_baseline_is_explicit_and_stays_a_review_finding(self):
        result=self.start(False);self.assertFalse(result['assessment']['baseline_available'])
        result=maintenance.stage(self.output);self.assertEqual(result['phase'],'ready_to_build_with_review')

    def test_origin_baseline_and_working_changes_are_not_silently_reused(self):
        self.start();working=self.output/'project/mod'
        (working/'author-change.txt').write_text('edit', encoding='utf-8')
        self.assertEqual(maintenance.status(self.output)['phase'],'assessment_stale')
        with self.assertRaisesRegex(FormatError,'changed'):maintenance.stage(self.output)
        maintenance.assess(self.output)
        (self.source/'new-production-file.txt').write_text('external edit', encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'Original Mod changed'):maintenance.status(self.output)

    def test_refreshed_artifact_hash_cannot_authorize_a_forged_rebase(self):
        self.fixture.pack.write_bytes(rpk([resource(value=u32(2))]));self.start()
        state=json.loads((self.output/'maintenance.json').read_text(encoding='utf-8'));folder=self.output/state['assessment']['directory']
        plan=json.loads((folder/'rebase.json').read_text(encoding='utf-8'));plan['plans'][0]['operations'][0]['replacement_hex']=u32(99).hex()
        save_record(folder/'rebase.json',plan)
        for artifact in state['assessment']['artifacts']:
            if artifact['file'].endswith('/rebase.json'):artifact['sha256']=file_hash(folder/'rebase.json')
        save_record(self.output/'maintenance.json',state)
        with self.assertRaisesRegex(FormatError,'Recomputed migration differs'):maintenance.stage(self.output)

    def test_release_preparation_requires_a_current_build(self):
        self.start();maintenance.stage(self.output)
        with self.assertRaisesRegex(FormatError,'Build a maintenance candidate'):maintenance.prepare_release(self.output)


class MaintenanceEvidenceTests(unittest.TestCase):
    setUp=MaintenanceTests.setUp
    start=MaintenanceTests.start
    def release(self,baseline=True):
        self.start(baseline);maintenance.stage(self.output)
        result=authoring.build_project(self.output/'project',self.output/'builds/test-release',self.game,native_checks=False)
        path=Path(result['release'])/'manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'))
        # Native validation itself is covered elsewhere. These tests isolate the
        # maintenance verifier; do not claim this synthetic release is publishable.
        manifest['native_validation_requested']=True;save_record(path,manifest)
        current=snapshots.capture(path.parent/manifest['content_directory'],self.game)
        previous=snapshots.load(self.baseline) if baseline else None
        comparison=snapshots.compare(previous,current) if previous else None
        maintenance_release.attach(path,previous,current,comparison)
        return path

    def test_offline_proof_binds_payload_and_preserves_missing_baseline_gate(self):
        path=self.release(False);manifest=json.loads(path.read_text(encoding='utf-8'))
        summary=maintenance_release.verify(path.parent,manifest,path.parent/manifest['content_directory'])
        self.assertTrue(summary['review_required']);self.assertTrue(manifest['release_gate']['review_required'])
        manifest['release_gate']={'review_required':False,'issues':[]}
        with self.assertRaisesRegex(FormatError,'release gate'):maintenance_release.verify(path.parent,manifest,path.parent/manifest['content_directory'])

    def test_forged_comparison_with_refreshed_hashes_is_refused(self):
        path=self.release();manifest=json.loads(path.read_text(encoding='utf-8'));file=path.parent/'maintenance-evidence/comparison.json'
        comparison=json.loads(file.read_text(encoding='utf-8'));comparison['summary']['manual_conflicts']=99;save_record(file,comparison)
        evidence=json.loads((path.parent/'maintenance.json').read_text(encoding='utf-8'));evidence['files']['comparison.json']=file_hash(file)
        save_record(path.parent/'maintenance.json',evidence);manifest['maintenance_validation_sha256']=file_hash(path.parent/'maintenance.json')
        with self.assertRaisesRegex(FormatError,'not derived'):maintenance_release.verify(path.parent,manifest,path.parent/manifest['content_directory'])

    def test_payload_edit_is_not_hidden_by_refreshing_file_hashes(self):
        path=self.release();manifest=json.loads(path.read_text(encoding='utf-8'));content=path.parent/manifest['content_directory']
        from aow4mod import editing
        from aow4mod.core import Node
        import uuid
        package=read_acp(content/'mod.acp')[0]['package_id'];raw=acp(resource(value=u32(9)))
        (content/'mod.acp').write_bytes(raw[:19]+editing.rewrite_wrapper(Node(raw,19,len(raw)),0x40009d,{0x2a:uuid.UUID(package).bytes_le}))
        manifest['files']['mod.acp']=file_hash(content/'mod.acp')
        evidence=json.loads((path.parent/'maintenance.json').read_text(encoding='utf-8'));evidence['payload_files']=manifest['files']
        save_record(path.parent/'maintenance.json',evidence);manifest['maintenance_validation_sha256']=file_hash(path.parent/'maintenance.json')
        with self.assertRaisesRegex(FormatError,'payload resources'):maintenance_release.verify(path.parent,manifest,content)

    def test_missing_policy_or_witness_is_refused(self):
        path=self.release();manifest=json.loads(path.read_text(encoding='utf-8'));manifest.pop('maintenance_validation_requested')
        with self.assertRaisesRegex(FormatError,'policy is missing'):maintenance_release.verify(path.parent,manifest,path.parent/manifest['content_directory'])

    def test_frozen_context_is_self_contained_and_rechecks_payload(self):
        import shutil
        path=self.release();manifest=json.loads(path.read_text(encoding='utf-8'));content=path.parent/manifest['content_directory']
        stage=self.root/'frozen';stage.mkdir();shutil.copytree(content,stage/'content')
        manifest['_maintenance_root']=str(path.parent);manifest['content_directory']=str(stage/'content')
        maintenance_release.freeze(manifest,stage)
        self.assertEqual(manifest['_maintenance_root'],str(stage))
        self.assertFalse(maintenance_release.verify(stage,manifest,stage/'content')['review_required'])

    def test_preparation_records_a_verified_frozen_bundle_and_detects_its_edit(self):
        from aow4mod import native_release
        path=self.release();manifest=json.loads(path.read_text(encoding='utf-8'))
        # Isolate orchestration; native evidence semantics have their own tests.
        summary={'checks':1,'errors':0,'mismatches':0,'diagnostic_count':0,'review_required':False}
        save_record(path.parent/'native-validation.json',{'format':'synthetic-test'})
        (path.parent/'native-evidence').mkdir();(path.parent/'native-evidence/witness.bin').write_bytes(b'unit-test')
        manifest.update(native_validation_file='native-validation.json',native_validation_sha256=file_hash(path.parent/'native-validation.json'),native_validation_summary=summary)
        save_record(path,manifest)
        state=json.loads((self.output/'maintenance.json').read_text(encoding='utf-8'))
        state['build']={'manifest':path.relative_to(self.output).as_posix(),'manifest_sha256':file_hash(path),'version':'0.1'}
        save_record(self.output/'maintenance.json',state)
        with patch.object(native_release,'validate',return_value=summary):
            result=maintenance.prepare_release(self.output)
            self.assertEqual(result['phase'],'prepared_workshop_identity_missing')
            self.assertFalse(result['published']);self.assertFalse(result['installed'])
            preparation=Path(result['preparation']['directory'])/'prepared-publication.json'
            state_path=self.output/'maintenance.json';original_state=state_path.read_bytes();original_prepared=preparation.read_bytes()
            forged=json.loads(original_prepared);forged['_manifest_sha256']='0'*64;save_record(preparation,forged)
            record=json.loads(original_state)
            for artifact in record['preparation']['artifacts']:
                if artifact['file'].endswith('/prepared-publication.json'):artifact['sha256']=file_hash(preparation)
            save_record(state_path,record)
            with self.assertRaisesRegex(FormatError,'different candidate'):maintenance.status(self.output)
            state_path.write_bytes(original_state);preparation.write_bytes(original_prepared)
            preparation.write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(FormatError,'preparation changed'):maintenance.status(self.output)
