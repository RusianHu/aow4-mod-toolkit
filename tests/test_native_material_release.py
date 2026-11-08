import copy
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import native_material_release,material_links,native_release,native
from aow4mod.core import FormatError,sha
from test_material_links import clb,report_fixture
from aow4mod.filesystem import file_hash


class NativeMaterialReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name).resolve()
        self.content=self.root/'content';(self.content/'Libraries').mkdir(parents=True);self.source=self.content/'Libraries/TEST.clb';self.source.write_bytes(clb())
        self.unit=self.root/'native-evidence/0000';self.unit.mkdir(parents=True);self.game=self.root/'game';self.game.mkdir();self.cache=self.root/'cache'

    def freeze(self,external=False,warning=False,resolved=True):
        provided=[]
        if external:
            self.source.write_bytes(clb(effect_namespace='FX',dependencies=['FX.clb']));fx=self.root/'FX.clb';fx.write_bytes(clb('FX','FX',material=False));provided=[fx]
        report=report_fixture(self.root/'run',self.source,provided,resolved=resolved,warning=warning)
        with patch.object(material_links,'check',return_value=copy.deepcopy(report)):
            detail,path,primary=native_material_release.collect(self.source,self.content,self.game,self.unit,self.root,self.cache,30,provided)
        return detail,path,primary

    def test_frozen_complete_graph_and_objects_verify_without_native_backend(self):
        detail,_,_=self.freeze();self.assertEqual(detail['comparison']['material_link_issue_count'],0)
        self.assertEqual(len(list((self.unit/'material-links/objects').glob('*.bin'))),2)
        with patch.object(native,'profile_for',side_effect=AssertionError('DLL access')),patch.object(native,'run_worker',side_effect=AssertionError('native launch')):
            self.assertEqual(native_material_release.assess(detail,self.source,self.content,self.root),detail['comparison'])

    def test_external_context_bytes_and_namespace_cannot_be_omitted_or_replaced(self):
        detail,_,_=self.freeze(external=True);batch=detail['batches'][0]
        self.assertEqual(batch['context'][1]['storage'],'evidence')
        for change in ('omit','namespace','target_type'):
            bad=copy.deepcopy(detail)
            if change=='omit':bad['batches'][0]['context'].pop()
            elif change=='namespace':bad['batches'][0]['context'][1]['namespace']='UNRELATED'
            else:bad['batches'][0]['objects'][-1]['identity']['type_id']='0x00410035'
            with self.subTest(change=change),self.assertRaises(FormatError):native_material_release.assess(bad,self.source,self.content,self.root)

    def test_unresolved_effect_remains_an_issue_with_no_object_certification(self):
        detail,_,_=self.freeze(resolved=False)
        self.assertEqual(detail['comparison']['material_link_issue_count'],1);self.assertFalse(detail['comparison']['effect_objects_evaluated'])
        bad=copy.deepcopy(detail);bad['batches'][0]['materials'][0]['issues']=[]
        with self.assertRaises(FormatError):native_material_release.assess(bad,self.source,self.content,self.root)

    def test_cleared_diagnostics_or_missing_raw_log_are_rejected(self):
        detail,_,_=self.freeze(warning=True);self.assertTrue(detail['diagnostics'])
        for kind in ('cleared','missing'):
            bad=copy.deepcopy(detail)
            if kind=='cleared':bad['diagnostics']=[];bad['batches'][0]['diagnostics']=[]
            else:bad['batches'][0]['logs'].pop()
            with self.subTest(kind=kind),self.assertRaises(FormatError):native_material_release.assess(bad,self.source,self.content,self.root)

    def test_refreshed_native_hash_cannot_hide_independent_effect_data_difference(self):
        detail,_,_=self.freeze();bad=copy.deepcopy(detail);item=bad['batches'][0]['materials'][0]['target_artifact'];old=self.root/item['file'];raw=bytearray(old.read_bytes());raw[-1]^=1;new=old.parent/(sha(raw)+'.bin');new.write_bytes(raw);item.update(file=new.relative_to(self.root).as_posix(),sha256=sha(raw))
        with self.assertRaises(FormatError):native_material_release.assess(bad,self.source,self.content,self.root)

    def test_path_roles_and_incomplete_batches_are_rejected(self):
        detail,_,_=self.freeze()
        for change in ('path','batch','source'):
            bad=copy.deepcopy(detail)
            if change=='path':bad['batches'][0]['materials'][0]['artifacts']['before']['file']='../escape'
            elif change=='batch':bad['batches']=[]
            else:bad['source']['sha256']='0'*64
            with self.subTest(change=change),self.assertRaises(FormatError):native_material_release.assess(bad,self.source,self.content,self.root)

    def test_protocol_seven_adds_material_targets_and_independent_issue_counter(self):
        _,old=native_release.targets(self.content,include_models=True);_,new=native_release.targets(self.content,include_models=True,include_materials=True)
        self.assertEqual(sum(r['operation']=='clb_material_links' for r in old),0);self.assertEqual(sum(r['operation']=='clb_material_links' for r in new),1)
        row={'operation':'clb_material_links','state':'review_required','data_equal':True,'material_link_issue_count':1,'material_count':1,'effect_objects_evaluated':False}
        current=native_release.summary([row],7);self.assertTrue(current['review_required']);self.assertEqual(current['mismatches'],0);self.assertEqual(current['material_link_issue_count'],1)
        historical=native_release.summary([],6);self.assertNotIn('material_link_issue_count',historical)

    def test_current_collection_cannot_omit_material_evidence_or_downgrade_its_scope(self):
        out=self.root/'current';out.mkdir();report=report_fixture(self.root/'run',self.source)
        private={'game':str(self.game),'dll_sha256':next(iter(native.PROFILES)),'backend_sha256':'fixture','profile':next(iter(native.PROFILES.values()))}
        binding=native.public_binding(private);report['binding']=binding
        def export(source,game,output,*args,**kwargs):
            from aow4mod import content_libraries
            output=Path(output);output.mkdir();target=output/Path(source).name;shutil.copyfile(source,target)
            return {'format':'aow4mod-native-assets/1','source_sha256':file_hash(source),'output':str(output),'binding':binding,
                    'files':[{'name':target.name,'sha256':file_hash(target)}],'comparison':content_libraries.compare(source,target),'diagnostics':[]}
        with patch.object(native,'profile_for',return_value=private),patch('aow4mod.native_release.native_assets.export_library',side_effect=export),patch.object(material_links,'check',return_value=report):
            result=native_release.collect(self.content,self.game,out,self.cache,_protocol=7)
        self.assertEqual(result['format'],native_release.MATERIAL_FORMAT);self.assertEqual(result['summary']['material_count'],1)
        self.assertEqual(native_release.validate(result,self.content,out,7),result['summary'])
        bad=copy.deepcopy(result);bad['checks']=[r for r in bad['checks'] if r['operation']!='clb_material_links'];bad['summary']=native_release.summary(bad['checks'],7)
        with self.assertRaisesRegex(FormatError,'coverage'):native_release.validate(bad,self.content,out,7)
        bad.update(format=native_release.WORLD_FORMAT,scope=native_release.WORLD_SCOPE,summary=native_release.summary(bad['checks'],6))
        with self.assertRaisesRegex(FormatError,'protocol'):native_release.validate(bad,self.content,out,7)


if __name__=='__main__':unittest.main()
