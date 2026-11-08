import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import authoring,editing,maintenance,maintenance_context,maintenance_release,snapshots
from aow4mod.core import FormatError,Node,read_acp
from aow4mod.filesystem import file_hash,inventory,save_record
from test_aow4_mod_audit import acp,rpk,resource,u32


class ContextTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.game=self.root/'game';(self.game/'Content').mkdir(parents=True)
        (self.game/'Content/test.rpk').write_bytes(rpk([resource()]))
        self.origin=self.mod('Primary',0);self.other=self.mod('Other',3)
        self.config=self.root/'data/content_load.json';self.config.parent.mkdir()
        save_record(self.config,{'enabledMods':[{'path':str(self.other)},{'path':str(self.origin)}],'disabledDLC':['kept'],'enabledUGC':[],'unknown':{'preserve':1}})
        self.candidate=self.root/'candidate';shutil.copytree(self.origin,self.candidate)
        self.context=self.root/'context'

    def mod(self,name,value):
        root=self.root/name;root.mkdir();raw=acp(resource(value=u32(value)))
        raw=raw[:19]+editing.rewrite_wrapper(Node(raw,19,len(raw)),0x40009d,{0x2a:uuid.uuid4().bytes_le})
        (root/'mod.acp').write_bytes(raw);return root

    def capture(self):return maintenance_context.capture(self.origin,self.candidate,self.config,self.context)

    def test_order_primary_substitution_and_conflict_are_replayed_from_raw_witnesses(self):
        before=self.config.read_bytes();self.capture()
        result=maintenance_context.verify(self.context,self.candidate)
        self.assertEqual(result['summary']['enabled_mods'],2);self.assertEqual(result['summary']['primary_slot'],1)
        self.assertEqual(result['summary']['primary_related_differences'],1);self.assertTrue(result['summary']['review_required'])
        self.assertEqual(result['conflicts']['projects'][0]['path'],str(self.other));self.assertEqual(result['conflicts']['projects'][1]['path'],str(self.origin))
        self.assertFalse(result['conflicts']['runtime_winner_verified']);self.assertEqual(self.config.read_bytes(),before)
        self.assertTrue((self.context/'mods/0000/mod.acp').exists())

    def test_no_enabled_mod_can_be_omitted_even_with_refreshed_record_hashes(self):
        self.capture();record=json.loads((self.context/'context.json').read_text(encoding='utf-8'));record['mods'].pop(0);save_record(self.context/'context.json',record)
        with self.assertRaisesRegex(FormatError,'omitted/added'):maintenance_context.verify(self.context,self.candidate)

    def test_witness_edit_or_omission_and_primary_identity_change_are_refused(self):
        self.capture();witness=self.context/'mods/0000/mod.acp';raw=witness.read_bytes();witness.write_bytes(raw+b'extra')
        with self.assertRaisesRegex(FormatError,'files changed'):maintenance_context.verify(self.context,self.candidate)
        witness.write_bytes(raw);(self.candidate/'mod.acp').write_bytes((self.other/'mod.acp').read_bytes())
        with self.assertRaisesRegex(FormatError,'different identity'):maintenance_context.verify(self.context,self.candidate)

    def test_live_change_invalidates_context_without_invalidating_offline_history(self):
        self.capture();self.config.write_text(self.config.read_text(encoding='utf-8')+'\n', encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'playset changed'):maintenance_context.validate_live(self.context)
        self.assertTrue(maintenance_context.verify(self.context,self.candidate)['summary']['review_required'])

    def test_missing_primary_and_unreadable_active_mod_do_not_fall_back_to_empty_context(self):
        save_record(self.config,{'enabledMods':[{'path':str(self.other)}]})
        with self.assertRaisesRegex(FormatError,'uniquely enabled'):self.capture()
        save_record(self.config,{'enabledMods':[{'path':str(self.origin)},{'path':str(self.root/'missing')} ]})
        with self.assertRaisesRegex(FormatError,'unreadable'):self.capture()
        self.assertFalse(self.context.exists())

    def test_workflow_binds_validation_order_and_live_context(self):
        baseline=self.root/'baseline.json';save_record(baseline,snapshots.capture(self.origin,self.game))
        workspace=self.root/'workflow';maintenance.start(self.origin,workspace,self.game,baseline)
        result=maintenance.set_context(workspace,self.config)
        self.assertEqual(result['playset_context']['enabled_mods'],2)
        self.assertTrue(result['assessment']['review_required'])
        maintenance.stage(workspace)
        config=workspace/'project/aow4mod.project.json';data=json.loads(config.read_text(encoding='utf-8'))
        self.assertEqual(data['validation']['mod_order'],[str(self.other),'@primary'])
        data['validation']['mod_order']=['@primary',str(self.other)];save_record(config,data)
        with self.assertRaisesRegex(FormatError,'context/order changed'):maintenance.assess(workspace)

    def test_frozen_release_gate_cannot_hide_context_report_edits(self):
        self.capture();workspace=self.root/'project';authoring.import_project(self.candidate,workspace)
        release=self.root/'release';authoring.build_project(workspace,release,self.game,native_checks=False)
        path=release/'manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'));manifest['native_validation_requested']=True;save_record(path,manifest)
        current=snapshots.capture(release/manifest['content_directory'],self.game)
        maintenance_release.attach(path,current,current,snapshots.compare(current,current),self.context)
        manifest=json.loads(path.read_text(encoding='utf-8'));self.assertTrue(manifest['maintenance_validation_summary']['review_required'])
        proof=json.loads((release/'maintenance.json').read_text(encoding='utf-8'));check=release/'maintenance-context-check.json'
        report=json.loads(check.read_text(encoding='utf-8'));report['summary']['different_definitions']=0;save_record(check,report)
        proof['context']['check_sha256']=file_hash(check);save_record(release/'maintenance.json',proof);manifest['maintenance_validation_sha256']=file_hash(release/'maintenance.json')
        with self.assertRaisesRegex(FormatError,'raw Mod witnesses'):maintenance_release.verify(release,manifest,release/manifest['content_directory'])
