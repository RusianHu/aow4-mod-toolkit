import copy
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import resource_templates as templates,native,native_roundtrip,editing,authoring,catalog
from aow4mod.core import Node,Resource,FormatError,read_rpk,references,sha
from aow4mod.filesystem import file_hash,inventory
from test_aow4_mod_audit import acp,resource,mapping,array,string,typed
from test_resource_groups import pack,category,located,groups


def generic(name,objects):
    data=pack([(0,groups.GENERIC,[category('Objects',0)],objects)])
    root=Node(data,176,len(data));payload=editing.rewrite_map(root,{0x16:string(name)})
    return data[:12]+struct.pack('<I',len(payload))+data[16:176]+payload


def with_link(data,target,locator=True):
    link=(mapping({0x14:string(f'[#{target:016X}]0\\{target}'),0x16:struct.pack('<Q',target),0x17:string('old label')}) if locator else
          typed(0x40004a,{0x14:struct.pack('<Q',target),0x16:string('old label')}))
    return editing.rewrite_typed(Node(data,0,len(data)),{0x471c6d:mapping({1:array([link])})})


class ResourceTemplateTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.game=self.root/'game';(self.game/'Content').mkdir(parents=True)
        self.template=self.game/'Content/TEMPLATE.rpk'
        self.template.write_bytes(generic('TEMPLATE.RPK',[with_link(located(1,0,1,'One'),2),with_link(located(2,0,2,'Two'),1,False)]))
        self.index=self.root/'index.sqlite';catalog.build(self.index,self.game)
        self.mod=self.root/'mod';self.mod.mkdir();self.acp=self.mod/'Own.acp';data=acp(resource())
        self.acp.write_bytes(data[:19]+editing.rewrite_wrapper(Node(data,19,len(data)),0x40009d,
            {0x2a:uuid.uuid4().bytes_le,0x1a:authoring.string_vector(['OWN.RPK'])}))
        self.target=self.mod/'Packs/OWN.rpk';self.target.parent.mkdir();self.target.write_bytes(generic('OWN.RPK',[located(100,0,0,'Existing')]))
        self.binding={'game':str(self.game),'dll_sha256':'fixture','backend_sha256':'fixture','profile':{'name':'fixture'}}
        self.public=native.public_binding(self.binding);self.counter=0
        for mock in [patch.object(native,'profile_for',return_value=self.binding),
                     patch.object(templates.native_objects,'roundtrip_data',side_effect=self.object_roundtrip),
                     patch.object(templates.native_roundtrip,'run',side_effect=self.pack_roundtrip)]:
            mock.start();self.addCleanup(mock.stop)

    def object_roundtrip(self,game,cid,body,*args):
        self.counter+=1;directory=self.root/f'object-{self.counter}';directory.mkdir()
        source=directory/'source.bin';state=directory/'state.bin';source.write_bytes(body);state.write_bytes(body)
        return body,{'type':{'cid':cid,'name':'Fixture'},'binding':self.public,'comparison':{'data_equal':True},
            'source_artifact':{'file':str(source),'sha256':sha(body)},'state_artifact':{'file':str(state),'sha256':sha(body)}}

    def pack_roundtrip(self,candidate,game,output,*args,**kwargs):
        output.mkdir();destination=output/candidate.name;shutil.copyfile(candidate,destination)
        return {'output_file':str(destination),'comparison':native_roundtrip.compare(candidate,destination),
                'status':'byte_identical','diagnostics':['fixture diagnostic'],'binding':self.public}

    def plan(self,**kwargs):
        values=dict(mod=self.mod,template=self.template,ids=['1'],into_pack='Packs/OWN.rpk',game=self.game,
            index=self.index,category='Objects',cache_dir=self.root/'cache')
        values.update(kwargs);return templates.make_plan(**values)

    def test_single_copy_remaps_identity_retains_external_link_and_adds_original_import(self):
        before=inventory(self.mod);plan=self.plan(name='New One');receipt=templates.apply_plan(plan,self.root/'out')
        info,rows=read_rpk(self.root/'out/Packs/OWN.rpk')
        self.assertEqual(rows[0].node.raw(),read_rpk(self.target)[1][0].node.raw());self.assertEqual(rows[1].name,'New One')
        self.assertEqual(references(rows[1])[0]['id'],'0000000000000002');self.assertIn('TEMPLATE.RPK',info['dependencies'])
        self.assertEqual((self.root/'out/Own.acp').read_bytes(),self.acp.read_bytes());self.assertEqual(inventory(self.mod),before)
        self.assertEqual(receipt['copied_resources'],1)

    def test_dependency_closure_preserves_cyclic_graph_and_updates_locator_links(self):
        plan=self.plan(with_dependencies=True);self.assertEqual(len(plan['id_map']),2)
        templates.apply_plan(plan,self.root/'out');_,rows=read_rpk(self.root/'out/Packs/OWN.rpk')
        first,second=rows[1:];a=plan['id_map']['0000000000000001']['identity'];b=plan['id_map']['0000000000000002']['identity']
        ref=references(first)[0];self.assertEqual(ref['id'],b['id']);self.assertEqual(ref['locator'],b['locator']);self.assertEqual(ref['name'],b['name'])
        self.assertEqual(references(second)[0]['id'],a['id']);self.assertNotIn('TEMPLATE.RPK',plan['dependencies'])

    def test_same_pack_copy_has_no_self_import(self):
        plan=self.plan(template=self.target,ids=['64']);self.assertEqual(plan['dependencies'],[])
        templates.apply_plan(plan,self.root/'out');self.assertEqual(len(read_rpk(self.root/'out/Packs/OWN.rpk')[1]),2)

    def test_identical_template_alias_is_allowed_without_a_self_import(self):
        alias=self.root/'alias.rpk';alias.write_bytes(self.target.read_bytes())
        plan=self.plan(template=alias,ids=['64']);self.assertEqual(plan['dependencies'],[])
        templates.apply_plan(plan,self.root/'out');self.assertEqual(len(read_rpk(self.root/'out/Packs/OWN.rpk')[1]),2)

    def test_opaque_id_field_blocks_copy_instead_of_blind_replacement(self):
        obj=located(1,0,1,'Opaque');obj=editing.rewrite_typed(Node(obj,0,len(obj)),{0x471c6d:mapping({1:struct.pack('<Q',1)})})
        self.template.write_bytes(generic('TEMPLATE.RPK',[obj]))
        with self.assertRaisesRegex(FormatError,'unrecognized 64-bit'):self.plan()

    def test_refreshed_forged_pack_cannot_change_an_old_target_resource(self):
        plan=self.plan();data=editing.transform_records(bytes.fromhex(plan['pack_hex']),'.rpk',lambda pack,node:
            editing.rewrite_typed(node,{0x15:string('Forged old resource')}) if Resource.parse(node).rid==100 else node.raw())
        plan.update(pack_hex=data.hex(),pack_sha256=sha(data));Path(plan['candidate_file']).write_bytes(data)
        Path(plan['native_roundtrip']['output_file']).write_bytes(data)
        with self.assertRaisesRegex(FormatError,'outside the selected'):templates.apply_plan(plan,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())

    def test_changed_template_source_and_object_artifacts_are_rejected(self):
        plan=self.plan();state=Path(plan['native_objects'][0]['state_artifact']['file']);state.write_bytes(b'changed')
        with self.assertRaisesRegex(FormatError,'object evidence'):templates.apply_plan(plan,self.root/'bad-object')
        self.template.write_bytes(self.template.read_bytes()+b'x')
        with self.assertRaisesRegex(FormatError,'template or target'):templates.apply_plan(plan,self.root/'bad-template')

    def test_native_pack_binding_cannot_be_replaced_with_another_profile(self):
        plan=copy.deepcopy(self.plan());plan['native_roundtrip']['binding']['profile']='another profile'
        with self.assertRaisesRegex(FormatError,'native pack evidence'):templates.apply_plan(plan,self.root/'bad-binding')
        self.assertFalse((self.root/'bad-binding').exists())

    def test_plan_selection_ids_names_and_class_cannot_disagree(self):
        plan=self.plan()
        for mutation in ['id','locator','class','name','selection']:
            changed=copy.deepcopy(plan);item=next(iter(changed['id_map'].values()))
            if mutation=='id':item['identity']['id']='0000000000000064'
            elif mutation=='locator':item['identity']['locator']='[#'+item['identity']['id']+']0\\99'
            elif mutation=='class':item['type_id']+=1
            elif mutation=='name':item['source_name']='false source'
            else:changed['selected_ids']=[]
            with self.assertRaises(FormatError):templates.apply_plan(changed,self.root/('bad-'+mutation))

    def test_bounds_wrong_scope_same_pack_name_and_large_closure_are_refused(self):
        with self.assertRaises(FormatError):self.plan(ids=['FFFF'])
        with self.assertRaises(FormatError):self.plan(with_dependencies=True,name='One name')
        with self.assertRaises(FormatError):self.plan(into_pack=self.template)
        self.template.write_bytes(generic('OWN.RPK',[located(1,0,1)]))
        with self.assertRaisesRegex(FormatError,'same pack name'):self.plan()

    def test_pure_selection_bound_and_opaque_strings(self):
        rows=[Resource.parse(Node(obj:=located(i,0,i),0,len(obj))) for i in range(1,258)]
        with self.assertRaisesRegex(FormatError,'256'):templates.select(rows,[f'{r.rid:X}' for r in rows])
        obj=located(1,0,1);obj=editing.rewrite_typed(Node(obj,0,len(obj)),{0x471c6d:mapping({1:string('ID 0000000000000001')})})
        row=Resource.parse(Node(obj,0,len(obj)));id_map={1:{'identity':{'id':'0000000000000003','name':'New','locator':'[#0000000000000003]0\\1'},'type_id':row.type_id,'source_name':row.name}}
        with self.assertRaisesRegex(FormatError,'opaque string'):templates.copy_definition(row,id_map)


if __name__=='__main__':unittest.main()
