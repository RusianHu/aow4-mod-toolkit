import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import content_libraries,material_links,material_textures as links,native_objects,native_release,native_texture_release
from aow4mod.core import FormatError,sha
from aow4mod.filesystem import file_hash
from aow4mod.authoring import string
from test_aow4_mod_audit import mapping,typed,array,u32


def library_bytes(empty=False):
    strings=bytearray()
    def text(value):
        raw=value.encode();start=len(strings);strings.extend(raw+b'\0');return struct.pack('<II',len(raw),start)
    prop=typed(0x411695,{40:text('DiffuseTexture'),100:mapping({20:text('[OWN]TX\\0')})})
    mat=typed(0x41166f,{20:text('[OWN]MAT\\0'),21:text('Material'),50:mapping({20:text('[OWN]EFFECT\\0')}),51:array([] if empty else [prop])})
    tex=typed(0x41003d,{20:text('[OWN]TX\\0'),21:text('Texture'),30:u32(2),31:u32(2)})
    effect=typed(0x411669,{20:text('[OWN]EFFECT\\0'),21:text('Effect')})
    metadata=mapping({19:u32(8),20:text('OWN'),21:array([mat,tex,effect]),28:b'\x01\x01\0\0'});align=lambda b:bytes(b)+bytes(-len(b)%16)
    strings,metadata,data=map(align,(strings,metadata,b'pixel payload'))
    return content_libraries.SIGNATURE+struct.pack('<4I',8,len(strings),len(metadata),len(data))+bytes(8)+strings+metadata+data


def fixture(root,source,empty=False):
    root=Path(root);root.mkdir(parents=True);lib=content_libraries.read(source);selected=material_links.members(source)
    context,targets,specs=links.context(source,selected)
    prop=mapping({40:string('DiffuseTexture'),100:mapping({20:string('[OWN]TX\\0')})})
    before=mapping({20:string('[OWN]MAT\\0'),21:string('Material'),51:array([] if empty else [struct.pack('<I',0x411695)+prop])})
    texture=mapping({20:string('[OWN]TX\\0'),21:string('Texture'),30:u32(2),31:u32(2),40:b'original pixels'})
    def artifact(name,body):
        p=root/name;p.write_bytes(body);return {'file':str(p),'sha256':sha(body)}
    source_artifact=artifact('source.bin',before);target_artifact=artifact('target.bin',texture)
    rows=[]
    if not empty:
        row={**specs[0]['textures'][0],'expected':targets[0][0],'resolved':True,'actual_cid':links.TEXTURE,'target_ready':True,
             'same_context_member':True,'resolution_stable':True,'artifacts':{'before':artifact('prop-before.bin',prop),'after':artifact('prop-after.bin',prop)},
             'target_artifact':target_artifact,'comparison':native_objects.compare_bodies(prop,prop),'target_comparison':native_objects.compare_bodies(texture,texture),'issues':[]}
        rows.append(row)
    materials=[{'locator':selected[0]['locator'],'property_count':0 if empty else 1,'textures':rows,
                'artifacts':{'before':source_artifact,'after':artifact('after.bin',before)},'comparison':native_objects.compare_bodies(before,before),'source_comparison':native_objects.compare_bodies(before,before)}]
    objects=[{'context':0,'identity':selected[0],'artifact':artifact('source-independent.bin',before)}]
    if not empty:objects.append({'context':0,'identity':lib.members[1]['identity'],'artifact':artifact('target-independent.bin',texture)})
    runs=[]
    for i in range(2):
        run=root/f'run-{i}';run.mkdir();(run/'stdout.log').write_text('Native fixture completed\n', encoding='utf-8');runs.append(str(run))
    r={'format':links.FORMAT,'source':str(source),'source_sha256':file_hash(source),'selection':selected,'context':context,'targets':targets,'declarations':specs,
       'materials':materials,'objects':objects,'runs':runs,'binding':{'game':'fixture','dll_sha256':'fixture','backend_sha256':'fixture','profile':'fixture'},'diagnostics':[]}
    r['comparison']=links.assess(r);r['status']='textures_resolved' if not empty else 'review_required';return r


class MaterialTextureTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve();self.content=self.root/'content';self.content.mkdir();self.source=self.content/'OWN.clb';self.source.write_bytes(library_bytes())
        self.r=fixture(self.root/'run',self.source)

    def test_complete_pointer_and_independent_objects_are_required(self):
        d=links.recheck(self.r,self.source);self.assertEqual(d['texture_count'],1);self.assertEqual(d['texture_link_issue_count'],0);self.assertTrue(d['texture_objects_evaluated'])

    def test_missing_live_pointer_remains_issue_even_with_valid_independent_file(self):
        r=copy.deepcopy(self.r);row=r['materials'][0]['textures'][0];row.update(resolved=False,actual_cid=None,target_ready=False,same_context_member=False,target_artifact=None,target_comparison=None)
        row['issues']=links.issue_list(row);d=links.assess(r)
        self.assertGreater(d['texture_link_issue_count'],0);self.assertFalse(d['texture_objects_evaluated']);self.assertTrue(d['serialized_texture_data_equal'])

    def test_ready_wrong_type_cannot_pass(self):
        r=copy.deepcopy(self.r);row=r['materials'][0]['textures'][0];row.update(actual_cid='0x0041004B',target_artifact=None,target_comparison=None)
        row['issues']=links.issue_list(row);self.assertIn('resolved_texture_wrong_type',row['issues']);self.assertGreater(links.assess(r)['texture_link_issue_count'],0)

    def test_omitting_texture_row_is_rejected(self):
        r=copy.deepcopy(self.r);r['materials'][0]['textures']=[]
        with self.assertRaises(FormatError):links.assess(r)

    def test_missing_independent_texture_is_rejected(self):
        r=copy.deepcopy(self.r);r['objects']=r['objects'][:1]
        with self.assertRaises(FormatError):links.assess(r)

    def test_wrong_property_name_is_rejected(self):
        r=copy.deepcopy(self.r);r['materials'][0]['textures'][0]['name']='Other'
        with self.assertRaises(FormatError):links.assess(r)

    def test_parent_data_change_is_not_hidden_by_matching_property(self):
        r=copy.deepcopy(self.r);m=r['materials'][0];data=Path(m['artifacts']['after']['file']).read_bytes()+b'changed';Path(m['artifacts']['after']['file']).write_bytes(data);m['artifacts']['after']['sha256']=sha(data)
        with self.assertRaises(FormatError):links.assess(r)

    def test_unstable_resolution_is_a_separate_issue(self):
        r=copy.deepcopy(self.r);row=r['materials'][0]['textures'][0];row['resolution_stable']=False;row['issues']=links.issue_list(row)
        self.assertEqual(links.assess(r)['texture_link_issue_count'],1)

    def test_texture_mismatch_survives_updated_hash_and_comparison(self):
        r=copy.deepcopy(self.r);row=r['materials'][0]['textures'][0];item=row['target_artifact'];old=Path(item['file']).read_bytes();data=old.replace(b'original pixels',b'modified pixels');Path(item['file']).write_bytes(data);item['sha256']=sha(data)
        row['target_comparison']=native_objects.compare_bodies(data,old);row['issues']=links.issue_list(row)
        d=links.assess(r);self.assertGreater(d['texture_link_issue_count'],0);self.assertFalse(d['serialized_texture_data_equal'])

    def test_zero_texture_selection_is_explicitly_unevaluated(self):
        self.source.write_bytes(library_bytes(True));r=fixture(self.root/'empty',self.source,True);d=links.assess(r)
        self.assertFalse(d['texture_objects_evaluated']);self.assertEqual(d['materials_without_supported_textures'],1)

    def test_duplicate_source_selection_is_rejected(self):
        r=copy.deepcopy(self.r);r['selection']*=2
        with self.assertRaises(FormatError):links.recheck(r,self.source)

    def test_changed_source_graph_is_rejected_before_object_claims(self):
        r=copy.deepcopy(self.r);r['declarations'][0]['textures'][0]['locator']='[OWN]TX\\1'
        with self.assertRaises(FormatError):links.recheck(r,self.source)

    def test_protocol_eight_adds_texture_coverage_and_independent_counter(self):
        _,old=native_release.targets(self.content,include_materials=True);_,new=native_release.targets(self.content,include_materials=True,include_textures=True)
        self.assertFalse(any(t['operation']=='clb_material_textures' for t in old));self.assertEqual(sum(t['operation']=='clb_material_textures' for t in new),1)
        row={'operation':'clb_material_textures','state':'review_required','data_equal':True,'texture_count':1,'texture_link_issue_count':1,'texture_objects_evaluated':False}
        summary=native_release.summary([row],8);self.assertEqual(summary['material_texture_issue_count'],1);self.assertEqual(summary['mismatches'],0);self.assertTrue(summary['review_required'])
        self.assertNotIn('material_texture_issue_count',native_release.summary([],7))

    def frozen(self):
        root=self.root/'release';unit=root/'native-evidence/0000';unit.mkdir(parents=True)
        with patch.object(links,'check',return_value=copy.deepcopy(self.r)):
            detail,_,_=native_texture_release.collect(self.source,self.content,self.root/'game',unit,root,self.root/'cache',30)
        return detail,root

    def test_frozen_graph_replays_without_native_backend(self):
        detail,root=self.frozen();result=native_texture_release.assess(detail,self.source,self.content,root)
        self.assertEqual(result['texture_link_issue_count'],0);self.assertEqual(result['texture_object_count'],1)

    def test_frozen_missing_target_object_is_rejected(self):
        detail,root=self.frozen();detail['batches'][0]['objects']=detail['batches'][0]['objects'][:1]
        with self.assertRaises(FormatError):native_texture_release.assess(detail,self.source,self.content,root)

    def test_frozen_wrong_runtime_type_and_zero_summary_are_rejected(self):
        detail,root=self.frozen();detail['batches'][0]['materials'][0]['textures'][0]['actual_cid']='0x0041004B'
        with self.assertRaises(FormatError):native_texture_release.assess(detail,self.source,self.content,root)

    def test_frozen_missing_raw_log_is_rejected(self):
        detail,root=self.frozen();detail['batches'][0]['logs'].pop()
        with self.assertRaises(FormatError):native_texture_release.assess(detail,self.source,self.content,root)

    def test_frozen_declared_target_cannot_be_swapped(self):
        detail,root=self.frozen();detail['batches'][0]['targets'][0][0]['identity']['type_id']='0x0041004B'
        with self.assertRaises(FormatError):native_texture_release.assess(detail,self.source,self.content,root)


if __name__=='__main__':unittest.main()
