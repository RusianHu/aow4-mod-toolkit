import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import editing,map_entities,map_world_entities,maps,native,native_map_release,native_release,native_world_entities,native_world_release,publication
from aow4mod.core import FormatError
from aow4mod.filesystem import file_hash,inventory,save_record
from test_map_entity_copies import fixture
from test_map_world_entities import put_cell,proof_report
import test_native_map_release as legacy_maps


class NativeWorldReleaseTests(unittest.TestCase):
    # Reuse the synthetic serialization transport, not the protocol-5 test cases.
    def setUp(self):
        legacy_maps.NativeMapReleaseTests.setUp(self);self.source.write_bytes(put_cell(fixture()));self.world_calls=[];self.world_diagnostics=[];self.direction_failure=False
        mocked=patch('aow4mod.native_world_release.native_world_entities.run',side_effect=self.world)
        mocked.start();self.addCleanup(mocked.stop)

    def collect(self):return native_release.collect(self.content,self.game,self.release,self.cache,_protocol=6)

    roundtrip=legacy_maps.NativeMapReleaseTests.roundtrip
    refresh=legacy_maps.NativeMapReleaseTests.refresh

    def world(self,source,game,output,ids,cache,timeout,libraries=(),packs=()):
        self.world_calls.append(list(ids));Path(output).parent.mkdir(parents=True,exist_ok=True)
        data=Path(source).read_bytes();report=proof_report(data,output)
        report['entities']=[r for r in report['entities'] if int(r['id'],16) in ids]
        if self.direction_failure and report['entities']:
            report['entities'][0].update(direction_status=-1,direction=None)
        run=Path(output)/'run';run.mkdir();(run/'stdout.log').write_text('Native fixture\n'+'\n'.join(self.world_diagnostics)+'\n',encoding='utf-8')
        from aow4mod import native_validation
        report.update(source=str(source),binding=self.public,run=str(run),objects_evaluated=bool(ids),
            context_libraries=native.library_context(libraries),context_packs=native_map_release.native_context.bind(packs),
            diagnostics=native_validation.diagnostics(run))
        return report

    def detail(self,report):
        record=next(r for r in report['checks'] if r['operation']=='tam_world_entities');path=self.release/record['detail_file']
        return record,path,json.loads(path.read_text(encoding='utf-8'))

    def index(self,detail):
        path=self.release/detail['index']['file'];return path,json.loads(path.read_text(encoding='utf-8'))

    def refresh_index(self,report,record,path,detail,index_path,index):
        save_record(index_path,index);detail['index']['sha256']=file_hash(index_path);record['artifact_sha256']=file_hash(index_path)
        self.refresh(report,record,path,detail)

    def manifest(self,report):
        path=legacy_maps.NativeMapReleaseTests.manifest(self,report);value=json.loads(path.read_text(encoding='utf-8'));value['native_validation_protocol']=6;save_record(path,value);return path

    def many(self,count):
        data=put_cell(fixture());d=maps.decode(data);world=d['map'][0x14];_,wf=world.typed();scope=wf[map_entities.WORLD_CID];manager=scope.mapping()[2];objects=manager.mapping()[0xa]
        example=map_entities.records(data)[0];values=[]
        for i in range(count):
            values.append(editing.rewrite_typed(example['node'],{map_entities.ENTITY_SCOPE:editing.rewrite_map(example['fields'][map_entities.ENTITY_SCOPE],{1:(0x40010000+i).to_bytes(4,'little')})}))
        manager=editing.rewrite_map(manager,{0xa:editing.replace_array_items(objects,values)});scope=editing.rewrite_map(scope,{2:manager})
        world=editing.rewrite_typed(world,{map_entities.WORLD_CID:scope})
        return d['envelope']+d['header_bytes']+editing.rewrite_map(d['body'],{0x14:world})

    def test_protocol_six_freezes_world_objects_cells_and_rechecks_without_cache(self):
        report=self.collect();self.assertEqual(report['format'],native_release.WORLD_FORMAT);self.assertEqual(report['summary']['checks'],2)
        self.assertEqual(report['summary']['world_entity_count'],3);self.assertEqual(report['summary']['world_entity_issue_count'],0)
        self.assertEqual(report['summary']['passed'],2);record,path,detail=self.detail(report);idx,index=self.index(detail)
        self.assertEqual(len(index['batches'][0]['entities']),3);self.assertTrue(index['batches'][0]['objects_evaluated'])
        self.cache.rename(self.root/'no-cache')
        with patch.object(native_world_entities,'run',side_effect=AssertionError('offline must not invoke DLL')):
            native_release.validate(report,self.content,self.release,6)

    def test_large_world_has_fixed_complete_batches_and_shared_cell_files(self):
        data=put_cell(put_cell(self.many(255),eid=0x4000000a,slot=1),eid=0x4000000b,slot=3)
        self.source.write_bytes(data);report=self.collect()
        self.assertEqual([len(x) for x in self.world_calls],[256,2]);self.assertEqual(report['summary']['world_entity_count'],258)
        _,_,detail=self.detail(report);_,index=self.index(detail);self.assertEqual(len(index['batches']),2)
        self.assertEqual(detail['comparison']['batch_count'],2)
        first=index['batches'][0]['entities'][-1]['cell_file'];second=index['batches'][1]['entities'][0]['cell_file']
        self.assertEqual(first,second);self.assertEqual(len(list((self.release/detail['index']['file']).parent.glob('objects/cell-*.bin'))),1)

    def test_missing_batch_or_replaced_selection_is_refused_after_refreshing_hashes(self):
        self.source.write_bytes(self.many(258));report=self.collect();record,path,detail=self.detail(report);idx,index=self.index(detail)
        original=copy.deepcopy(index);index['batches'].pop();self.refresh_index(report,record,path,detail,idx,index)
        with self.assertRaisesRegex(FormatError,'batch coverage'):native_release.validate(report,self.content,self.release,6)
        index=original;index['batches'][1]['selection']=index['batches'][0]['selection'];self.refresh_index(report,record,path,detail,idx,index)
        with self.assertRaisesRegex(FormatError,'selection'):native_release.validate(report,self.content,self.release,6)

    def test_raw_log_prevents_clearing_diagnostics_in_index_and_detail(self):
        self.world_diagnostics=['Warning: retained world diagnostic'];report=self.collect();record,path,detail=self.detail(report);idx,index=self.index(detail)
        self.assertEqual(record['diagnostic_count'],1);index['batches'][0]['diagnostics']=[];detail['diagnostics']=[]
        self.refresh_index(report,record,path,detail,idx,index)
        with self.assertRaisesRegex(FormatError,'raw log'):native_release.validate(report,self.content,self.release,6)

    def test_batch_log_roles_cannot_alias_a_different_run(self):
        self.source.write_bytes(self.many(258));report=self.collect();record,path,detail=self.detail(report);idx,index=self.index(detail)
        index['batches'][1]['log']=copy.deepcopy(index['batches'][0]['log']);self.refresh_index(report,record,path,detail,idx,index)
        with self.assertRaisesRegex(FormatError,'log role'):native_release.validate(report,self.content,self.release,6)

    def test_refreshed_object_and_cell_hashes_cannot_hide_unrequested_body_data(self):
        report=self.collect();record,path,detail=self.detail(report);idx,index=self.index(detail)
        row=index['batches'][0]['entities'][0];object_file=idx.parent/'objects'/row['file'];raw=object_file.read_bytes()
        from aow4mod.core import Node
        object_file.write_bytes(editing.rewrite_map(Node(raw,0,len(raw)),{0x99:b'forged extra body'}));row['sha256']=file_hash(object_file)
        self.refresh_index(report,record,path,detail,idx,index)
        with self.assertRaisesRegex(FormatError,'summaries differ'):native_release.validate(report,self.content,self.release,6)

    def test_inconsistent_known_ids_are_explicit_errors_not_unchecked_tam(self):
        self.source.write_bytes(put_cell(fixture(),eid=0x40000007));report=self.collect()
        check=next(r for r in report['checks'] if r['operation']=='tam_world_entities')
        self.assertEqual(check['state'],'error');self.assertIn('duplicate',check['error']);self.assertNotIn('map.tam',report['not_native_checked'])

    def test_wrong_object_identity_or_cell_witness_cannot_be_hidden_by_refreshed_hashes(self):
        report=self.collect();record,path,detail=self.detail(report);idx,index=self.index(detail)
        cell=next(r for r in index['batches'][0]['entities'] if r['location']['origin']=='cell')
        cell['cell_file']='../escaped.bin';self.refresh_index(report,record,path,detail,idx,index)
        with self.assertRaisesRegex(FormatError,'cell filename'):native_release.validate(report,self.content,self.release,6)

    def test_identity_summary_and_context_are_rederived_from_payload(self):
        report=self.collect();record,path,detail=self.detail(report);idx,index=self.index(detail)
        index['identity_summary']['count']=1;self.refresh_index(report,record,path,detail,idx,index)
        with self.assertRaisesRegex(FormatError,'identity index'):native_release.validate(report,self.content,self.release,6)

    def test_getter_failure_cannot_be_hidden_by_zero_diagnostics_or_changed_summary(self):
        self.direction_failure=True;report=self.collect();self.assertEqual(report['summary']['diagnostic_count'],0)
        self.assertEqual(report['summary']['world_entity_issue_count'],1);self.assertTrue(report['summary']['review_required'])
        record,path,detail=self.detail(report);record['world_entity_issue_count']=0;record['state']='passed'
        with self.assertRaisesRegex(FormatError,'hides'):native_release.validate(report,self.content,self.release,6)

    def test_empty_known_world_is_explicitly_unevaluated_not_a_successful_object_check(self):
        self.source.write_bytes(self.many(0));data=self.source.read_bytes()
        # The fixture still holds one cell entity; remove its holder as well.
        d=maps.decode(data);world=d['map'][0x14];_,wf=world.typed();base=wf[0x401107c];grids=base.mapping()[3];grid=grids.array()[0];_,gf=grid.typed();cells=gf[0x401107d]
        cell=cells.mapping()[103];cs=cell.mapping()[0x471519];cell=editing.rewrite_map(cell,{0x471519:editing.rewrite_map(cs,{4:b'\0'})})
        grid=editing.rewrite_typed(grid,{0x401107d:editing.rewrite_map(cells,{103:cell})});world=editing.rewrite_typed(world,{0x401107c:editing.rewrite_map(base,{3:editing.replace_array_items(grids,[grid])})})
        self.source.write_bytes(d['envelope']+d['header_bytes']+editing.rewrite_map(d['body'],{0x14:world}))
        report=self.collect();self.assertEqual(self.world_calls,[[]]);self.assertEqual(report['summary']['errors'],0)
        self.assertEqual(report['summary']['world_objects_unevaluated_maps'],1);self.assertEqual(report['summary']['world_entity_issue_count'],1)
        self.assertTrue(report['summary']['review_required']);record,_,_=self.detail(report);self.assertIsNone(record['data_equal'])

    def test_protocol5_remains_historical_and_cannot_satisfy_manifest6(self):
        report=self.collect();old=copy.deepcopy(report);old.update(format=native_release.MAP_FORMAT,scope=native_release.MAP_SCOPE,
            checks=[r for r in old['checks'] if r['operation']!='tam_world_entities'])
        old['summary']=native_release.summary(old['checks'],5);native_release.validate(old,self.content,self.release,5)
        with self.assertRaisesRegex(FormatError,'protocol'):native_release.validate(old,self.content,self.release,6)

    def test_frozen_new_protocol_survives_cache_removal_and_rejects_downgrade(self):
        report=self.collect();path=self.manifest(report);manifest,content=publication.load_release(path);frozen,stage=publication.stage_release(manifest,content,self.release)
        self.cache.rename(self.root/'no-cache');publication.check_staged(frozen)
        bad=copy.deepcopy(frozen);bad['native_validation_protocol']=5
        with self.assertRaisesRegex(FormatError,'protocol'):publication.check_staged(bad)


if __name__=='__main__':unittest.main()
