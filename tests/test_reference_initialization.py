import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import reference_initialization as init
from aow4mod.core import FormatError,Node,read_rpk,references
from test_linking import obj,link
from test_pack_imports import named_pack
from test_aow4_mod_audit import mapping,string,typed


class ReferenceInitializationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve()
        self.mod=self.root/'mod';self.mod.mkdir();self.source=self.mod/'own.rpk'
        self.source.write_bytes(named_pack([obj(1,props={1:b'\0',2:b'keep'})],'OWN.RPK'))
        self.template=self.root/'template.rpk';self.template.write_bytes(named_pack([obj(10,props={1:link(2)}),obj(2)],'BASE.RPK'))
        self.target=self.root/'target.rpk';self.target.write_bytes(named_pack([obj(3),obj(4,0x471c72)],'TARGET.RPK'))

    def plan(self,**kwargs):
        values=dict(source=self.source,rid='1',property_path='0x471c6d/1',template_file=self.template,
            template_id='A',target_id='3',index=None,target_file=self.target,old_target_file=self.template,source_root=self.mod)
        values.update(kwargs);return init.make_plan(**values)

    def test_empty_map_reference_is_initialized_with_type_template_and_import(self):
        before=self.source.read_bytes();plan=self.plan();result=init.apply_plan(plan,self.root/'out')
        meta,rows=read_rpk(self.root/'out/own.rpk')
        self.assertEqual(meta['dependencies'],['TARGET.RPK']);self.assertEqual(references(rows[0])[0]['id'],'0000000000000003')
        self.assertEqual(rows[0].fields[0x471c6d].mapping()[2].raw(),b'keep')
        self.assertEqual(self.source.read_bytes(),before);self.assertEqual(result['before'],'empty_map')

    def test_omitted_direct_field_is_inserted_without_adding_a_class_scope(self):
        self.source.write_bytes(named_pack([obj(1,props={2:b'keep'})],'OWN.RPK'))
        plan=self.plan();self.assertEqual(plan['before'],'omitted');init.apply_plan(plan,self.root/'out')
        self.assertEqual(len(references(read_rpk(self.root/'out/own.rpk')[1][0])),1)

    def test_recognized_zero_id_forms_are_empty(self):
        values=[typed(0x40004a,{0x14:bytes(8),0x16:string('')}),
                mapping({0x14:string(''),0x16:bytes(8),0x17:string('')})]
        for i,value in enumerate(values):
            self.source.write_bytes(named_pack([obj(1,props={1:value})],'OWN.RPK'))
            plan=self.plan();init.apply_plan(plan,self.root/f'out{i}')
            self.assertEqual(references(read_rpk(self.root/f'out{i}/own.rpk')[1][0])[0]['id'],'0000000000000003')

    def test_existing_reference_and_arbitrary_data_are_not_overwritten(self):
        for value in [link(2),b'opaque',b'\x01\x01\0\0']:
            self.source.write_bytes(named_pack([obj(1,props={1:value})],'OWN.RPK'))
            with self.assertRaisesRegex(FormatError,'only fills'):self.plan()

    def test_same_class_field_and_target_type_are_required(self):
        with self.assertRaisesRegex(FormatError,'target type differs'):self.plan(target_id='4')
        self.template.write_bytes(named_pack([obj(10,0x471c72,props={1:link(2)}),obj(2)],'BASE.RPK'))
        with self.assertRaisesRegex(FormatError,'same resource class'):self.plan()
        self.template.write_bytes(named_pack([obj(10,props={2:link(2)}),obj(2)],'BASE.RPK'))
        with self.assertRaisesRegex(FormatError,'does not serialize'):self.plan()

    def test_empty_template_or_nested_path_is_not_a_reference_initializer(self):
        self.template.write_bytes(named_pack([obj(10,props={1:b'\0'}),obj(2)],'BASE.RPK'))
        with self.assertRaisesRegex(FormatError,'nonempty'):self.plan()
        with self.assertRaises(FormatError):self.plan(property_path='0x471c6d/1/0')

    def test_changed_template_target_source_and_forged_plan_are_refused(self):
        plan=self.plan();changed=copy.deepcopy(plan);changed['edit']['operations'][0]['replacement_hex']='00'
        with self.assertRaises(FormatError):init.apply_plan(changed,self.root/'forged')
        self.template.write_bytes(self.template.read_bytes()+b'x')
        with self.assertRaisesRegex(FormatError,'changed'):init.apply_plan(plan,self.root/'changed')
        self.assertFalse((self.root/'forged').exists());self.assertFalse((self.root/'changed').exists())

    def test_source_root_escape_and_existing_output_are_refused(self):
        with self.assertRaisesRegex(FormatError,'outside'):self.plan(source_root=self.mod/'other')
        output=self.root/'existing';output.mkdir()
        with self.assertRaises(FormatError):init.apply_plan(self.plan(),output)


if __name__=='__main__':unittest.main()
