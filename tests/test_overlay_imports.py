import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import overlay_imports as imports
from aow4mod import editing, linking, reference_arrays, reference_initialization, projects
from aow4mod.core import FormatError, Node, read_acp, references
import struct
from test_aow4_mod_audit import acp, mapping, array, string
from test_linking import obj, link
from test_pack_imports import named_pack


class OverlayImportTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.mod=self.root/'mod';self.mod.mkdir()
        self.source=self.mod/'mod.acp'
        self.source.write_bytes(acp(obj(1,props={1:link(2),2:b'opaque sibling'})))
        self.original=self.root/'original.rpk';self.original.write_bytes(named_pack([obj(2)],'TEST.RPK'))
        self.target=self.mod/'new.rpk';self.target.write_bytes(named_pack([obj(3)],'NEW.RPK'))

    def test_change_blob_length_is_updated_and_malformed_source_refused(self):
        raw=self.source.read_bytes();root=Node(raw,19,len(raw));fields=root.typed()[1][0x40009d].mapping()
        valid=raw[:19]+editing.rewrite_wrapper(root,0x40009d,{0x25:struct.pack('<I',len(fields[0x26].raw()))})
        self.source.write_bytes(valid)
        plan=imports.make_plan(self.source,'TEST.RPK',['NEW.RPK'])
        imports.apply_plan(plan,self.root/'length-updated');updated=(self.root/'length-updated/mod.acp').read_bytes()
        after=Node(updated,19,len(updated)).typed()[1][0x40009d].mapping()
        self.assertEqual(after[0x25].uint(4),len(after[0x26].raw()))
        self.assertNotEqual(after[0x25].uint(4),len(fields[0x26].raw()))
        corrupted=valid[:19]+editing.rewrite_wrapper(Node(valid,19,len(valid)),0x40009d,{0x25:struct.pack('<I',1)})
        self.source.write_bytes(corrupted)
        with self.assertRaisesRegex(FormatError,'change-blob length'):imports.make_plan(self.source,'TEST.RPK',['NEW.RPK'])
        with self.assertRaisesRegex(FormatError,'change-blob length'):read_acp(self.source)

    def test_zero_declared_blob_with_absent_changes_is_valid(self):
        raw=self.source.read_bytes();root=Node(raw,19,len(raw))
        data=raw[:19]+editing.rewrite_wrapper(root,0x40009d,{0x25:bytes(4),0x26:None})
        self.source.write_bytes(data)
        self.assertEqual(read_acp(self.source)[1],[])
        self.assertEqual(imports.records(data),[])

    def test_required_optional_casefold_and_resource_preservation(self):
        before=self.source.read_bytes()
        plan=imports.make_plan(self.source,'test.rpk',['NEW.RPK','new.rpk'])
        self.assertEqual(plan['after'],['NEW.RPK'])
        imports.apply_plan(plan,self.root/'required')
        updated=self.root/'required/mod.acp'
        plan=imports.make_plan(updated,'TEST.RPK',['OPTIONAL.RPK'],optional=True)
        imports.apply_plan(plan,self.root/'both')
        output=self.root/'both/mod.acp';state=imports.selected(output.read_bytes(),'TEST.RPK')
        self.assertEqual(state,{'pack':'TEST.RPK','required':['NEW.RPK'],'optional':['OPTIONAL.RPK']})
        self.assertEqual(read_acp(output)[1][0]['resource'].node.raw(),read_acp(self.source)[1][0]['resource'].node.raw())
        removal=imports.make_plan(output,'TEST.RPK',remove=['new.rpk'])
        imports.apply_plan(removal,self.root/'removed')
        self.assertEqual(imports.selected((self.root/'removed/mod.acp').read_bytes(),'TEST.RPK')['required'],[])
        self.assertEqual(self.source.read_bytes(),before)

    def test_other_mod_records_and_unknown_fields_are_unchanged(self):
        data=self.source.read_bytes();root=Node(data,19,len(data));scope=root.typed()[1][0x40009d].mapping()
        record=next(iter(scope[0x26].array().values()))
        other=editing.rewrite_wrapper(record,0x401204,{1:string('OTHER.RPK'),99:b'unknown'})
        payload=editing.rewrite_wrapper(root,0x40009d,{0x26:editing.replace_array_items(scope[0x26],[record.raw(),other]),77:b'root opaque'})
        data=data[:19]+payload;changed=imports.rewrite(data,'TEST.RPK',['NEW.RPK'])
        fields=Node(changed,19,len(changed)).typed()[1][0x40009d].mapping()
        self.assertEqual(list(fields[0x26].array().values())[1].raw(),other)
        self.assertEqual(fields[77].raw(),b'root opaque')

    def test_invalid_noop_self_import_and_ambiguous_pack_are_refused(self):
        for kwargs in ({},{'add':['TEST.RPK']},{'add':['../bad.rpk']},
                       {'remove':['missing.rpk']},{'add':['a.rpk'],'remove':['A.RPK']}):
            with self.subTest(kwargs=kwargs),self.assertRaises(FormatError):
                imports.make_plan(self.source,'TEST.RPK',**kwargs)
        with self.assertRaises(FormatError):imports.make_plan(self.source,'missing.rpk',['NEW.RPK'])
        data=self.source.read_bytes();node=Node(data,19,len(data));scope=node.typed()[1][0x40009d].mapping()
        record=next(iter(scope[0x26].array().values())).raw()
        self.source.write_bytes(data[:19]+editing.rewrite_wrapper(node,0x40009d,
            {0x26:editing.replace_array_items(scope[0x26],[record,record])}))
        with self.assertRaises(FormatError):imports.make_plan(self.source,'TEST.RPK',['NEW.RPK'])

    def test_changed_siblings_tampering_and_existing_outputs_are_refused(self):
        plan=imports.make_plan(self.source,'TEST.RPK',['NEW.RPK'])
        altered=copy.deepcopy(plan);altered['after'].append('unexpected.rpk')
        with self.assertRaises(FormatError):imports.apply_plan(altered,self.root/'forged')
        with self.assertRaises(FormatError):imports.apply_plan(plan,self.mod)
        self.target.write_bytes(b'changed')
        with self.assertRaises(FormatError):imports.apply_plan(plan,self.root/'stale')
        self.assertFalse((self.root/'forged').exists());self.assertFalse((self.root/'stale').exists())

    def test_atomic_direct_link_adds_acp_import_and_reachable_local_pack(self):
        plan=linking.make_plan(self.source,'1','0x471c6d/1','3',None,self.target,self.original,source_root=self.mod)
        self.assertEqual(plan['imports']['kind'],'acp_mod_dependencies')
        output=self.root/'linked';linking.apply_plan(plan,output)
        self.assertEqual(imports.selected((output/'mod.acp').read_bytes(),'TEST.RPK')['required'],['NEW.RPK'])
        changed=read_acp(output/'mod.acp')[1][0]['resource']
        self.assertEqual(references(changed)[0]['id'],'0000000000000003')
        self.assertEqual(changed.fields[0x471c6d].mapping()[2].raw(),b'opaque sibling')
        self.assertEqual(len(projects.inspect_project(output)['packs']),1)
        self.assertIsNone(linking.import_update(output/'mod.acp',output/'new.rpk','TEST.RPK'))
        altered=copy.deepcopy(plan);altered['imports']['after'].append('extra.rpk')
        with self.assertRaises(FormatError):linking.apply_plan(altered,self.root/'bad-link')

    def test_atomic_array_and_empty_reference_initialization(self):
        template=self.root/'template.rpk'
        template.write_bytes(named_pack([obj(10,props={1:mapping({0x1e:array([link(2)])})}),obj(2)],'TEMPLATE.RPK'))
        self.source.write_bytes(acp(obj(1,props={1:bytes.fromhex('011e0001010000')})))
        plan=reference_arrays.make_plan(self.source,'1','0x471c6d/1/1e',template,'a','0x471c6d/1/1e/1/0',
            '3',None,self.target,template,source_root=self.mod)
        reference_arrays.apply_plan(plan,self.root/'array')
        self.assertEqual(imports.selected((self.root/'array/mod.acp').read_bytes(),'TEST.RPK')['required'],['NEW.RPK'])
        self.source.write_bytes(acp(obj(1,props={1:b'\0'})))
        template.write_bytes(named_pack([obj(10,props={1:link(2)}),obj(2)],'TEMPLATE.RPK'))
        plan=reference_initialization.make_plan(self.source,'1','0x471c6d/1',template,'a','3',None,
            self.target,template,source_root=self.mod)
        reference_initialization.apply_plan(plan,self.root/'initialized')
        self.assertEqual(imports.selected((self.root/'initialized/mod.acp').read_bytes(),'TEST.RPK')['required'],['NEW.RPK'])

    def test_atomic_import_failure_leaves_no_output(self):
        plan=linking.make_plan(self.source,'1','0x471c6d/1','3',None,self.target,self.original,source_root=self.mod)
        altered=copy.deepcopy(plan['imports']);altered['before']=['false.rpk']
        with self.assertRaisesRegex(FormatError,'ACP imports changed'):
            editing.apply_project_plans([plan['edit']],self.mod,self.root/'failed',{'mod.acp':altered})
        self.assertFalse((self.root/'failed').exists())


if __name__=='__main__':unittest.main()
