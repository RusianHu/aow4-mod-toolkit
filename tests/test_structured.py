import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import structured,editing
from aow4mod.core import Node,FormatError,read_acp
from test_aow4_mod_audit import acp,resource,mapping,typed,array,u32,string


class StructuredTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        (self.root/'mod').mkdir();self.source=self.root/'mod/mod.acp'
        self.value=mapping({0x1e:array([typed(0x471234,{0x471234:mapping({7:u32(5),8:string('keep')})}),b'opaque sibling'])})
        self.source.write_bytes(acp(resource(value=self.value)))
        self.path='0x471c6d/1/1e/1/0/471234/7'

    def plan(self,value=8,codec='u32',path=None):
        return editing.make_plan(self.source,'1',path or self.path,value,codec,allow_opaque=True)

    def test_nested_edit_preserves_opaque_sibling_and_other_fields(self):
        plan=self.plan();out=self.root/'staged';receipt=editing.apply_plan(plan,out)
        self.assertEqual(receipt['verified_changed_fields'],1)
        raw=read_acp(out/'mod.acp')[1][0]['resource'].leaves()['0x00471C6D/0x01']
        array_node=Node(raw,0,len(raw)).mapping()[0x1e]
        self.assertEqual(array_node.array()[1].raw(),b'opaque sibling')
        fields=array_node.array()[0].typed()[1][0x471234].mapping()
        self.assertEqual(fields[7].uint(4),8);self.assertEqual(fields[8].string(),'keep')

    def test_unset_array_item_reindexes_the_envelope(self):
        out=self.root/'deleted';editing.apply_plan(self.plan(None,'unset','0x471c6d/1/1e/1/0'),out)
        raw=read_acp(out/'mod.acp')[1][0]['resource'].leaves()['0x00471C6D/0x01']
        items=Node(raw,0,len(raw)).mapping()[0x1e].array()
        self.assertEqual(list(items),[0]);self.assertEqual(items[0].raw(),b'opaque sibling')

    def test_nested_tampering_and_unacknowledged_opaque_edits_are_rejected(self):
        plan=self.plan();plan['operations'][0]['nested']['value']=100
        with self.assertRaises(FormatError):editing.apply_plan(plan,self.root/'bad')
        with self.assertRaises(FormatError):editing.make_plan(self.source,'1',self.path,8,'u32')
        with self.assertRaises(FormatError):self.plan(path=self.path+'/1')

    def test_inspection_is_paginated_and_does_not_require_an_index(self):
        report=structured.inspect(self.source,'1','0x471c6d/1/1e/1',limit=1)
        self.assertEqual(report['total'],2);self.assertEqual(report['next_offset'],1)
        self.assertEqual(report['items'][0]['key'],'0x0')

    def test_float_and_vector_codecs_are_finite_and_exact_width(self):
        self.assertEqual(editing.encode_value('f32',1.5),struct.pack('<f',1.5))
        self.assertEqual(editing.encode_value('vec3f',[1,2,3]),struct.pack('<3f',1,2,3))
        for codec,value in [('f32',float('nan')),('f64',float('inf')),('f32',True),('vec3f',[1,2]),('f32',1e100),('f64',10**1000)]:
            with self.assertRaises(FormatError):editing.encode_value(codec,value)

    def test_array_insert_and_copy_template_preserve_all_element_bytes(self):
        plan=structured.make_array_plan(self.source,'1','0x471c6d/1/1e','insert',1,
            copy_from={'file':self.source,'id':'1','property':'0x471c6d/1/1e/1/0'},allow_opaque=True)
        out=self.root/'inserted';editing.apply_plan(plan,out)
        raw=read_acp(out/'mod.acp')[1][0]['resource'].leaves()['0x00471C6D/0x01']
        items=Node(raw,0,len(raw)).mapping()[0x1e].array()
        self.assertEqual(len(items),3);self.assertEqual(items[0].raw(),items[1].raw())
        self.assertEqual(items[2].raw(),b'opaque sibling')

    def test_array_append_replace_and_remove_validate_positions_and_evidence(self):
        append=structured.make_array_plan(self.source,'1','0x471c6d/1/1e','append',codec='string',value='new',allow_opaque=True)
        self.assertEqual(append['operations'][0]['array_edit']['after_count'],3)
        changed=copy.deepcopy(append);changed['operations'][0]['array_edit']['value']='different'
        with self.assertRaises(FormatError):editing.validate_plan(changed)
        replace=structured.make_array_plan(self.source,'1','0x471c6d/1/1e','replace',1,codec='u32',value=7,allow_opaque=True)
        editing.validate_plan(replace)
        remove=structured.make_array_plan(self.source,'1','0x471c6d/1/1e','remove',0,allow_opaque=True)
        editing.validate_plan(remove)
        with self.assertRaises(FormatError):structured.make_array_plan(self.source,'1','0x471c6d/1/1e','remove',2,allow_opaque=True)

    def test_array_order_follows_indices_even_with_noncanonical_payload_order(self):
        raw=mapping({1:mapping({1:string('B'),0:string('A')})});node=Node(raw,0,len(raw))
        self.assertEqual([n.string() for n in node.array().values()],['A','B'])
        result=structured.array_bytes(node,'insert',1,string('between'))
        self.assertEqual([n.string() for n in Node(result,0,len(result)).array().values()],['A','between','B'])

    def test_explicit_path_disambiguates_native_empty_container(self):
        raw=bytes.fromhex('011e0001010000');node=Node(raw,0,len(raw))
        with self.assertRaisesRegex(FormatError,'unique'):structured.children(node)
        typed,fields,kind=structured.children(node,0x1e)
        self.assertFalse(typed);self.assertIsNone(kind);self.assertEqual(len(fields[0x1e].array()),0)
        self.assertEqual(structured.find_node(node,[0x1e]).raw(),fields[0x1e].raw())
        with self.assertRaises(FormatError):structured.find_node(node,[99])


if __name__=='__main__':unittest.main()
