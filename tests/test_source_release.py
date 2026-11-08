import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import source_release,source_correspondence,publication
from aow4mod.core import FormatError
from aow4mod.filesystem import inventory,file_hash,save_record
from test_source_correspondence import source,body,library


class SourceReleaseTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve();self.mod=self.root/'mod';self.content=self.root/'content';self.release=self.root/'release';self.release.mkdir()
        folder=self.mod/'SourceSkeletons/LOCAL';folder.mkdir(parents=True);source(folder/'prepared.fbx');self.content.mkdir();(self.content/'Libraries').mkdir();(self.content/'Libraries/LOCAL.clb').write_bytes(library(body(),True))
        marker=folder/'.aow4mod-skeleton-v2.json';save_record(marker,{'format':'aow4mod-skeletal-import-receipt/2','prepared_source_sha256':file_hash(folder/'prepared.fbx'),'change':{'mesh':'[LOCAL]MESH\\0'}})
        self.asset={'file':'Libraries/LOCAL.clb','model':'[LOCAL]MODEL\\0','source_receipt':'SourceSkeletons/LOCAL/.aow4mod-skeleton-v2.json','source_receipt_sha256':file_hash(marker),'source_correspondence_required':True}
        self.manifest={'source_files':inventory(self.mod),'files':{'Libraries/LOCAL.clb':file_hash(self.content/'Libraries/LOCAL.clb')},'draft_skeletal_assets':[self.asset]}
        self.manifest.update(source_release.collect(self.mod,self.content,self.release,self.manifest['source_files'],[self.asset]))

    def test_evidence_recomputes_from_copied_fbx_and_real_binary_model(self):
        summary=source_release.verify(self.release,self.manifest,self.content)
        self.assertTrue(summary['local_geometry_weights_defaults_verified']);self.assertFalse(summary['source_animation_world_bind_rendering_verified'])

    def test_source_policy_cannot_be_cleared_or_disconnected(self):
        changed=copy.deepcopy(self.manifest);changed['skeletal_source_validation_requested']=False
        with self.assertRaisesRegex(FormatError,'Required'):source_release.verify(self.release,changed,self.content)
        changed=copy.deepcopy(self.manifest);changed['draft_skeletal_assets'][0]['source_correspondence_required']=False
        self.assertEqual(len(source_release.required(changed)),1)
        changed['release_gate']={'review_required':True,'issues':[{'issue':'skeletal_model_requires_bind_space_bounds_variant_review','file':'Libraries/LOCAL.clb'}]}
        with self.assertRaisesRegex(FormatError,'requirement'):publication.check_skeletal_drafts(changed,changed['files'])

    def test_forged_world_claim_refreshed_report_hash_is_rejected(self):
        report=json.loads((self.release/source_release.FILE).read_text(encoding='utf-8'));report['assets'][0]['assessment']['source_world_bind_space_verified']=True;save_record(self.release/source_release.FILE,report)
        changed=copy.deepcopy(self.manifest);changed['skeletal_source_validation_sha256']=file_hash(self.release/source_release.FILE)
        with self.assertRaisesRegex(FormatError,'recomputation'):source_release.verify(self.release,changed,self.content)

    def test_wrong_model_frame_or_palette_fails_even_when_mesh_is_unchanged(self):
        for options in ({'model_parent':0},{'map_value':1}):
            path=self.content/'Libraries/LOCAL.clb';path.write_bytes(library(body(),True,**options));changed=copy.deepcopy(self.manifest);changed['files']['Libraries/LOCAL.clb']=file_hash(path)
            report=json.loads((self.release/source_release.FILE).read_text(encoding='utf-8'));report['assets'][0]['library_sha256']=file_hash(path);save_record(self.release/source_release.FILE,report);changed['skeletal_source_validation_sha256']=file_hash(self.release/source_release.FILE)
            with self.assertRaises(FormatError):source_release.verify(self.release,changed,self.content)

    def test_frozen_replay_does_not_read_original_mod_or_library(self):
        frozen=self.root/'frozen';frozen.mkdir();content=frozen/'content';shutil.copytree(self.content,content)
        m=dict(self.manifest,content_directory=str(content),_skeletal_source_root=str(self.release));source_release.stage(m,frozen)
        original=Path.open
        def guarded(path,*a,**k):
            if path.is_relative_to(self.mod) or path.is_relative_to(self.content) or path.is_relative_to(self.release):raise AssertionError('Read original source after freeze')
            return original(path,*a,**k)
        with patch.object(Path,'open',guarded):source_release.verify(frozen,m,content)

    def test_retained_source_changes_after_import_cannot_gain_new_acceptance(self):
        source(self.mod/'SourceSkeletons/LOCAL/prepared.fbx',2)
        with self.assertRaisesRegex(FormatError,'changed after import'):source_release.collect(self.mod,self.content,self.root/'other',inventory(self.mod),[self.asset])

    def test_publication_relative_root_yields_absolute_snapshot_paths(self):
        description=self.root/'description.txt';notes=self.root/'notes.txt';description.write_text('draft', encoding='utf-8');notes.write_text('draft', encoding='utf-8')
        manifest_file=self.root/'manifest.json';save_record(manifest_file,{})
        manifest={'files':{'Libraries/LOCAL.clb':file_hash(self.content/'Libraries/LOCAL.clb')},'description_file':str(description),'description_sha256':file_hash(description),'change_notes_file':str(notes),'change_notes_sha256':file_hash(notes),'_manifest_path':str(manifest_file),'_manifest_sha256':file_hash(manifest_file)}
        cwd=Path.cwd()
        try:
            os.chdir(self.root);frozen,stage=publication.stage_release(manifest,self.content,Path('relative-snapshot'))
        finally:os.chdir(cwd)
        self.assertTrue(stage.is_absolute());self.assertTrue(Path(frozen['content_directory']).is_absolute());self.assertTrue(Path(frozen['_source_manifest_snapshot']).is_absolute())


if __name__=='__main__':unittest.main()
