import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import content_libraries,material_links,native_objects,native_validation
from aow4mod.authoring import string,string_vector
from aow4mod.core import FormatError,sha
from aow4mod.filesystem import file_hash
from test_aow4_mod_audit import mapping,typed,array,u32


def clb(namespace='TEST',effect_namespace='TEST',material=True,target_type=0x411669,dependencies=()):
    strings=bytearray()
    def text(value):
        raw=value.encode();start=len(strings);strings.extend(raw+b'\0');return struct.pack('<II',len(raw),start)
    name=text(namespace);members=[];target=f'[{effect_namespace}]EFFECT\\0'
    if material:members.append(typed(0x41166f,{20:text(f'[{namespace}]MAT\\0'),21:text('Material'),50:mapping({20:text(target)})}))
    if namespace==effect_namespace:members.append(typed(target_type,{20:text(target),21:text('Effect')}))
    fields={19:u32(8),20:name,21:array(members),28:b'\x01\x01\0\0'}
    if dependencies:fields[22]=string_vector(dependencies)
    metadata=mapping(dict(sorted(fields.items())));align=lambda b:bytes(b)+bytes(-len(b)%16)
    strings,metadata,data=map(align,(strings,metadata,b'opaque'))
    return content_libraries.SIGNATURE+struct.pack('<4I',8,len(strings),len(metadata),len(data))+bytes(8)+strings+metadata+data


def report_fixture(root,source,provided=(),resolved=True,wrong_type=False,warning=False):
    selected=material_links.members(source);contexts,targets=material_links.context(source,selected,provided);target=targets[0];identity=target['identity']
    before=mapping({20:string(selected[0]['locator']),21:string('Material'),50:mapping({20:string(identity['locator'])}),0x41166f:mapping({1:u32(5)})})
    effect=mapping({20:string(identity['locator']),21:string('Effect'),72:u32(7),73:b'program'})
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    def artifact(name,raw):
        path=root/name;path.write_bytes(raw);return {'file':str(path),'sha256':sha(raw)}
    a,b=artifact('before.bin',before),artifact('after.bin',before)
    row={'locator':selected[0]['locator'],'status':3,'declared_locator':identity['locator'],'expected':target,'resolved':resolved,
         'actual_cid':'0x00410035' if wrong_type else material_links.EFFECT if resolved else None,
         'target_ready':resolved and not wrong_type,'same_context_member':resolved,'resolution_stable':True,'artifacts':{'before':a,'after':b},
         'comparison':native_objects.compare_bodies(before,before),'source_comparison':native_objects.compare_bodies(before,before),
         'target_artifact':artifact('linked.bin',effect) if resolved and not wrong_type else None,
         'target_comparison':native_objects.compare_bodies(effect,effect) if resolved and not wrong_type else None}
    objects=[{'context':0,'identity':selected[0],'artifact':artifact('source.bin',before)}]
    if identity['type_id']==material_links.EFFECT:objects.append({'context':target['context'],'identity':identity,'artifact':artifact('independent.bin',effect)})
    row['issues']=material_links.issue_list(row);runs=[]
    for i in range(1+len({o['context'] for o in objects})):
        path=root/f'run-{i}';path.mkdir();(path/'stdout.log').write_text('Warning: synthetic fixture\n' if warning and i==0 else 'Content Registered\n',encoding='utf-8');runs.append(str(path))
    r={'format':'aow4mod-native-material-links/1','source':str(source),'source_sha256':file_hash(source),'library':'TEST','selection':selected,
       'context':contexts,'targets':targets,'binding':{'dll_sha256':'fixture'},'materials':[row],'objects':objects,'runs':runs,
       'diagnostics':[d for run in runs for d in native_validation.diagnostics(run)],'status':'fixture','coverage':'fixture'}
    r['comparison']=material_links.assess(r);return r


class MaterialLinksTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name).resolve();self.source=self.root/'TEST.clb';self.source.write_bytes(clb())

    def test_complete_source_and_effect_objects_are_required_for_success(self):
        r=report_fixture(self.root/'proof',self.source);result=material_links.assess(r)
        self.assertEqual(result['material_link_issue_count'],0);self.assertTrue(result['effect_objects_evaluated']);self.assertTrue(result['material_data_equal']);self.assertTrue(result['effect_data_equal'])
        r['objects'].pop()
        with self.assertRaises(FormatError):material_links.assess(r)

    def test_ready_material_with_unresolved_effect_still_requires_review(self):
        r=report_fixture(self.root/'proof',self.source,resolved=False);self.assertEqual(r['materials'][0]['status'],3)
        result=material_links.assess(r);self.assertEqual(result['material_link_issue_count'],1);self.assertFalse(result['effect_objects_evaluated']);self.assertIsNone(result['effect_data_equal'])
        r['materials'][0]['issues']=[]
        with self.assertRaises(FormatError):material_links.assess(r)

    def test_wrong_declared_and_actual_type_cannot_be_hidden_by_ready_flags(self):
        self.source.write_bytes(clb(target_type=0x410035));r=report_fixture(self.root/'proof',self.source,wrong_type=True)
        self.assertEqual(material_links.assess(r)['material_link_issue_count'],2)
        r['materials'][0]['actual_cid']=material_links.EFFECT
        with self.assertRaises(FormatError):material_links.assess(r)

    def test_mutated_effect_with_refreshed_hash_cannot_hide_its_independent_difference(self):
        r=report_fixture(self.root/'proof',self.source);item=r['materials'][0]['target_artifact'];raw=bytearray(Path(item['file']).read_bytes());raw[-1]^=1;Path(item['file']).write_bytes(raw);item['sha256']=sha(raw)
        with self.assertRaises(FormatError):material_links.assess(r)

    def test_mutated_both_material_witnesses_still_disagree_with_independent_source(self):
        r=report_fixture(self.root/'proof',self.source);row=r['materials'][0];raw=bytearray(Path(row['artifacts']['before']['file']).read_bytes());raw[-1]^=1
        for item in row['artifacts'].values():Path(item['file']).write_bytes(raw);item['sha256']=sha(raw)
        row['comparison']=native_objects.compare_bodies(raw,raw)
        with self.assertRaises(FormatError):material_links.assess(r)

    def test_context_binds_declared_closure_and_inferred_missing_import_targets(self):
        self.source.write_bytes(clb(effect_namespace='FX'));fx=self.root/'FX.clb';fx.write_bytes(clb('FX','FX',material=False))
        contexts,targets=material_links.context(self.source,material_links.members(self.source),[fx])
        self.assertEqual(len(contexts),2);self.assertEqual(contexts[0]['dependencies'],[]);self.assertEqual(targets[0]['context'],1)
        self.source.write_bytes(clb(effect_namespace='FX',dependencies=['MISSING.CLB']))
        with self.assertRaises(FormatError):material_links.context(self.source,material_links.members(self.source),[fx])

    def test_ambiguous_physical_library_names_are_refused(self):
        self.source.write_bytes(clb(effect_namespace='FX'));a=self.root/'a';b=self.root/'b';a.mkdir();b.mkdir();(a/'FX.clb').write_bytes(clb('FX','FX',material=False));(b/'FX.clb').write_bytes(clb('FX','FX',material=True))
        with self.assertRaises(FormatError):material_links.context(self.source,material_links.members(self.source),[a/'FX.clb',b/'FX.clb'])

    def test_wrong_declared_effect_type_is_rejected_before_native_launch(self):
        self.source.write_bytes(clb(target_type=0x410035))
        with patch.object(material_links.native,'run_worker',side_effect=AssertionError('unsafe native launch')),self.assertRaisesRegex(FormatError,'non-TREEffectResource'):
            material_links.check(self.source,['[TEST]MAT\\0'],self.root/'game')


if __name__=='__main__':unittest.main()
