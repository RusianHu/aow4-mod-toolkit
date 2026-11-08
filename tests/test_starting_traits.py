import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from aow4mod import catalog, content_analysis as analysis, content_snapshots, starting_traits
from aow4mod.core import FormatError, references, sha
from test_aow4_mod_audit import typed, mapping, string, u32, rpk
from test_content_analysis import obj, res, links
from test_content_research import text


def research_settings(selected=(4,), count=None, requisite=(99,), cid=0x477C08):
    fields={1:links(*selected),3:mapping({}),4:links(*requisite)}
    if count is not None:fields[2]=struct.pack('<i',count)
    entry=typed(cid,{1:u32(1),2:string('Settings'),cid:mapping(fields)})
    return mapping({10:u32(1),1:mapping({0:entry})})


class StartingTraitTests(unittest.TestCase):
    def test_fixed_research_excludes_random_requisites_and_keeps_omission(self):
        r=res(obj(1,0x477C0D,{0x477C0D:mapping({1:research_settings((4,4,5))})}))
        info=starting_traits.analyze(r)
        self.assertEqual([x['id'] for x in info['references']],['0000000000000004']*2+['0000000000000005'])
        self.assertIsNone(info['selectors'][0]['count'])
        self.assertEqual(info['selectors'][0]['count_source'],'omitted_unread')
        refs=references(r)
        self.assertEqual(sum(analysis.role(r,x)=='starting_research' for x in refs),3)
        self.assertIsNone(analysis.role(r,next(x for x in refs if int(x['id'],16)==99)))

    def test_explicit_random_count_is_not_evaluated_as_fixed_grants(self):
        r=res(obj(1,0x477C0D,{0x477C0D:mapping({1:research_settings((),3)})}))
        info=starting_traits.analyze(r)
        self.assertEqual(info['references'],[])
        self.assertEqual(info['selectors'][0]['count'],3)
        self.assertEqual(info['selectors'][0]['status'],'selector_not_evaluated')

    def test_unknown_settings_and_bad_counts_do_not_become_grants(self):
        variants=[research_settings(cid=0x999999),mapping({10:u32(2),1:mapping({})}),
                  mapping({10:u32(1),1:mapping({1:typed(0x477C08,{})})})]
        for value in variants:
            with self.subTest(value=value):
                r=res(obj(1,0x477C0D,{0x477C0D:mapping({1:value})}))
                info=starting_traits.analyze(r)
                self.assertTrue(info['issues']);self.assertEqual(info['references'],[])

    def test_empty_description_is_not_certified_generated_tooltip(self):
        for raw in [text(''),mapping({2:mapping({0x401604:mapping({})})})]:
            r=res(obj(1,0x477C10,{0x477C10:mapping({1:links(2,3)}),0x477C0A:mapping({1:raw})}))
            desc=starting_traits.analyze(r)['description']
            self.assertEqual(desc['status'],'empty_key_runtime_text_unverified')
            self.assertFalse(desc['generated_description_evaluated']);self.assertFalse(desc['rendered_tooltip'])

    def test_bad_link_list_and_null_link_remain_issues(self):
        for raw in [mapping({}),links(0),mapping({0x1E:mapping({1:mapping({1:b'bad'})})})]:
            r=res(obj(1,0x477C10,{0x477C10:mapping({1:raw})}))
            info=starting_traits.analyze(r)
            self.assertTrue(info['issues']);self.assertEqual(info['references'],[])

    def test_omitted_and_serialized_empty_lists_are_different(self):
        a=res(obj(1,0x477C10,{0x477C10:mapping({})}))
        b=res(obj(1,0x477C10,{0x477C10:mapping({1:links()})}))
        self.assertEqual(starting_traits.analyze(a)['lists'][0]['status'],'omitted_unread')
        self.assertEqual(starting_traits.analyze(b)['lists'][0]['count'],0)

    def test_tag_selected_operations_never_infer_spell_from_filter_reference(self):
        r=res(obj(1,0x477C0F,{0x477C0F:mapping({1:links(2)})}))
        info=starting_traits.analyze(r)
        self.assertEqual(info['selectors'][0]['status'],'selector_not_evaluated')
        self.assertEqual(info['references'],[])
        self.assertIsNone(analysis.role(r,references(r)[0]))

    def fixture(self,root,wrong_type=False):
        game=root/'game';(game/'Content').mkdir(parents=True)
        rows=[obj(1,0x473533,{0x473533:mapping({5:links(6),0x27:links(2,3)})}),
              obj(2,0x477C0D,{0x477C0D:mapping({1:research_settings()}),0x477C0A:mapping({1:text('START@DESC',[('{GLOBAL.TEST.Hyperlink}',None)])})}),
              obj(3,0x477C10,{0x477C10:mapping({1:links(5)})}),
              obj(4,0x473702,{0x473702:mapping({13:links(7)})}),
              obj(5,0x473702 if wrong_type else 0x47512B,{0x47512B:mapping({3:links(6)})}),
              obj(6,0x4714A3,{}),obj(7,0x471DDA,{0x471DDA:mapping({4:links(8)})}),
              obj(8,0x4714A3,{}),obj(9,0x47024A,{0x471CE4:mapping({3:links(8)})})]
        pack=game/'Content/test.rpk';pack.write_bytes(rpk(rows))
        index=root/'index.sqlite';catalog.build(index,game,with_links=True)
        return game,pack,index

    def test_trait_to_research_unlock_and_empire_property_remain_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,_,idx=self.fixture(root)
            with patch.object(analysis.semantics,'schema_status',return_value={'verified':True,'observed_tool_sha256':'fixture'}):
                report=analysis.explain(idx,'1')
            roles=[e['role'] for e in report['edges']]
            for role in ['starting_property','starting_research','starting_empire_skill','trait_side_property','empire_skill_side_property','unlocks_spell']:
                self.assertIn(role,roles)
            self.assertNotIn('0000000000000063',[n['id'] for n in report['nodes']])
            self.assertFalse(report['coverage']['gameplay_tested'])
            self.assertEqual(report['starting_bonus_summary']['declaration_counts']['starting_research'],1)
            self.assertEqual(report['starting_bonus_summary']['declaration_counts']['starting_empire_skill'],1)

    def test_wrong_target_type_and_unvisited_candidates_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,_,idx=self.fixture(root,True)
            with patch.object(analysis.semantics,'schema_status',return_value={'verified':True,'observed_tool_sha256':'fixture'}):
                report=analysis.explain(idx,'1')
                limited=analysis.explain(idx,'1',depth=1)
            edge=next(e for e in report['edges'] if e['role']=='starting_empire_skill')
            self.assertEqual(len(edge['target_type_check']['mismatches']),1)
            edge=next(e for e in limited['edges'] if e['role']=='starting_empire_skill')
            self.assertEqual(edge['target_type_check']['unchecked_candidates'],1)
            self.assertTrue(limited['traversal_truncated'])

    def test_duplicate_definitions_and_missing_targets_do_not_choose_a_winner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);game,_,idx=self.fixture(root)
            (game/'Content/other.rpk').write_bytes(rpk([obj(5,0x473702,{})]))
            catalog.build(idx,game,with_links=True)
            with patch.object(analysis.semantics,'schema_status',return_value={'verified':True,'observed_tool_sha256':'fixture'}):
                report=analysis.explain(idx,'1')
            edge=next(e for e in report['edges'] if e['role']=='starting_empire_skill')
            self.assertEqual(edge['resolution'],'ambiguous')
            self.assertEqual(edge['candidate_count'],2)
            self.assertEqual(len(edge['target_type_check']['mismatches']),1)
            (game/'Content/missing.rpk').write_bytes(rpk([obj(20,0x477C10,{0x477C10:mapping({1:links(100)})})]))
            catalog.build(idx,game,with_links=True)
            with patch.object(analysis.semantics,'schema_status',return_value={'verified':True,'observed_tool_sha256':'fixture'}):
                missing=analysis.explain(idx,'14')
            self.assertEqual(missing['edges'][0]['resolution'],'missing')

    def test_offline_snapshot_detects_removed_starting_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);game,pack,idx=self.fixture(root)
            snapshots=[]
            for i,ids in enumerate([(4,5),(4,)]):
                pack.write_bytes(rpk([obj(1,0x477C10,{0x477C10:mapping({1:links(*ids)})}),obj(4,0x47512B,{}),obj(5,0x47512B,{})]))
                catalog.build(idx,game,with_links=True)
                with patch.object(analysis.semantics,'schema_status',return_value={'verified':True,'observed_tool_sha256':'fixture'}):
                    report=analysis.explain(idx,'1')
                path=root/(str(i)+'.json');path.write_text(json.dumps(report),encoding='utf-8')
                snapshots.append(content_snapshots.capture(path))
            pack.unlink();idx.unlink()
            result=content_snapshots.compare(*snapshots)
            self.assertTrue(result['execution_edges_changed'])
            self.assertIn('starting_bonus',result['changed'][0]['changed_sections'])

    def test_snapshot_rejects_forged_grants_type_checks_and_tooltip_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,pack,idx=self.fixture(root)
            with patch.object(analysis.semantics,'schema_status',return_value={'verified':True,'observed_tool_sha256':'fixture'}):
                report=analysis.explain(idx,'1')
            path=root/'report.json';path.write_text(json.dumps(report),encoding='utf-8')
            snapshot=content_snapshots.capture(path)
            for mode in ['grant','type','binding','coverage','summary']:
                bad=copy.deepcopy(snapshot);r=copy.deepcopy(report)
                research=next(n for n in r['nodes'] if n['id']=='0000000000000002')
                if mode=='grant':research['starting_bonus']['references'][0]['id']='0000000000000005'
                if mode=='type':next(e for e in r['edges'] if e['role']=='starting_empire_skill')['target_type_check']['expected_type']='0x00473702'
                if mode=='binding':research['text_candidates'][0]['parameters']=[]
                if mode=='coverage':research['text_candidate_coverage']['total']=0
                if mode=='summary':r['starting_bonus_summary']['declaration_counts']['starting_research']=50
                raw=json.dumps(r).encode();bad['explanation_hex']=raw.hex();bad['explanation_sha256']=sha(raw)
                keys={n['node']:content_snapshots.definition_key(n,r['game_build']['path']) for n in r['nodes']}
                bad['projection']=content_snapshots.projection(r,keys)
                with self.subTest(mode=mode),self.assertRaises(FormatError):content_snapshots.validate(bad)
            pack.unlink();idx.unlink()
            result=content_snapshots.compare(snapshot,snapshot)
            self.assertEqual(result['changed'],[]);self.assertFalse(result['execution_edges_changed'])

    def test_text_candidates_have_an_explicit_limit_and_include_later_descriptions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);game=root/'game';(game/'Content').mkdir(parents=True)
            rows=[obj(1,0x473533,{0x473533:mapping({i:text('TEXT@'+str(i)) for i in range(20,38)})})]
            (game/'Content/test.rpk').write_bytes(rpk(rows));idx=root/'index.sqlite';catalog.build(idx,game,with_links=True)
            with patch.object(analysis.semantics,'schema_status',return_value={'verified':True,'observed_tool_sha256':'fixture'}):
                report=analysis.explain(idx,'1')
            node=report['nodes'][0]
            self.assertEqual(len(node['text_candidates']),16)
            self.assertEqual(node['text_candidate_coverage'],{'total':18,'limit':16,'truncated':True})


if __name__=='__main__':unittest.main()
