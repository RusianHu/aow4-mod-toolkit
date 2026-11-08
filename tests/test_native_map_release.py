import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import maps,native,native_maps,native_map_release,native_release,publication
from aow4mod.core import FormatError
from aow4mod.filesystem import file_hash,inventory,save_record
from test_maps import map_bytes
from test_aow4_mod_audit import rpk,resource
from test_content_libraries import library_bytes


class NativeMapReleaseTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.release=self.root/'release';self.release.mkdir();self.content=self.release/'content';self.content.mkdir()
        self.source=self.content/'map.tam';self.source.write_bytes(map_bytes())
        (self.content/'loose.bin').write_bytes(b'not a native format')
        self.game=self.root/'game';self.game.mkdir();self.cache=self.root/'cache';self.calls=0;self.normalize=False
        self.binding={'game':str(self.game),'dll_sha256':next(iter(native.PROFILES)),'backend_sha256':'fixture','profile':next(iter(native.PROFILES.values()))}
        self.public=native.public_binding(self.binding)
        for name,kwargs in [('aow4mod.native_release.native.profile_for',{'return_value':self.binding}),
                            ('aow4mod.native_map_release.native_maps.run',{'side_effect':self.roundtrip})]:
            mocked=patch(name,**kwargs);mocked.start();self.addCleanup(mocked.stop)

    def roundtrip(self,source,game,output,cache,timeout,libraries=(),packs=()):
        self.calls+=1;output=Path(output);output.mkdir(parents=True)
        data=map_bytes(b'normalized world') if self.normalize else Path(source).read_bytes()
        body=maps.decode(data)['body'].raw();files=[]
        for name in native_map_release.ROLES.values():
            path=output/name;path.write_bytes(data if name.endswith('.tam') else body)
            files.append({'file':name,'sha256':file_hash(path),'bytes':path.stat().st_size})
        return {'source':{'sha256':file_hash(source)},'output':str(output),'binding':self.public,'runs':['fixture-1','fixture-2'],
                'files':files,'diagnostics':[],'context_libraries':native.library_context(libraries),'context_packs':native_map_release.native_context.bind(packs)}

    def collect(self):return native_release.collect(self.content,self.game,self.release,self.cache,_protocol=5)

    def detail(self,report):
        record=next(r for r in report['checks'] if r['operation']=='tam_roundtrip');path=self.release/record['detail_file']
        return record,path,json.loads(path.read_text(encoding='utf-8'))

    def refresh(self,report,record,path,detail):
        save_record(path,detail);record['detail_sha256']=file_hash(path);report['evidence_files']=inventory(self.release/'native-evidence')

    def manifest(self,report):
        save_record(self.release/'native-validation.json',report)
        for name in ('description','change_notes'):(self.release/(name+'.txt')).write_text('fixture', encoding='utf-8')
        value={'format':'aow4mod-release/1','app_id':1669000,'content_directory':'content','files':{p:r['sha256'] for p,r in inventory(self.content).items()},
               'native_validation_requested':True,'native_validation_protocol':5,'native_validation_file':'native-validation.json',
               'native_validation_sha256':file_hash(self.release/'native-validation.json'),'native_validation_summary':report['summary'],
               'release_gate':{'review_required':report['summary']['review_required'],'issues':[{'issue':'native_validation_review_required'}] if report['summary']['review_required'] else []}}
        for name in ('description','change_notes'):value[name+'_file']=name+'.txt';value[name+'_sha256']=file_hash(self.release/(name+'.txt'))
        path=self.release/'manifest.json';save_record(path,value);return path

    def test_tam_is_automatic_and_six_roles_deduplicate_without_losing_coverage(self):
        report=self.collect();self.assertEqual(report['format'],native_release.MAP_FORMAT)
        self.assertEqual(report['summary']['operations']['tam_roundtrip'],1);self.assertEqual(report['summary']['passed'],1)
        self.assertEqual(report['not_native_checked'],['loose.bin']);record,path,detail=self.detail(report)
        self.assertEqual(set(detail['artifacts']),set(native_map_release.ROLES))
        self.assertEqual(len({v['file'] for v in detail['artifacts'].values()}),2)
        self.cache.rename(self.root/'removed-cache')
        with patch.object(native_maps,'run',side_effect=AssertionError('offline verifier cannot run native code')):
            self.assertEqual(native_release.validate(report,self.content,self.release,5),report['summary'])

    def test_first_save_changes_remain_findings_even_with_a_stable_independent_save(self):
        self.normalize=True;report=self.collect();_,_,detail=self.detail(report)
        self.assertFalse(detail['comparison']['source_save']['all_compared_data_equal'])
        self.assertTrue(detail['comparison']['independent_reload']['all_compared_data_equal'])
        self.assertEqual(report['summary']['mismatches'],1);self.assertTrue(report['summary']['review_required'])

    def test_refreshed_saved_map_or_object_hash_cannot_hide_unrequested_data(self):
        for role in ('first_saved','first_before'):
            with self.subTest(role=role):
                # Each subcase gets fresh frozen files while keeping the same source.
                self.release=self.root/role;self.release.mkdir();self.content=self.release/'content';self.content.mkdir()
                self.source=self.content/'map.tam';self.source.write_bytes(map_bytes())
                report=self.collect();record,path,detail=self.detail(report);item=detail['artifacts'][role];artifact=self.release/item['file']
                artifact.write_bytes(map_bytes(b'changed world') if role.endswith('saved') else b'changed native object')
                for value in detail['artifacts'].values():
                    if value['file']==item['file']:value['sha256']=file_hash(artifact)
                if record['artifact']==item['file']:record['artifact_sha256']=file_hash(artifact)
                self.refresh(report,record,path,detail)
                with self.assertRaisesRegex(FormatError,'comparison differs'):native_release.validate(report,self.content,self.release,5)
                detail['comparison']=native_map_release.assess(detail,self.source,self.content,self.release);self.refresh(report,record,path,detail)
                with self.assertRaisesRegex(FormatError,'hides'):native_release.validate(report,self.content,self.release,5)

    def test_missing_role_and_escaped_artifact_are_refused_after_rehashing(self):
        report=self.collect();record,path,detail=self.detail(report);original=copy.deepcopy(detail)
        detail['artifacts'].pop('reloaded_before');self.refresh(report,record,path,detail)
        with self.assertRaisesRegex(FormatError,'role coverage'):native_release.validate(report,self.content,self.release,5)
        detail=original;detail['artifacts']['reloaded_before']['file']='../outside.bin';self.refresh(report,record,path,detail)
        with self.assertRaisesRegex(FormatError,'release-relative'):native_release.validate(report,self.content,self.release,5)

    def test_bundled_lookup_inputs_are_required_in_frozen_map_context(self):
        (self.content/'own.rpk').write_bytes(rpk([resource()]))
        (self.content/'own.clb').write_bytes(library_bytes())
        folder=self.release/'native-evidence/one';folder.mkdir(parents=True)
        detail,_,_=native_map_release.collect(self.source,self.content,self.game,folder,self.release,self.cache,60)
        self.assertEqual(len(detail['context_packs']),1);self.assertEqual(len(detail['context_libraries']),1)
        for key in ('context_packs','context_libraries'):
            bad=copy.deepcopy(detail);bad[key]=[]
            with self.subTest(key=key),self.assertRaisesRegex(FormatError,'lookup context'):native_map_release.assess(bad,self.source,self.content,self.release)
        (self.content/'own.rpk').write_bytes(rpk([resource(rid=99)]))
        with self.assertRaisesRegex(FormatError,'lookup context'):native_map_release.assess(detail,self.source,self.content,self.release)

    def test_invalid_tam_is_a_native_error_and_cannot_return_to_unchecked_scope(self):
        self.source.write_bytes(b'invalid TAM');report=self.collect()
        self.assertEqual(report['summary']['errors'],1);self.assertNotIn('map.tam',report['not_native_checked'])
        self.assertIn('map.tam',report['inputs'])
        bad=copy.deepcopy(report);bad['checks']=[]
        with self.assertRaisesRegex(FormatError,'coverage'):native_release.validate(bad,self.content,self.release,5)

    def test_historical_protocol4_keeps_tam_unchecked_and_manifest5_blocks_downgrade(self):
        report=self.collect();old=copy.deepcopy(report)
        old.update(format=native_release.MODEL_FORMAT,scope=native_release.MODEL_SCOPE,inputs={},checks=[],
                   summary=native_release.summary([],4),not_native_checked=sorted(inventory(self.content)))
        self.assertEqual(native_release.validate(old,self.content,self.release,4),old['summary'])
        with self.assertRaisesRegex(FormatError,'protocol'):native_release.validate(old,self.content,self.release,5)
        path=self.manifest(report);value=json.loads(path.read_text(encoding='utf-8'));value['native_validation_protocol']=4;save_record(path,value)
        with self.assertRaisesRegex(FormatError,'protocol'):publication.load_release(path)

    def test_upload_snapshot_revalidates_without_the_original_cache_or_release_evidence(self):
        report=self.collect();path=self.manifest(report);manifest,content=publication.load_release(path)
        frozen,stage=publication.stage_release(manifest,content,self.release)
        self.cache.rename(self.root/'old-cache');(self.release/'native-evidence').rename(self.release/'old-native-evidence')
        publication.check_staged(frozen)
        bad=copy.deepcopy(frozen);bad['native_validation_protocol']=4
        with self.assertRaisesRegex(FormatError,'protocol'):publication.check_staged(bad)

    def test_frozen_source_manifest_binds_native_policy_even_if_all_report_hashes_are_refreshed(self):
        report=self.collect();path=self.manifest(report);manifest,content=publication.load_release(path)
        frozen,stage=publication.stage_release(manifest,content,self.release)
        old=copy.deepcopy(report);old.update(format=native_release.MODEL_FORMAT,scope=native_release.MODEL_SCOPE,inputs={},checks=[],
            summary=native_release.summary([],4),not_native_checked=sorted(inventory(stage)))
        validation=Path(frozen['_native_validation_path']);save_record(validation,old)
        bad=copy.deepcopy(frozen);bad.update(native_validation_protocol=4,native_validation_sha256=file_hash(validation),native_validation_summary=old['summary'])
        with self.assertRaisesRegex(FormatError,'policy differs.*protocol'):publication.check_staged(bad)
        for key in ('_native_validation_path','_source_manifest_snapshot'):
            bad=copy.deepcopy(frozen);bad.pop(key)
            with self.subTest(key=key),self.assertRaisesRegex(FormatError,'witness'):publication.check_staged(bad)

    def test_frozen_payload_or_gate_policy_cannot_be_replaced_with_refreshed_values(self):
        report=self.collect();path=self.manifest(report);manifest,content=publication.load_release(path)
        frozen,stage=publication.stage_release(manifest,content,self.release)
        bad=copy.deepcopy(frozen);bad['release_gate']['review_required']=True
        with self.assertRaisesRegex(FormatError,'policy differs.*release_gate'):publication.check_staged(bad)
        (stage/'loose.bin').write_bytes(b'changed');bad=copy.deepcopy(frozen);bad['files']['loose.bin']=file_hash(stage/'loose.bin')
        with self.assertRaisesRegex(FormatError,'policy differs.*files'):publication.check_staged(bad)


if __name__=='__main__':unittest.main()
