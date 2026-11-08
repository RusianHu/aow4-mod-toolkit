import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import authoring, content_libraries, editing, native, native_inspection
from aow4mod import native_objects, native_release, native_roundtrip, publication, native_overlays, native_overlay_release
from aow4mod import native_dependencies,overlay_imports
from aow4mod.core import FormatError, Node
from aow4mod.filesystem import file_hash, inventory, save_record
from test_aow4_mod_audit import acp, rpk, resource, string
from test_content_libraries import library_bytes


class NativeReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.content=self.root/'source';self.content.mkdir()
        obj=resource();obj=editing.rewrite_typed(Node(obj,0,len(obj)),{0x14:string('[#0000000000000001]0\\0')})
        raw=acp(obj);body=editing.rewrite_wrapper(Node(raw,19,len(raw)),0x40009d,{0x2a:uuid.uuid4().bytes_le})
        (self.content/'mod.acp').write_bytes(raw[:19]+body)
        (self.content/'own.rpk').write_bytes(rpk([resource(rid=2)]))
        (self.content/'fixture.clb').write_bytes(library_bytes())
        (self.content/'unsupported.bin').write_bytes(b'not native checked')
        self.game=self.root/'game';(self.game/'Content').mkdir(parents=True)
        (self.game/'Content/base.rpk').write_bytes(rpk([obj]))
        self.binding={'game':str(self.game),'dll_sha256':next(iter(native.PROFILES)),
                      'backend_sha256':'fixture','profile':next(iter(native.PROFILES.values()))}
        self.public=native.public_binding(self.binding);self.counter=0
        self.rpk_loss=False;self.clb_pixels=False;self.diagnostics=[]
        self.mocks=[]
        for name,value in [('native.profile_for',self.binding),('native_inspection.inspect',self.inspect),
                           ('native_roundtrip.run',self.roundtrip),('native_assets.export_library',self.export)]:
            kwargs={'return_value':value} if isinstance(value,dict) else {'side_effect':value}
            mock=patch('aow4mod.native_release.'+name,**kwargs);self.mocks.append(mock.start());self.addCleanup(mock.stop)
        mock=patch('aow4mod.native_overlay_release.native_overlays.run',side_effect=self.overlay)
        mock.start();self.addCleanup(mock.stop)

    def overlay(self,base,mods,game,output,*args,**kwargs):
        output=Path(output);output.mkdir();info,by_id,records,inputs,ignored,targets=native_overlays.collect_records(base,mods)
        data=Path(base).read_bytes();extra=[];preparation=None
        if native_dependencies.needed(records):
            before=output/'prepare-baseline.rpk';before.write_bytes(data)
            data=native_dependencies.expected(data,records)
            prepared=output/'prepared.rpk';prepared.write_bytes(data)
            prep_record=output/'prepare-record.bin';prep_record.write_bytes(bytes.fromhex(records[0]['body_hex']))
            extra.extend([{'kind':kind,'step':index,'file':path.name,'sha256':file_hash(path)} for kind,index,path in
                          [('prepare_baseline',None,before),('prepare_step',0,prepared),('prepared',None,prepared),('prepare_mod_data',0,prep_record)]])
            preparation={'entries':[[{'id':row['id'],'cid':row['cid'],'operation':0x40,'applied_before':0,'applied_after':0}
                                    for row in records[0]['resources']]]}
        from aow4mod import projects
        context_packs=[]
        for row in projects.inspect_project(mods[0])['packs']:
            if not row['registered'] or row['name'].casefold()==info['name'].casefold():continue
            path=output/f'context-{len(context_packs)}.rpk';path.write_bytes(Path(row['path']).read_bytes())
            extra.append({'kind':'context_pack','step':len(context_packs),'file':path.name,'sha256':file_hash(path)})
            context_packs.append({'file':row['path'],'name':row['name'],'sha256':file_hash(path),'artifact':path.name})
        baseline=output/'baseline.rpk';baseline.write_bytes(data)
        updates={row['resource'].rid:row['resource'] for row in targets[0]}
        data=editing.transform_records(data,'.rpk',lambda pack,node:editing.rewrite_typed(updates[rid].node,
            {0x14:by_id[rid].fields[0x14].raw()}) if (rid:=native_overlays.Resource.parse(node).rid) in updates else node.raw())
        merged=output/'merged.rpk';merged.write_bytes(data);record_file=output/'mod-data.bin';record_file.write_bytes(bytes.fromhex(records[0]['body_hex']))
        return {'binding':self.public,'base':{'file':str(base),'sha256':file_hash(base),'pack':info['name']},
            'artifacts':[{'kind':kind,'step':None if kind=='baseline' else 0,'file':path.name,'sha256':file_hash(path)}
                for kind,path in [('baseline',baseline),('step',merged),('mod_data',record_file)]]+extra,
            'proof':{'steps':[{'entries':[{'id':row['id'],'cid':row['cid'],'operation':0x40,'applied_before':0,'applied_after':1}
                for row in records[0]['resources']]}]},'preparation':preparation,'context_packs':context_packs,
            'diagnostics':self.diagnostics,'coverage':'synthetic native application transport'}

    def with_dependency(self):
        from test_pack_imports import named_pack
        path=self.content/'mod.acp';path.write_bytes(overlay_imports.rewrite(path.read_bytes(),'TEST.RPK',['LEAF.RPK']))
        (self.content/'Leaf.rpk').write_bytes(named_pack([resource(rid=7)],'LEAF.RPK'))

    def test_frozen_dependency_preparation_and_context_are_independently_checked(self):
        self.with_dependency();report,out=self.collect();record,path,detail=self.overlay_detail(report,out)
        self.assertTrue(detail['comparison']['dependency_preparation']['dependency_preparation_matched'])
        self.assertEqual(len(detail['context_packs']),1)
        (self.game/'Content/base.rpk').unlink()
        with patch('aow4mod.native.profile_for',side_effect=AssertionError('offline must not start native code')):
            self.assertEqual(native_release.validate(report,self.content,out,7),report['summary'])
        detail['context_packs']=[];self.refresh_overlay(report,out,record,path,detail)
        with self.assertRaisesRegex(FormatError,'context coverage'):native_release.validate(report,self.content,out,7)

    def test_refreshed_prepared_metadata_cannot_hide_unrequested_change(self):
        self.with_dependency();report,out=self.collect();record,path,detail=self.overlay_detail(report,out)
        prepared=out/detail['artifacts']['prepared']['file'];raw=prepared.read_bytes();node=Node(raw,176,len(raw))
        body=editing.rewrite_map(node,{0x99:b'not declared'})
        prepared.write_bytes(raw[:12]+len(body).to_bytes(4,'little')+raw[16:176]+body)
        for key in ('prepared','prepare_step_0'):detail['artifacts'][key]['sha256']=file_hash(prepared)
        self.refresh_overlay(report,out,record,path,detail)
        with self.assertRaisesRegex(FormatError,'comparison differs'):native_release.validate(report,self.content,out,7)
        detail['comparison']=native_overlay_release.assess(detail,self.content,out)
        self.refresh_overlay(report,out,record,path,detail)
        with self.assertRaisesRegex(FormatError,'hides'):native_release.validate(report,self.content,out,7)

    def test_context_payload_binding_and_preparation_downgrade_are_rejected(self):
        self.with_dependency();report,out=self.collect();record,path,detail=self.overlay_detail(report,out)
        original=copy.deepcopy(detail);context=detail['context_packs'][0];key=context['key']
        copied=out/detail['artifacts'][key]['file']
        copied.write_bytes(editing.transform_records(copied.read_bytes(),'.rpk',
            lambda p,n:editing.rewrite_typed(n,{0x15:string('changed dependency')})))
        context['sha256']=file_hash(copied);detail['artifacts'][key]['sha256']=file_hash(copied)
        self.refresh_overlay(report,out,record,path,detail)
        with self.assertRaisesRegex(FormatError,'bundled RPK'):native_release.validate(report,self.content,out,7)
        copied.write_bytes((self.content/'Leaf.rpk').read_bytes());detail=original
        detail['format']=native_overlay_release.LEGACY_FORMAT
        self.refresh_overlay(report,out,record,path,detail)
        with self.assertRaisesRegex(FormatError,'protocol requires'):native_release.validate(report,self.content,out,7)

    def test_previous_protocol_remains_readable_and_new_manifest_blocks_it(self):
        report,out=self.collect();previous=copy.deepcopy(report)
        previous.update(format=native_release.PREVIOUS_FORMAT,scope=native_release.PREVIOUS_SCOPE)
        previous['summary']=native_release.summary(previous['checks'],2)
        self.assertEqual(native_release.validate(previous,self.content,out,2),previous['summary'])
        with self.assertRaisesRegex(FormatError,'protocol'):native_release.validate(previous,self.content,out,7)
        context=copy.deepcopy(report);context.update(format=native_release.CONTEXT_FORMAT,scope=native_release.CONTEXT_SCOPE,
                                                     summary=native_release.summary(context['checks'],3))
        self.assertEqual(native_release.validate(context,self.content,out,3),context['summary'])
        with self.assertRaisesRegex(FormatError,'protocol'):native_release.validate(context,self.content,out,7)

    def inspect(self,source,game,rid=None,pack=None,**kwargs):
        self.counter+=1;body,selected=native_inspection.source_body(source,rid,pack)
        witness=self.root/f'body-{self.counter}.bin';witness.write_bytes(body)
        omitted=self.binding['profile']['package_omitted_fields'] if rid is None else None
        return {'format':'aow4mod-native-inspection/1','binding':self.public,'source':selected,
                'roundtrip_artifact':{'file':str(witness),'sha256':file_hash(witness)},
                'comparison':native_objects.compare_bodies(body,body,omitted)}

    def roundtrip(self,source,game,output,*args,**kwargs):
        output=Path(output);output.mkdir(parents=True);saved=output/Path(source).name
        saved.write_bytes(rpk([]) if self.rpk_loss else Path(source).read_bytes())
        return {'format':'aow4mod-native-roundtrip/1','binding':self.public,'output_file':str(saved),
                'comparison':native_roundtrip.compare(source,saved),'diagnostics':self.diagnostics}

    def export(self,source,game,output,*args):
        output=Path(output);output.mkdir(parents=True);saved=output/'fixture.clb'
        saved.write_bytes(library_bytes(pixel=b'changed') if self.clb_pixels else Path(source).read_bytes())
        return {'format':'aow4mod-native-assets/1','binding':self.public,'source_sha256':file_hash(source),
                'files':[{'name':saved.name,'sha256':file_hash(saved)}],
                'comparison':content_libraries.compare(source,saved),'diagnostics':[]}

    def collect(self):
        out=self.root/'release';out.mkdir()
        return native_release.collect(self.content,self.game,out,self.root/'cache',_protocol=7),out

    def overlay_detail(self,report,out):
        record=next(row for row in report['checks'] if row['operation']=='acp_apply')
        path=out/record['detail_file'];return record,path,json.loads(path.read_text(encoding='utf8'))

    def refresh_overlay(self,report,out,record,path,detail):
        save_record(path,detail);record['detail_sha256']=file_hash(path)
        report['evidence_files']=inventory(out/'native-evidence')

    def test_frozen_english_catalog_context_is_bound_to_payload(self):
        try:import polib
        except ImportError:self.skipTest('optional localization dependency')
        p=self.content/'Language/EN/custom.mo';p.parent.mkdir(parents=True)
        po=polib.POFile();po.metadata={'Content-Type':'text/plain; charset=UTF-8'}
        po.append(polib.POEntry(msgid='CUSTOM@NAME',msgstr='Original'));p.write_bytes(po.to_binary())
        report,out=self.collect()
        record=next(x for x in report['checks'] if x['operation']=='rpk_roundtrip')
        detail=json.loads((out/record['detail_file']).read_text(encoding='utf8'))
        self.assertEqual(detail['context_languages'],[{'file':'Language/EN/custom.mo','sha256':file_hash(p)}])
        native_release.validate(report,self.content,out,7)
        po[0].msgstr='Changed after native read';p.write_bytes(po.to_binary())
        with self.assertRaisesRegex(FormatError,'MO context'):native_release.validate(report,self.content,out,7)

    def test_v4_requires_every_overlay_target_and_manifest_protocol_blocks_downgrade(self):
        report,out=self.collect();self.assertEqual(report['summary']['operations']['acp_apply'],1)
        missing=copy.deepcopy(report);missing['checks']=[row for row in missing['checks'] if row['operation']!='acp_apply']
        with self.assertRaisesRegex(FormatError,'coverage'):native_release.validate(missing,self.content,out,7)
        legacy=copy.deepcopy(missing);legacy.update(format=native_release.LEGACY_FORMAT,scope=native_release.LEGACY_SCOPE,
            summary=native_release.summary(legacy['checks'],1))
        self.assertEqual(native_release.validate(legacy,self.content,out,1),legacy['summary'])
        with self.assertRaisesRegex(FormatError,'protocol'):native_release.validate(legacy,self.content,out,7)

    def test_refreshed_merged_snapshot_hash_cannot_hide_wrong_applied_fields(self):
        report,out=self.collect();record,path,detail=self.overlay_detail(report,out)
        merged=out/detail['artifacts']['merged']['file'];data=merged.read_bytes()
        data=editing.transform_records(data,'.rpk',lambda pack,node:editing.rewrite_typed(node,
            {0x471c6d:editing.rewrite_map(node.typed()[1][0x471c6d],{1:b'\x01\0\0\0'})}))
        merged.write_bytes(data);record['artifact_sha256']=file_hash(merged);detail['artifacts']['merged']['sha256']=file_hash(merged)
        self.refresh_overlay(report,out,record,path,detail)
        with self.assertRaisesRegex(FormatError,'comparison differs'):native_release.validate(report,self.content,out)
        detail['comparison']=native_overlay_release.assess(detail,self.content,out)
        self.refresh_overlay(report,out,record,path,detail)
        with self.assertRaisesRegex(FormatError,'hides'):native_release.validate(report,self.content,out)

    def test_changed_base_hash_and_skipped_marker_are_not_native_success(self):
        report,out=self.collect();record,path,detail=self.overlay_detail(report,out)
        detail['entries'][0]['applied_after']=0;self.refresh_overlay(report,out,record,path,detail)
        with self.assertRaisesRegex(FormatError,'comparison differs'):native_release.validate(report,self.content,out)
        detail['entries'][0]['applied_after']=1;base=out/detail['artifacts']['base']['file'];data=bytearray(base.read_bytes());data[100]=1;base.write_bytes(data)
        detail['artifacts']['base']['sha256']=file_hash(base);self.refresh_overlay(report,out,record,path,detail)
        with self.assertRaisesRegex(FormatError,'identity/hash'):native_release.validate(report,self.content,out)

    def test_frozen_overlay_checks_work_without_installed_base_or_native_backend(self):
        report,out=self.collect();(self.game/'Content/base.rpk').unlink()
        with patch('aow4mod.native.profile_for',side_effect=AssertionError('offline validation must not load DLL')):
            self.assertEqual(native_release.validate(report,self.content,out,7),report['summary'])

    def test_overlay_base_resolution_refuses_ambiguity_and_accepts_explicit_selection(self):
        selected=self.game/'Content/base.rpk';other=self.game/'Content/other.rpk';other.write_bytes(rpk([resource(rid=99)]))
        with self.assertRaisesRegex(FormatError,'ambiguous'):native_overlay_release.resolve_base('TEST.RPK',self.game,content=self.content)
        self.assertEqual(native_overlay_release.resolve_base('TEST.RPK',self.game,content=self.content,explicit={'test.rpk':selected}),selected.resolve())

    def test_manifest_protocol_and_frozen_overlay_copies_survive_source_cleanup(self):
        result,path=self.built();value=json.loads(path.read_text(encoding='utf-8'));self.assertEqual(value['native_validation_protocol'],8)
        manifest,content=publication.load_release(path);frozen,stage=publication.stage_release(manifest,content,path.parent)
        self.assertFalse((stage/'base.rpk').exists())
        (self.game/'Content/base.rpk').unlink();(path.parent/'native-evidence').rename(path.parent/'old-evidence')
        publication.check_staged(frozen)
        value['native_validation_protocol']=1;save_record(path,value)
        with self.assertRaisesRegex(FormatError,'protocol'):publication.load_release(path)

    def test_all_targets_are_checked_and_verification_uses_frozen_binaries_without_dll(self):
        report,out=self.collect()
        self.assertEqual(report['summary']['checks'],5);self.assertEqual(report['summary']['passed'],5)
        self.assertFalse(report['summary']['review_required']);self.assertIn('unsupported.bin',report['not_native_checked'])
        for mock in self.mocks:mock.reset_mock()
        self.assertEqual(native_release.validate(report,self.content,out),report['summary'])
        for mock in self.mocks:mock.assert_not_called()

    def test_partial_coverage_and_hidden_summary_are_refused(self):
        report,out=self.collect();bad=copy.deepcopy(report);bad['checks'].pop()
        with self.assertRaisesRegex(FormatError,'coverage'):native_release.validate(bad,self.content,out)
        bad=copy.deepcopy(report);bad['summary']['checks']=0
        with self.assertRaisesRegex(FormatError,'summary'):native_release.validate(bad,self.content,out)
        bad=copy.deepcopy(report);bad['not_native_checked']=[]
        with self.assertRaisesRegex(FormatError,'unchecked'):native_release.validate(bad,self.content,out)

    def test_resource_loss_and_pixel_changes_enter_review_even_with_successful_native_return(self):
        self.rpk_loss=True;self.clb_pixels=True;report,out=self.collect()
        self.assertEqual(report['summary']['mismatches'],2)
        self.assertTrue(report['summary']['review_required']);self.assertEqual(report['summary']['errors'],0)
        self.assertEqual(native_release.validate(report,self.content,out),report['summary'])

    def test_rehashed_changed_artifact_still_fails_independent_comparison(self):
        report,out=self.collect();record=next(r for r in report['checks'] if r['operation']=='rpk_roundtrip')
        artifact=out/record['artifact'];artifact.write_bytes(rpk([]))
        record['artifact_sha256']=file_hash(artifact);report['evidence_files']=inventory(out/'native-evidence')
        with self.assertRaisesRegex(FormatError,'comparison differs'):native_release.validate(report,self.content,out)

    def test_native_diagnostics_cannot_be_hidden_by_changing_status_or_gate(self):
        self.diagnostics=['Warning: retained diagnostic'];report,out=self.collect()
        self.assertTrue(report['summary']['review_required'])
        record=next(r for r in report['checks'] if r['operation']=='rpk_roundtrip');record['state']='passed'
        with self.assertRaisesRegex(FormatError,'hides'):native_release.validate(report,self.content,out)

    def test_unavailable_backend_is_explicitly_incomplete(self):
        with patch('aow4mod.native_release.native.profile_for',side_effect=FormatError('unsupported DLL')):
            report,out=self.collect()
        self.assertEqual(report['summary']['errors'],5);self.assertTrue(report['summary']['review_required'])
        self.assertTrue(all('unsupported DLL' in r['error'] for r in report['checks']))
        self.assertEqual(native_release.validate(report,self.content,out),report['summary'])

    def test_input_change_and_changed_detail_are_refused(self):
        report,out=self.collect();record=report['checks'][0]
        (out/record['detail_file']).write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'files changed'):native_release.validate(report,self.content,out)
        (self.content/'own.rpk').write_bytes(rpk([resource(rid=99)]))
        with self.assertRaisesRegex(FormatError,'payload'):native_release.validate(report,self.content,out)

    def built(self):
        project=self.root/'project';authoring.import_project(self.content,project)
        result=authoring.build_project(project,self.root/'built',self.game,native_checks=True)
        return result,self.root/'built/manifest.json'

    def test_build_and_publication_gate_bind_native_evidence(self):
        self.diagnostics=['Warning: review me'];result,path=self.built()
        self.assertEqual(result['native_validation_summary']['checks'],5)
        manifest,content=publication.load_release(path)
        self.assertTrue(manifest['_review_required'])
        value=json.loads(path.read_text(encoding='utf-8'));value['release_gate']['issues']=[i for i in value['release_gate']['issues'] if i['issue']!='native_validation_review_required']
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'Native findings'):publication.load_release(path)

    def test_publication_rejects_missing_requested_evidence(self):
        result,path=self.built();value=json.loads(path.read_text(encoding='utf-8'));value.pop('native_validation_file')
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'evidence is missing'):publication.load_release(path)

    def test_upload_freezes_native_evidence_and_detects_later_changes(self):
        result,path=self.built();manifest,content=publication.load_release(path)
        frozen,stage=publication.stage_release(manifest,content,path.parent)
        (path.parent/'native-evidence').rename(path.parent/'old-native-evidence')
        publication.check_staged(frozen)
        proof=Path(frozen['_native_evidence_root'])/'native-evidence'
        body=next(proof.rglob('body.bin'));body.write_bytes(b'changed')
        with self.assertRaisesRegex(FormatError,'files changed'):publication.check_staged(frozen)

    def test_native_policy_and_cache_path_are_validated_before_writes(self):
        project=self.root/'project';authoring.import_project(self.content,project)
        config=project/authoring.CONFIG;value=json.loads(config.read_text(encoding='utf-8'));value['validation']={'native':'yes'}
        save_record(config,value)
        with self.assertRaisesRegex(FormatError,'boolean'):authoring.build_project(project,self.root/'invalid',self.game)
        value['validation']={'native':True,'native_cache':'mod/cache'};save_record(config,value)
        with self.assertRaises(FormatError):authoring.build_project(project,self.root/'bad-cache',self.game)
        self.assertFalse((project/'mod/cache').exists())

    def test_direct_collection_cannot_write_into_game_or_payload(self):
        before=inventory(self.content)
        for out,cache in [(self.content,self.root/'cache'),(self.game,self.root/'cache'),
                          (self.root,self.content/'cache')]:
            with self.subTest(output=out,cache=cache),self.assertRaises(FormatError):
                native_release.collect(self.content,self.game,out,cache)
        self.assertEqual(inventory(self.content),before)

    def test_context_cannot_be_removed_even_after_rehashing_detail_and_inventory(self):
        report,out=self.collect();record=next(x for x in report['checks'] if x['operation']=='rpk_roundtrip')
        detail_file=out/record['detail_file'];detail=json.loads(detail_file.read_text(encoding='utf-8'));detail['context_libraries']=[]
        save_record(detail_file,detail);record['detail_sha256']=file_hash(detail_file)
        report['evidence_files']=inventory(out/'native-evidence')
        with self.assertRaisesRegex(FormatError,'CLB context'):native_release.validate(report,self.content,out)


if __name__=='__main__':unittest.main()
