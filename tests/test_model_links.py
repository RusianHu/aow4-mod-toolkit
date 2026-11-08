import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import model_links,native_objects,native
from aow4mod.core import FormatError
from aow4mod.filesystem import file_hash
from test_aow4_mod_audit import mapping,array,typed,string


def model_body(mesh='[TEST]MESH\\0'):
    component=typed(0x410067,{0x1f:mapping({0x14:string(mesh)}),0x21:mapping({0x14:string('[TEST]MAT\\0')}),0x23:mapping({})})
    return mapping({0x14:string('[TEST]MODEL\\0'),0x1f:array([typed(0x412e00,{0xa:array([component])})])})


class ModelLinksTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        def artifact(name,raw):
            path=self.root/name;path.write_bytes(raw);return {'file':str(path),'sha256':file_hash(path)}
        self.artifact=artifact;body=model_body()
        row={'locator':'[TEST]MODEL\\0','status':3,'components':[],'issues':[],
             'artifacts':{'before':artifact('before.bin',body),'after':artifact('after.bin',body)},
             'comparison':native_objects.compare_bodies(body,body)}
        component={'lod':0,'component':0,'status':3}
        for label,locator,cid in (('mesh','[TEST]MESH\\0','00410035'),('material','[TEST]MAT\\0','0041166F')):
            component[label]={'declared_locator':locator,'resolved':True,'resolved_cid':cid,'resolved_local_locator':locator,
                              'same_local_member':True,'connected_and_activated':True,
                              'target_artifact':artifact(label+'.bin',mapping({0x14:string(locator)}))}
        component['shadow']={'declared_locator':'','resolved':False,'resolved_cid':None,'resolved_local_locator':None,
                             'same_local_member':False,'connected_and_activated':False}
        row['components']=[component];self.payload={'models':[row]}

    def test_local_resolved_targets_and_serialized_definitions_agree(self):
        self.assertTrue(model_links.assess(self.payload))

    def test_activated_and_ready_flags_do_not_hide_wrong_target_type(self):
        row=self.payload['models'][0];row['components'][0]['material']['resolved_cid']='00410035'
        with self.assertRaisesRegex(FormatError,'findings'):model_links.assess(self.payload)
        row['issues']=model_links.issues_for(row)
        self.assertTrue(model_links.assess(self.payload))
        self.assertEqual(row['issues'][0]['issue'],'model_target_wrong_type')

    def test_missing_target_stays_review_required_without_diagnostic_text(self):
        row=self.payload['models'][0];component=row['components'][0];component['status']=1
        component['mesh'].update(resolved=False,resolved_cid=None,resolved_local_locator=None,same_local_member=False,connected_and_activated=False)
        del component['mesh']['target_artifact'];row['issues']=model_links.issues_for(row)
        self.assertTrue(model_links.assess(self.payload))
        self.assertEqual([i['issue'] for i in row['issues']],['component_not_ready','model_target_not_resolved'])

    def test_refreshed_hash_does_not_hide_changed_declared_or_resolved_target(self):
        original=copy.deepcopy(self.payload)
        row=self.payload['models'][0];changed=model_body('[TEST]MESH\\9')
        row['artifacts']['before']=self.artifact('changed.bin',changed)
        row['comparison']=native_objects.compare_bodies(changed,model_body())
        with self.assertRaisesRegex(FormatError,'declared link'):model_links.assess(self.payload)
        self.payload=original
        row=self.payload['models'][0];row['components'][0]['mesh']['target_artifact']=self.artifact('wrong.bin',mapping({0x14:string('[TEST]MESH\\9')}))
        with self.assertRaisesRegex(FormatError,'resolved target identity'):model_links.assess(self.payload)

    def test_missing_target_witness_or_changed_artifact_is_refused(self):
        row=self.payload['models'][0];link=row['components'][0]['mesh'];old=link.pop('target_artifact')
        with self.assertRaisesRegex(FormatError,'witness is missing'):model_links.assess(self.payload)
        link['target_artifact']=old;Path(old['file']).write_bytes(b'changed')
        with self.assertRaisesRegex(FormatError,'witness changed'):model_links.assess(self.payload)

    def test_selection_requires_unique_existing_model_members(self):
        rows=[{'identity':{'locator':'[TEST]MODEL\\0','type_id':'0x0041004B','ordinal':0}},
              {'identity':{'locator':'[TEST]MESH\\0','type_id':'0x00410035','ordinal':1}}]
        with patch.object(model_links.content_libraries,'read') as read:
            read.return_value.members=rows
            _,_,selected=model_links.select('unused',['[test]model\\0']);self.assertEqual(selected[0]['ordinal'],0)
            for names in ([],['[TEST]MODEL\\0']*17,['[TEST]MODEL\\0','[test]model\\0'],['[TEST]MESH\\0'],['[TEST]MODEL\\9']):
                with self.subTest(names=names),self.assertRaises(FormatError):model_links.select('unused',names)

    def test_native_result_is_bound_to_selected_models_and_input(self):
        params={'sha256':'input','language_sha256':'language','library_name':'TEST','models':[{'locator':'[TEST]MODEL\\0'}]}
        payload={**params,'callback_result':0,'content_present':True,'models':[{'locator':'[TEST]MODEL\\0'}]}
        native.validate_payload('model-links',payload,params)
        for key,value in (('models',[]),('sha256','other'),('library_name','OTHER')):
            with self.subTest(key=key),self.assertRaises(FormatError):native.validate_payload('model-links',{**payload,key:value},params)


if __name__=='__main__':unittest.main()
