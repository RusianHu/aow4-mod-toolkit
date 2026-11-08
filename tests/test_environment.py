from pathlib import Path
import json
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import assets,catalog,dlc,editing,environment,overlay_imports
from aow4mod.core import Node,Resource,FormatError
from test_aow4_mod_audit import acp,rpk,mapping,typed,string


def link(rid):return typed(0x40004a,{0x14:struct.pack('<Q',rid),0x16:string('Target')})
def obj(rid,props=None,name='Fixture'):
    return typed(0x471c71,{0x18:struct.pack('<Q',rid),0x15:string(name),0x471c6d:mapping(props or {})})
def pack(objects,name='TEST.RPK',deps=()):
    raw=rpk(objects)
    values={0x16:string(name),0x1e:struct.pack('<I',len(deps))+b''.join(string(x) for x in deps)}
    body=editing.rewrite_map(Node(raw,0xb0,len(raw)),values)
    return raw[:12]+struct.pack('<I',len(body))+raw[16:0xb0]+body


class FakeSteam:
    def __init__(self,installed):self.installed=installed;self.requested=[]
    def dlc_status(self,ids):self.requested=list(ids);return {str(i):self.installed for i in ids}


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.game=self.root/'game';(self.game/'Content/Title').mkdir(parents=True)
        (self.game/'Content/Expansion').mkdir();(self.game/'Launcher/dlc').mkdir(parents=True)
        (self.game/'Launcher/dlc/expansion.dlc.json').write_text(json.dumps({'id':{'steam':'42','paradox':'EXPANSION'},'displayName':{'en':'Expansion'}}), encoding='utf-8')
        self.base=self.game/'Content/Title/base.rpk';self.base.write_bytes(pack([obj(1)]))
        self.extra=self.game/'Content/Expansion/extra.rpk';self.extra.write_bytes(pack([obj(2,{1:link(1)})],'EXTRA.RPK'))
        (self.game/'Content/Title/Icons.clb').write_bytes(b'opaque library')
        self.mod=self.root/'mod';self.mod.mkdir();self.acp=self.mod/'mod.acp'
        self.acp.write_bytes(acp(obj(1,{1:link(2),2:mapping({0x14:string('[Icons]TXA\\0'),0x17:string('Icon')})})))
        self.index=self.root/'index.sqlite';catalog.build(self.index,self.game)

    def analyze(self,**kwargs):return environment.analyze([self.mod],self.game,self.index,**kwargs)

    def test_dlc_unknown_disabled_and_owned_are_distinct(self):
        offline=dlc.inventory(self.game)
        self.assertEqual(offline['groups']['expansion']['state'],'installed_ownership_unknown')
        client=FakeSteam(True)
        owned=dlc.inventory(self.game,steam=client)
        self.assertEqual(owned['groups']['expansion']['state'],'steam_owned_and_installed')
        self.assertEqual(client.requested,[42])
        disabled=dlc.inventory(self.game,['42'],client)
        self.assertEqual(disabled['groups']['expansion']['state'],'disabled_by_configuration')
        self.assertEqual(dlc.inventory(self.game,steam=FakeSteam(False))['groups']['expansion']['state'],'steam_not_owned_or_installed')

    def test_recognized_graph_handles_cycles_and_reports_clb_member_limit(self):
        result=self.analyze(steam=FakeSteam(True))
        self.assertEqual(result['summary']['resources_visited'],2)
        self.assertFalse(result['summary']['review_required'])
        asset=result['asset_references'][0]
        self.assertEqual(asset['status'],'found');self.assertFalse(asset['member_validated'])
        self.assertEqual(asset['resolution'],'library_file_only')

    def test_disabled_dlc_is_not_available_just_because_files_exist(self):
        result=self.analyze(disabled_dlc=['EXPANSION'])
        self.assertTrue(result['summary']['review_required'])
        self.assertIn('resource_unavailable_in_configuration',{i['issue'] for i in result['issues']})

    def test_missing_custom_asset_vs_inherited_original_problem(self):
        data=obj(1,{2:string('root:/Title/missing.dds')});self.acp.write_bytes(acp(data))
        result=self.analyze();self.assertTrue(result['summary']['review_required'])
        self.base.write_bytes(pack([data]));catalog.build(self.index,self.game)
        result=self.analyze();self.assertTrue(result['summary']['review_required'])
        self.assertTrue(result['asset_references'][0]['inherited_from_original'])

    def test_unselected_mod_resource_is_not_resolved_as_loaded(self):
        other=self.root/'unselected';other.mkdir();(other/'other.rpk').write_bytes(pack([obj(3)],'OTHER.RPK'))
        catalog.build(self.index,self.game,[other])
        self.acp.write_bytes(acp(obj(1,{1:link(3)})))
        result=self.analyze();self.assertTrue(result['summary']['review_required'])
        self.assertEqual(result['resource_references'][0]['status'],'only_in_unselected_mods')

    def test_multiple_active_definitions_are_not_assigned_a_guessed_winner(self):
        other=self.root/'other';other.mkdir();(other/'other.acp').write_bytes(acp(obj(2,name='Other')).replace(b'[#0000000000000001]',b'[#0000000000000002]'))
        third=self.root/'third';third.mkdir();(third/'third.acp').write_bytes(acp(obj(2,name='Third')).replace(b'[#0000000000000001]',b'[#0000000000000002]'))
        result=environment.analyze([self.mod,other,third],self.game,self.index)
        self.assertEqual(result['resource_references'][0]['status'],'ambiguous')
        self.assertEqual(len(result['resource_references'][0]['targets']),2)

    def test_pack_dependencies_can_be_asset_libraries(self):
        self.base.write_bytes(pack([obj(1)],deps=['Icons.clb']));catalog.build(self.index,self.game)
        result=self.analyze()
        edge=next(e for e in result['pack_dependencies'] if e['pack']=='Icons.clb')
        self.assertEqual(edge['kind'],'asset_library');self.assertEqual(edge['status'],'found')

    def test_pack_import_cycle_enters_environment_gate(self):
        self.base.write_bytes(pack([obj(1)],deps=['EXTRA.RPK']))
        self.extra.write_bytes(pack([obj(2)],'EXTRA.RPK',deps=['TEST.RPK']))
        catalog.build(self.index,self.game)
        result=self.analyze()
        self.assertTrue(result['summary']['review_required'])
        self.assertEqual(result['summary']['issue_types']['pack_import_cycle'],1)
        cycle=result['pack_import_graph']['cycles'][0]
        self.assertEqual(set(cycle['files']),{str(self.base),str(self.extra)})
        self.assertEqual(cycle['cycle'][0],cycle['cycle'][-1])

    def test_acp_additive_dependencies_participate_in_cycles(self):
        self.extra.write_bytes(pack([obj(2)],'EXTRA.RPK',['TEST.RPK']))
        catalog.build(self.index,self.game)
        self.acp.write_bytes(overlay_imports.rewrite(self.acp.read_bytes(),'TEST.RPK',['EXTRA.RPK']))
        result=self.analyze()
        self.assertEqual(result['summary']['pack_import_cycles'],1)
        edge=next(e for e in result['pack_dependencies'] if e.get('mod_added'))
        self.assertFalse(edge['inherited_from_original'])
        self.assertEqual(edge['source'],str(self.base))

    def test_missing_required_mod_import_is_not_original_advisory(self):
        self.acp.write_bytes(overlay_imports.rewrite(self.acp.read_bytes(),'TEST.RPK',['MISSING.RPK']))
        result=self.analyze()
        issue=next(i for i in result['issues'] if i['issue']=='declared_import_missing')
        self.assertFalse(issue['advisory_only']);self.assertTrue(result['summary']['review_required'])

    def test_added_import_is_attributed_to_declaring_mod_not_base_game_pack(self):
        self.acp.write_bytes(overlay_imports.rewrite(self.acp.read_bytes(),'TEST.RPK',['MISSING.RPK']))
        report=self.analyze();explained=environment.explain(report,self.mod)
        issue=next(i for i in report['issues'] if i['issue']=='declared_import_missing')
        self.assertEqual(issue['source'],str(self.base))
        self.assertEqual(issue['declared_by'],[str(self.acp)])
        row=next(r for r in explained['rows'] if r['issue']=='declared_import_missing')
        self.assertEqual(row['owner']['path'],str(self.mod))
        self.assertEqual(explained['primary_summary']['actionable_issues'],1)
        self.assertTrue(explained['review_required'])

    def test_missing_resource_inheritance_requires_exact_property_and_id(self):
        original=obj(1,{1:link(999)})
        self.base.write_bytes(pack([original]));catalog.build(self.index,self.game)
        self.acp.write_bytes(acp(original))
        inherited=self.analyze()
        self.assertTrue(next(i for i in inherited['issues'] if i['issue']=='resource_missing')['inherited_from_original'])
        self.acp.write_bytes(acp(obj(1,{2:link(999)})))
        moved=self.analyze()
        self.assertFalse(next(i for i in moved['issues'] if i['issue']=='resource_missing')['inherited_from_original'])
        self.assertTrue(moved['summary']['review_required'])

    def test_findings_keep_other_mod_separate_and_primary_must_be_present(self):
        other=self.root/'mod-other';other.mkdir();(other/'other.acp').write_bytes(acp(obj(1,{1:link(999)})))
        report=environment.analyze([self.mod,other],self.game,self.index)
        result=environment.explain(report,self.mod)
        missing=next(r for r in result['rows'] if r['issue']=='resource_missing')
        self.assertEqual(missing['owner']['path'],str(other))
        self.assertEqual(result['primary_summary']['issues'],0)
        self.assertTrue(result['review_required'])
        with self.assertRaisesRegex(FormatError,'captured primary'):
            environment.explain(report,self.root/'absent')

    def test_overlapping_roots_are_not_assigned_a_guessed_owner(self):
        report=self.analyze()
        report['projects'].append(dict(report['projects'][0],path=str(self.root),name='Parent'))
        report['issues']=[{'issue':'asset_missing','source':{'file':str(self.acp)},'inherited_from_original':True}]
        result=environment.explain(report)
        self.assertEqual(result['rows'][0]['owner']['kind'],'ambiguous_selected_roots')
        self.assertFalse(result['rows'][0]['runtime_harmlessness_verified'])

    def test_legacy_mod_import_without_provenance_is_not_blame_assigned_to_game(self):
        report=self.analyze()
        report['issues']=[{'issue':'declared_import_missing','source':str(self.base),'inherited_from_original':False}]
        result=environment.explain(report)
        self.assertEqual(result['rows'][0]['owner']['kind'],'mod_declaration_owner_unavailable')

    def test_inherited_primary_asset_does_not_clear_review(self):
        data=obj(1,{1:mapping({0x14:string('root:/Title/absent.ogg'),0x17:string('Voice')})})
        self.base.write_bytes(pack([data]));catalog.build(self.index,self.game);self.acp.write_bytes(acp(data))
        report=self.analyze();result=environment.explain(report,self.mod)
        self.assertEqual(result['primary_summary']['inherited_reference_issues'],1)
        self.assertTrue(result['review_required']);self.assertTrue(result['rows'][0]['actionable'])

    def test_absent_optional_mod_import_is_advisory_and_not_a_graph_gap(self):
        self.acp.write_bytes(overlay_imports.rewrite(self.acp.read_bytes(),'TEST.RPK',['OPTIONAL.RPK'],True))
        result=self.analyze()
        self.assertFalse(result['summary']['review_required'])
        self.assertEqual(result['summary']['unresolved_pack_imports'],0)
        self.assertTrue(next(i for i in result['issues'] if i['issue']=='declared_import_missing')['advisory_only'])

    def test_native_saved_mod_dependency_fields_are_traversed(self):
        data=self.base.read_bytes();node=Node(data,176,len(data))
        updated=editing.rewrite_map(node,{0x25:struct.pack('<I',1)+string('EXTRA.RPK')})
        self.base.write_bytes(data[:12]+struct.pack('<I',len(updated))+data[16:176]+updated)
        self.extra.write_bytes(pack([obj(2)],'EXTRA.RPK',['TEST.RPK']))
        catalog.build(self.index,self.game)
        self.assertEqual(self.analyze()['summary']['pack_import_cycles'],1)

    def test_depth_and_resource_limit_are_visible(self):
        result=self.analyze(depth=0)
        self.assertEqual(result['summary']['resources_visited'],1)
        self.assertGreater(result['capture']['depth_boundary_references'],0)
        result=self.analyze(max_resources=1)
        self.assertTrue(result['capture']['limit_reached']);self.assertTrue(result['summary']['review_required'])

    def test_stale_game_index_is_rejected(self):
        self.extra.write_bytes(pack([obj(2,name='Changed')],'EXTRA.RPK'))
        with self.assertRaisesRegex(FormatError,'stale'):self.analyze()

    def test_unreadable_selected_mod_prevents_a_clean_environment_result(self):
        result=environment.analyze([self.mod,self.root/'missing-mod'],self.game,self.index)
        self.assertTrue(result['summary']['review_required'])
        self.assertIn('selected_project_unreadable',result['summary']['issue_types'])

    def test_asset_paths_cannot_escape_inventory_roots(self):
        inv=assets.Inventory(self.game,[self.mod])
        for value in ('root:/../private.dds','../private.dds','C:/private.dds'):
            hits,method=inv.resolve(assets.path_reference(value))
            self.assertEqual(hits,[]);self.assertIn('unsafe',method)

    def test_localization_variants_and_identical_files_are_not_guessed_winners(self):
        files=[]
        for locale in ('EN','CN'):
            p=self.root/locale/'Language'/locale/'Text.mo';p.parent.mkdir(parents=True);p.write_bytes(locale.encode())
            files.append({'file':str(p)})
        self.assertEqual(assets.classify_matches(files,{}),'localized_variants')
        first=self.root/'a.tam';second=self.root/'b.tam';first.write_bytes(b'same');second.write_bytes(b'same')
        fingerprints={}
        self.assertEqual(assets.classify_matches([{'file':str(first)},{'file':str(second)}],fingerprints),'equivalent_files')
        self.assertEqual(len(fingerprints),2)
        second.write_bytes(b'different')
        self.assertEqual(assets.classify_matches([{'file':str(first)},{'file':str(second)}],{}),'ambiguous')


if __name__=='__main__':unittest.main()
