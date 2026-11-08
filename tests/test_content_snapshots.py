import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import catalog,content_analysis,content_snapshots,native_objects
from aow4mod.core import FormatError,sha
from test_aow4_mod_audit import rpk,mapping,u32
from test_content_analysis import obj,links,res


class ContentSnapshotTests(unittest.TestCase):
    def test_native_values_bind_to_worker_result_and_selected_body(self):
        r=res(obj(1,0x471df6,{0x471df6:mapping({3:u32(2)})}));body=r.node.raw()[4:]
        binding={'game':'fixture','dll_sha256':'dll','backend_sha256':'backend','profile':{'name':'fixture'}}
        prop={'name':'Duration','category':'','codec':'i32','default':2,'default_status':'read','getter_status':0}
        params={'cid':'00471DF6','name':'Effect','body_hex':body.hex(),'body_sha256':sha(body),'assignments':[],'include_properties':True}
        request={'operation':'object-roundtrip','request_id':'one','binding':binding,'params':params}
        raw=json.dumps(request).encode();digest=sha(raw)
        payload={'cid':'00471DF6','name':'Effect','source_file':'in.bin','source_sha256':sha(body),'output_file':'out.bin','output_sha256':sha(body),'readbacks':[],'properties':[prop]}
        result={'request_id':'one','request_sha256':digest,'payload':payload}
        material={'request.json':raw.hex(),'result.json':json.dumps(result).encode().hex(),
                  'process.json':json.dumps({'request_sha256':digest,'exit_code':0,'timed_out':False}).encode().hex(),
                  'output_body':body.hex(),'nested_bodies':[]}
        report={'roundtrip_artifact':{'sha256':sha(body)},'comparison':native_objects.compare_bodies(body,body),
                'binding':{'game':'fixture','dll_sha256':'dll','backend_sha256':'backend','profile':'fixture'},
                'properties':[{'name':'Duration','category':'','codec':'i32','value':2,'value_status':'read','getter_status':0}],'object_path':[]}
        content_snapshots.verify_native(report,material,r)
        bad=copy.deepcopy(report);bad['properties'][0]['value']=3
        with self.assertRaisesRegex(FormatError,'worker evidence'):content_snapshots.verify_native(bad,material,r)
        other=res(obj(1,0x471df6,{0x471df6:mapping({3:u32(3)})}))
        with self.assertRaisesRegex(FormatError,'input differs'):content_snapshots.verify_native(report,material,other)

    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.game=self.root/'game';(self.game/'Content').mkdir(parents=True)
        self.pack=self.game/'Content/test.rpk';self.index=self.root/'index.sqlite'
        self.mock=patch.object(content_analysis.semantics,'schema_status',return_value={'verified':True,'observed_tool_sha256':'fixture'})
        self.mock.start();self.addCleanup(self.mock.stop)

    def capture(self,name,duration=2,opaque=b'opaque'):
        self.pack.write_bytes(rpk([obj(1,0x471df6,{0x471df6:mapping({3:u32(duration)}),0x40:opaque})]))
        catalog.build(self.index,self.game,with_links=True)
        report=content_analysis.explain(self.index,'1')
        p=self.root/(name+'.json');p.write_text(json.dumps(report),encoding='utf-8')
        return content_snapshots.capture(p)

    def rewrite_report(self,snapshot,change):
        result=copy.deepcopy(snapshot);report=json.loads(bytes.fromhex(result['explanation_hex']));change(report)
        raw=json.dumps(report).encode();result['explanation_hex']=raw.hex();result['explanation_sha256']=sha(raw)
        keys={r['node']:content_snapshots.definition_key(r,report['game_build']['path']) for r in report['nodes']}
        result['projection']=content_snapshots.projection(report,keys)
        return result

    def test_offline_comparison_reports_duration_change_without_live_sources(self):
        a=self.capture('before',2);b=self.capture('after',3)
        self.pack.unlink();self.index.unlink()
        result=content_snapshots.compare(a,b)
        self.assertEqual(len(result['changed']),1)
        self.assertIn('facts',result['changed'][0]['changed_sections'])
        self.assertTrue(result['partial_scope'])

    def test_uninterpreted_byte_change_is_not_hidden_by_equal_known_facts(self):
        a=self.capture('before',2,b'first');b=self.capture('after',2,b'second')
        result=content_snapshots.compare(a,b)
        self.assertEqual(result['changed'],[])
        self.assertEqual(len(result['changes_outside_interpreted_projection']),1)

    def test_forged_facts_and_projection_are_rejected_even_after_rehash(self):
        a=self.capture('a')
        b=self.rewrite_report(a,lambda r:r['nodes'][0]['facts'][1].update(value=99))
        with self.assertRaisesRegex(FormatError,'facts disagree'):content_snapshots.validate(b)
        b=copy.deepcopy(a);next(iter(b['projection']['nodes'].values()))['name']='changed'
        with self.assertRaisesRegex(FormatError,'projection differs'):content_snapshots.validate(b)

    def test_stale_capture_and_changed_rules_or_evidence_mode_refuse(self):
        a=self.capture('a')
        self.pack.write_bytes(b'changed')
        with self.assertRaisesRegex(FormatError,'source changed'):content_snapshots.capture(self.root/'a.json')
        b=self.rewrite_report(a,lambda r:r.update(rules_fingerprint='other'))
        with self.assertRaisesRegex(FormatError,'rules changed'):content_snapshots.validate(b)
        b=self.rewrite_report(a,lambda r:r['coverage'].update(native_requested=True))
        with self.assertRaisesRegex(FormatError,'evidence mode'):content_snapshots.compare(a,b)

    def test_fabricated_execution_edge_is_rejected(self):
        self.pack.write_bytes(rpk([obj(1,0x471573,{0x4714cd:mapping({3:links(2)})}),obj(2,0x471df6,{0x471df6:mapping({3:u32(2)})})]))
        catalog.build(self.index,self.game,with_links=True)
        report=content_analysis.explain(self.index,'1');p=self.root/'graph.json';p.write_text(json.dumps(report))
        a=content_snapshots.capture(p)
        b=self.rewrite_report(a,lambda r:r['edges'][0].update(target_id='0000000000000003'))
        with self.assertRaisesRegex(FormatError,'edge disagrees'):content_snapshots.validate(b)


if __name__=='__main__':unittest.main()
