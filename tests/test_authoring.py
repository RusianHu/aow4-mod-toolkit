from pathlib import Path
import copy
import json
import sys
import tempfile
import unittest
import uuid
import importlib.util

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import authoring
from aow4mod.core import FormatError,Node,read_acp
from aow4mod.filesystem import inventory
from test_aow4_mod_audit import acp,rpk,resource,typed,mapping,string,u32


class AuthoringTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.mod=self.root/'source';self.mod.mkdir();self.acp=self.mod/'source.acp'
        raw=acp(resource());fields=Node(raw,19,len(raw)).typed()[1][0x40009d].mapping()
        data={k:v.raw() for k,v in fields.items()};data[0x2a]=uuid.uuid4().bytes_le
        self.acp.write_bytes(raw[:19]+typed(0x40009d,{0x40009d:mapping(data)}))
        self.game=self.root/'game';(self.game/'Content').mkdir(parents=True)
        (self.game/'Content/test.rpk').write_bytes(rpk([resource()]))

    def test_blank_project_has_fresh_identity_and_no_copied_gameplay(self):
        out=self.root/'new';before=self.acp.read_bytes()
        created=authoring.create_project(self.mod,'New Mod','Author',out)
        meta,changes=read_acp(out/'mod/New Mod.acp')
        self.assertEqual(changes,[])
        self.assertEqual(meta['name'],'New Mod')
        self.assertNotEqual(meta['package_id'],read_acp(self.acp)[0]['package_id'])
        self.assertEqual(self.acp.read_bytes(),before)

    def test_import_build_updates_metadata_and_preserves_gameplay(self):
        project=self.root/'project';before=inventory(self.mod)
        (self.mod/'private-notes.txt').write_text('do not include', encoding='utf-8')
        authoring.import_project(self.mod,project)
        result=authoring.build_project(project,self.root/'release',self.game,'1.2')
        content=self.root/'release/content/source'
        meta,changes=read_acp(content/'source.acp')
        self.assertEqual(meta['version_minor'],2)
        self.assertEqual(changes[0]['resource'].node.raw(),read_acp(self.acp)[1][0]['resource'].node.raw())
        self.assertFalse((content/'private-notes.txt').exists())
        self.assertFalse(result['published'])
        manifest=json.loads((self.root/'release/manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(set(manifest['files']),{'source.acp','.metadata/metadata.json'})

    def test_unknown_blank_template_fields_are_not_silently_kept(self):
        raw=self.acp.read_bytes();fields=Node(raw,19,len(raw)).typed()[1][0x40009d].mapping()
        self.acp.write_bytes(raw[:19]+typed(0x40009d,{0x40009d:mapping({**{k:v.raw() for k,v in fields.items()},999:b'opaque'})}))
        with self.assertRaises(FormatError):authoring.create_project(self.mod,'New','',self.root/'bad')

    def test_build_rejects_escape_glob_and_preserves_source(self):
        project=self.root/'project';authoring.import_project(self.mod,project)
        config=project/authoring.CONFIG;value=json.loads(config.read_text(encoding='utf-8'));value['include']=['../secret.txt','*.acp'];config.write_text(json.dumps(value), encoding='utf-8')
        before=inventory(project/'mod')
        with self.assertRaises(FormatError):authoring.build_project(project,self.root/'bad',self.game)
        self.assertEqual(inventory(project/'mod'),before)
        self.assertFalse((self.root/'bad').exists())

    def test_output_and_version_guards(self):
        with self.assertRaises(FormatError):authoring.create_project(self.mod,'CON','',self.root/'bad')
        for version in ('1','1.2.3','bad','-1.0'):
            with self.assertRaises(FormatError):authoring.version_pair(version)
        project=self.root/'project';authoring.import_project(self.mod,project)
        with self.assertRaises(FormatError):authoring.build_project(project,project/'mod/build',self.game)

    def test_environment_missing_asset_enters_release_gate_and_manifest(self):
        from aow4mod import editing,publication
        plan=editing.make_plan(self.acp,'1','0x471c6d/2','root:/Title/missing.dds','string',allow_opaque=True)
        editing.apply_plan(plan,self.root/'with-asset')
        project=self.root/'asset-project';authoring.import_project(self.root/'with-asset',project)
        result=authoring.build_project(project,self.root/'asset-release',self.game)
        self.assertTrue(result['release_gate']['review_required'])
        manifest_path=self.root/'asset-release/manifest.json'
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        report=self.root/'asset-release/environment.json'
        env=json.loads(report.read_text(encoding='utf-8'))
        self.assertIn('asset_missing',{i['issue'] for i in env['issues']})
        self.assertTrue(all(Path(p).exists() for p in env['input_hashes']))
        publication.load_release(manifest_path,False)
        manifest['release_gate']['review_required']=False;manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'missing from the release gate'):publication.load_release(manifest_path,False)
        manifest['release_gate']['review_required']=True;manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        report.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'Environment report changed'):publication.load_release(manifest_path,False)

    def test_validation_cache_cannot_write_inside_mod_source(self):
        project=self.root/'project';authoring.import_project(self.mod,project)
        path=project/authoring.CONFIG;config=json.loads(path.read_text(encoding='utf-8'))
        config['validation']={'index':'mod/cache.sqlite'};path.write_text(json.dumps(config), encoding='utf-8')
        before=inventory(project/'mod')
        with self.assertRaises(FormatError):authoring.build_project(project,self.root/'bad-cache',self.game)
        self.assertEqual(inventory(project/'mod'),before)

    def test_circular_pack_imports_enter_build_and_publication_gate(self):
        from aow4mod import pack_imports, publication
        from test_environment import pack, obj
        first=self.game/'Content/test.rpk'
        first.write_bytes(pack_imports.rewrite(first.read_bytes(),['SECOND.RPK']))
        (self.game/'Content/second.rpk').write_bytes(pack([obj(2)],'SECOND.RPK',['TEST.RPK']))
        project=self.root/'cycle-project';authoring.import_project(self.mod,project)
        release=self.root/'cycle-release'
        result=authoring.build_project(project,release,self.game)
        self.assertTrue(result['release_gate']['review_required'])
        self.assertEqual(result['environment_summary']['pack_import_cycles'],1)
        manifest_path=release/'manifest.json'
        publication.load_release(manifest_path,False)
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['release_gate']['review_required']=False
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'missing from the release gate'):
            publication.load_release(manifest_path,False)

    def test_draft_mesh_assets_are_reviewed_and_source_fbx_is_not_published(self):
        from test_content_libraries import library_bytes
        (self.mod/'Libraries').mkdir();(self.mod/'Libraries/fixture.clb').write_bytes(library_bytes())
        retained=self.mod/'SourceMeshes/FIXTURE';retained.mkdir(parents=True)
        (retained/'source.fbx').write_bytes(b'author source retained')
        (retained/'.aow4mod-mesh.json').write_text(json.dumps({'draft':True,'target':'Libraries/fixture.clb'}), encoding='utf-8')
        project=self.root/'mesh-project';authoring.import_project(self.mod,project)
        result=authoring.build_project(project,self.root/'mesh-release',self.game)
        self.assertIn('draft_mesh_asset_requires_binding_review',{row['issue'] for row in result['release_gate']['issues']})
        manifest=json.loads((self.root/'mesh-release/manifest.json').read_text(encoding='utf-8'))
        self.assertFalse(any(value.startswith('SourceMeshes/') for value in manifest['files']))
        self.assertEqual(manifest['draft_mesh_assets'][0]['file'],'Libraries/fixture.clb')
        from aow4mod import publication
        path=self.root/'mesh-release/manifest.json';publication.load_release(path)
        manifest['release_gate']['issues']=[row for row in manifest['release_gate']['issues'] if row['issue']!='draft_mesh_asset_requires_binding_review']
        path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'Draft mesh findings'):publication.load_release(path)

    def test_draft_model_provenance_cannot_lose_its_manifest_or_gate(self):
        from test_content_libraries import library_bytes
        from aow4mod import publication
        (self.mod/'Libraries').mkdir();(self.mod/'Libraries/FIXTURE.clb').write_bytes(library_bytes())
        source=self.mod/'SourceModels/FIXTURE';source.mkdir(parents=True)
        (source/'.aow4mod-model.json').write_text(json.dumps({'format':'aow4mod-model-bind-receipt/1','draft':True,
            'target':'Libraries/FIXTURE.clb','models':[{'model':'[FIXTURE]MODEL\\0'}]}), encoding='utf-8')
        authoring.import_project(self.mod,self.root/'model-project');release=self.root/'model-release'
        result=authoring.build_project(self.root/'model-project',release,self.game)
        self.assertIn('draft_model_asset_requires_variant_review',{x['issue'] for x in result['release_gate']['issues']})
        path=release/'manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'));publication.load_release(path)
        self.assertFalse(any(p.startswith('SourceModels/') for p in manifest['files']))
        loaded,content=publication.load_release(path);frozen,_=publication.stage_release(loaded,content,release)
        bad_frozen=copy.deepcopy(frozen);bad_frozen['draft_model_assets']=[]
        with self.assertRaisesRegex(FormatError,'Draft model provenance'):publication.check_staged(bad_frozen)
        original=copy.deepcopy(manifest)
        manifest['release_gate']['issues']=[x for x in manifest['release_gate']['issues'] if x['issue']!='draft_model_asset_requires_variant_review']
        path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'Draft model findings'):publication.load_release(path)
        original['draft_model_assets']=[];path.write_text(json.dumps(original), encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'Draft model provenance'):publication.load_release(path)

    def test_map_metadata_draft_stays_reviewed_in_manifest_and_snapshot(self):
        from test_map_metadata import fixture
        from aow4mod import publication
        (self.mod/'Maps').mkdir();(self.mod/'Maps/Probe.tam').write_bytes(fixture())
        source=self.mod/'SourceMaps/Probe.tam';source.mkdir(parents=True)
        (source/'.aow4mod-map.json').write_text(json.dumps({'format':'aow4mod-map-metadata-receipt/1','target':'Maps/Probe.tam','draft':True}), encoding='utf-8')
        project=self.root/'map-project';authoring.import_project(self.mod,project);release=self.root/'map-release'
        result=authoring.build_project(project,release,self.game);self.assertIn('draft_map_asset_requires_playability_review',{x['issue'] for x in result['release_gate']['issues']})
        path=release/'manifest.json';loaded,content=publication.load_release(path);frozen,_=publication.stage_release(loaded,content,release)
        bad=copy.deepcopy(frozen);bad['draft_map_assets']=[]
        with self.assertRaisesRegex(FormatError,'map provenance'):publication.check_staged(bad)
        bad=copy.deepcopy(frozen);bad['release_gate']['issues']=[]
        with self.assertRaisesRegex(FormatError,'map findings'):publication.check_staged(bad)

    def test_select_staged_source_and_verified_rollback(self):
        import shutil
        project=self.root/'project';authoring.import_project(self.mod,project)
        shutil.copytree(project/'mod',project/'stages/revision')
        original=(project/authoring.CONFIG).read_bytes()
        result=authoring.select_source(project,'stages/revision')
        self.assertEqual(authoring.load_project(project)[2],(project/'stages/revision').resolve())
        authoring.rollback_source(project,result['history'])
        self.assertEqual((project/authoring.CONFIG).read_bytes(),original)
        self.assertTrue((project/'stages/revision').exists())

    def test_import_includes_the_actual_preview_file(self):
        raw=self.acp.read_bytes();fields=Node(raw,19,len(raw)).typed()[1][0x40009d].mapping()
        self.acp.write_bytes(raw[:19]+typed(0x40009d,{0x40009d:mapping({**{k:v.raw() for k,v in fields.items()},0x1e:string('custom-preview.png')})}))
        (self.mod/'custom-preview.png').write_bytes(b'preview fixture')
        project=self.root/'project';authoring.import_project(self.mod,project)
        config=json.loads((project/authoring.CONFIG).read_text(encoding='utf-8'))
        self.assertIn('custom-preview.png',config['include'])
        authoring.build_project(project,self.root/'release',self.game)
        self.assertEqual((self.root/'release/content/source/custom-preview.png').read_bytes(),b'preview fixture')

    @unittest.skipUnless(importlib.util.find_spec('polib'),'optional localization dependency')
    def test_build_compiles_localization_without_shipping_po(self):
        import polib
        po=polib.POFile();po.metadata={'Content-Type':'text/plain; charset=UTF-8'}
        po.append(polib.POEntry(msgid='MOD@NAME',msgstr='中文名称'))
        (self.mod/'Language').mkdir();po.save(str(self.mod/'Language/text.po'))
        project=self.root/'project';authoring.import_project(self.mod,project)
        config_path=project/authoring.CONFIG;config=json.loads(config_path.read_text(encoding='utf-8'))
        config['localization']=[{'source':'Language/text.po','output':'Language/text.mo','require_complete':True}]
        config_path.write_text(json.dumps(config), encoding='utf-8')
        result=authoring.build_project(project,self.root/'release',self.game)
        self.assertEqual(result['localization_jobs'],1)
        self.assertEqual(result['localization_catalogs'],1)
        self.assertFalse((self.root/'release/content/source/Language/text.po').exists())
        compiled=polib.mofile(str(self.root/'release/content/source/Language/text.mo'))
        self.assertEqual(compiled[0].msgstr,'中文名称')


if __name__=='__main__':unittest.main()
