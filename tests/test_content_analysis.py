import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import content_analysis as analysis, catalog, editing
from aow4mod.core import Node,Resource,FormatError
from test_aow4_mod_audit import typed,mapping,array,rpk,string,u32


def obj(rid,cid,fields,extra=None):
    return typed(cid,{0x18:struct.pack('<Q',rid),0x15:string(f'Object {rid}'),**fields,**(extra or {})})


def res(raw):return Resource.parse(Node(raw,0,len(raw)))
def link(rid):return typed(0x40004a,{0x14:struct.pack('<Q',rid),0x16:string(f'Target {rid}')})
def links(*ids):return mapping({0x1e:array([link(rid) for rid in ids])})


class ContentAnalysisTests(unittest.TestCase):
    def test_dictionary_tokens_match_braced_parameter_bindings(self):
        text={'translations':{'items':[{'text':'+{damage}% {GLOBAL.TurnConcept}'}]},
              'parameters':[{'placeholder':'{damage}','replacement':'20'},{'placeholder':'{unused}','replacement':'old'}]}
        nodes={1:{'text_candidates':[text]}}
        analysis.check_parameters(None,nodes,{})
        result=text['parameter_checks']
        self.assertEqual(result['expressions'][0]['status'],'explicit_replacement')
        self.assertEqual(result['expressions'][0]['replacements'],['20'])
        self.assertEqual(result['unused_explicit_parameters'],['{unused}'])
        self.assertEqual(result['tokens_without_explicit_binding'],['{GLOBAL.TurnConcept}'])

    def test_display_parameters_preserve_tag_ambiguity_and_do_not_choose_first(self):
        nodes={1:{'node':1,'facts':[{'kind':'disable_turns','value':2,'value_source':'loaded_native_getter','property':'p'}]}}
        self.assertEqual(analysis.parameter_value('{DUPLICATE>Duration}',[{'row_id':1},{'row_id':2}],nodes)['status'],'ambiguous_tag')
        value=analysis.parameter_value('{UNIQUE>Duration}',[{'row_id':1}],nodes)
        self.assertEqual(value['value'],2);self.assertIn('not_rendered',value['status'])
        self.assertEqual(analysis.parameter_value('{MISSING>Duration}',[],nodes)['status'],'missing_tag')
        self.assertEqual(analysis.parameter_value('{UNIQUE>Unsupported}',[{'row_id':1}],nodes)['status'],'unsupported_display_expression')

    def test_omitted_duration_is_unknown_until_loaded_getter(self):
        r=res(obj(1,0x47355a,{0x47355a:mapping({})}))
        f=analysis.scalar_facts(r)[0]
        self.assertIsNone(f['value']);self.assertEqual(f['value_source'],'omitted_unread')
        read={'properties':[{'name':'Num Turns','value_status':'read','value':1}]}
        f=analysis.scalar_facts(r,read)[0]
        self.assertEqual(f['value'],1);self.assertEqual(f['value_source'],'loaded_native_getter')

    def test_percent_conversions_and_disagreement_remain_distinct(self):
        a=res(obj(1,0x471c23,{0x471c23:mapping({1:struct.pack('<f',1.2)})}))
        self.assertAlmostEqual(analysis.scalar_facts(a)[0]['derived']['bonus_percent'],20,places=4)
        b=res(obj(2,0x47190d,{0x47190d:mapping({15:u32(12)})}))
        self.assertEqual(analysis.scalar_facts(b)[0]['derived']['base_chance_percent'],120)
        f=analysis.scalar_facts(a,{'properties':[{'name':'Multiplier','value_status':'read','value':2.0}]})[0]
        self.assertTrue(f['serialized_getter_disagreement']);self.assertNotIn('derived',f)

    def test_cost_resource_id_amount_order_and_omission(self):
        def amount(rid,n):
            fields={1:mapping({2:struct.pack('<Q',rid)})}
            if n is not None:fields[2]=struct.pack('<i',n)
            return mapping({0x473ea2:mapping(fields)})
        block=mapping({0x473ea3:mapping({1:mapping({10:u32(2),11:amount(5,180),12:amount(6,None)})})})
        r=res(obj(1,0x471ef8,{0x471ef8:mapping({6:block})}))
        rows,issues=analysis.cost_rows(r)
        self.assertEqual([x['resource_id'] for x in rows],['0000000000000005','0000000000000006'])
        self.assertEqual(rows[0]['amount'],180);self.assertIsNone(rows[1]['amount'])
        self.assertEqual(issues[0]['issue'],'cost_block_omitted_unread')

    def test_filter_any_all_not_do_not_collapse(self):
        r=res(obj(1,0x471cd0,{0x471cd0:mapping({0x25:mapping({3:links(2,3),4:links(4),5:links(5)})})}))
        f=analysis.filters(r)[0]['groups']
        self.assertEqual([x['id'] for x in f['required_any']['references']],['0000000000000002','0000000000000003'])
        self.assertEqual(f['forbidden_any']['references'][0]['id'],'0000000000000004')
        self.assertEqual(f['required_all']['references'][0]['id'],'0000000000000005')

    def test_property_filter_has_no_invented_third_group(self):
        r=res(obj(1,0x471df6,{0x471df6:mapping({7:mapping({0x4719a7:mapping({1:links(),2:links(9)})})})}))
        groups=analysis.filters(r)[0]['groups']
        self.assertEqual(set(groups),{'required_all','forbidden_any'})

    def test_trace_uses_entry_links_and_retains_five_occurrences(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);game=root/'game';(game/'Content').mkdir(parents=True)
            rows=[obj(1,0x473708,{0x473708:mapping({})}),
                  obj(2,0x473702,{0x473702:mapping({6:link(1),13:links(3)})}),
                  obj(3,0x471dda,{0x471dda:mapping({4:links(4)})}),
                  obj(4,0x4714a3,{0x4714a3:mapping({})}),
                  obj(5,0x47024a,{0x471ce4:mapping({3:links(4),13:mapping({1:mapping({3:links(6)})})})}),
                  obj(6,0x471573,{0x4714cd:mapping({3:links(7,7,7,7,7)})}),
                  obj(7,0x4715ba,{0x4715ba:mapping({0x19:link(8)})}),
                  obj(8,0x47355a,{0x47355a:mapping({3:u32(2)})}),
                  obj(9,0x473702,{0x473702:mapping({6:b'\0'})}),
                  obj(10,0x471df6,{0x471df6:mapping({3:u32(3)})})]
            pack=game/'Content/test.rpk';pack.write_bytes(rpk(rows));idx=root/'index.sqlite';catalog.build(idx,game,with_links=True)
            with patch.object(analysis.semantics,'schema_status',return_value={'verified':True,'observed_tool_sha256':'fixture'}):
                result=analysis.explain(idx,'1')
                limited=analysis.explain(idx,'1',depth=1)
            ids={x['id'] for x in result['nodes']}
            self.assertIn('0000000000000008',ids);self.assertNotIn('000000000000000A',ids)
            self.assertEqual(result['nodes'][0]['research_member_count'],1)
            self.assertEqual(result['nodes'][0]['same_file_unattached_research'][0]['id'],'0000000000000009')
            self.assertEqual(result['repeated_effects'][0]['occurrences'],5)
            self.assertTrue(limited['traversal_truncated'])
            impact=analysis.impact(idx,'7',depth=2)
            self.assertEqual(len(impact['direct_retarget_sites']),5)
            pack.write_bytes(rpk(rows+[obj(11,0x4714a3,{})]))
            with self.assertRaises(FormatError):analysis.explain(idx,'1')

    def test_research_grant_revisits_marker_seen_as_an_initial_property(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);game=root/'game';(game/'Content').mkdir(parents=True)
            rows=[obj(1,0x473708,{0x473708:mapping({9:links(4)})}),
                  obj(2,0x473702,{0x473702:mapping({6:link(1),13:links(3)})}),
                  obj(3,0x471dda,{0x471dda:mapping({4:links(4)})}),obj(4,0x4714a3,{0x4714a3:mapping({})}),
                  obj(5,0x47024a,{0x471ce4:mapping({3:links(4)})})]
            (game/'Content/test.rpk').write_bytes(rpk(rows));idx=root/'index.sqlite';catalog.build(idx,game,with_links=True)
            with patch.object(analysis.semantics,'schema_status',return_value={'verified':True,'observed_tool_sha256':'fixture'}):
                result=analysis.explain(idx,'1')
            self.assertIn('0000000000000005',{n['id'] for n in result['nodes']})
            self.assertEqual(sum(e['role']=='unlocks_spell' for e in result['edges']),1)

    def test_flat_upgrade_reference_is_execution_but_display_link_is_not(self):
        r=res(obj(1,0x476e3e,{2:link(2)}))
        self.assertEqual(analysis.role(r,{'property':'meta/0x02'}),'unit_upgrade')
        self.assertIsNone(analysis.role(r,{'property':'meta/0x15'}))
        self.assertIsNone(analysis.role(r,{'property':'0x00471CE4/0x6'}))


if __name__=='__main__':unittest.main()
