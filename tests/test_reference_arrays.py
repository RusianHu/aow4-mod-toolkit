import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import reference_arrays, linking, structured, editing
from aow4mod.core import FormatError, read_rpk, references
from test_aow4_mod_audit import array, mapping
from test_linking import obj, link
from test_pack_imports import named_pack


class ReferenceArrayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve(); self.mod = self.root/'mod'; self.mod.mkdir()
        self.source = self.mod/'own.rpk'
        # Actual native default Effects layout: map and typed interpretations both parse.
        self.source.write_bytes(named_pack([obj(1, props={1:bytes.fromhex('011e0001010000')})], 'OWN.RPK'))
        self.template = self.root/'template.rpk'
        self.template.write_bytes(named_pack([obj(10, props={1:mapping({0x1e:array([link(2)])})}), obj(2)], 'BASE.RPK'))
        self.target = self.root/'target.rpk'; self.target.write_bytes(named_pack([obj(3),obj(4,0x471c72)], 'TARGET.RPK'))

    def plan(self, **kwargs):
        return reference_arrays.make_plan(self.source, '1', '0x471c6d/1/1e', self.template, 'a',
            '0x471c6d/1/1e/1/0', kwargs.pop('target_id','3'), None, self.target, self.template,
            source_root=self.mod, **kwargs)

    def test_append_retargets_and_adds_import_atomically(self):
        original = self.source.read_bytes(); plan = self.plan(); output = self.root/'staged'
        receipt = reference_arrays.apply_plan(plan, output)
        info, resources = read_rpk(output/'own.rpk')
        self.assertEqual(info['dependencies'], ['TARGET.RPK'])
        self.assertEqual([x['id'] for x in references(resources[0])], ['0000000000000003'])
        self.assertEqual(receipt['imports']['added'], 'TARGET.RPK')
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(len(structured.find_node(resources[0].fields[0x471c6d], [1,0x1e]).array()), 1)

    def test_wrapped_reference_preserves_sibling(self):
        self.template.write_bytes(named_pack([obj(10,props={1:mapping({0x1e:array([
            mapping({7:link(2,True),8:b'opaque sibling'})])})}),obj(2)], 'BASE.RPK'))
        plan = self.plan(element_link_path=[7]); output = self.root/'wrapped'
        reference_arrays.apply_plan(plan, output)
        resource = read_rpk(output/'own.rpk')[1][0]
        item = structured.find_node(resource.fields[0x471c6d], [1,0x1e,1,0])
        self.assertEqual(item.mapping()[8].raw(), b'opaque sibling')
        self.assertEqual(references(resource)[0]['id'], '0000000000000003')

    def test_type_change_and_template_mutation_are_guarded(self):
        with self.assertRaisesRegex(FormatError,'type differs'): self.plan(target_id='4')
        self.assertTrue(self.plan(target_id='4',allow_type_change=True)['review_required'])
        plan = self.plan(); self.template.write_bytes(self.template.read_bytes()+b'changed')
        with self.assertRaises(FormatError): reference_arrays.apply_plan(plan, self.root/'stale')
        self.assertFalse((self.root/'stale').exists())

    def test_tampered_imports_and_failed_atomic_edit_leave_no_output(self):
        plan = self.plan(); changed = copy.deepcopy(plan); changed['imports']['after'].append('EXTRA.RPK')
        with self.assertRaises(FormatError): reference_arrays.apply_plan(changed,self.root/'tampered')
        self.assertFalse((self.root/'tampered').exists())
        with self.assertRaisesRegex(FormatError,'imports changed'):
            editing.apply_project_plans([plan['edit']], self.mod, self.root/'bad-import',
                {'own.rpk':{'before':['FALSE.RPK'],'after':['TARGET.RPK']}})
        self.assertFalse((self.root/'bad-import').exists())

    def test_direct_retarget_adds_import_and_existing_import_is_not_duplicated(self):
        self.source.write_bytes(named_pack([obj(1,props={1:link(2)})], 'OWN.RPK'))
        plan = linking.make_plan(self.source,'1','0x471c6d/1','3',None,
            self.target,self.template,source_root=self.mod)
        output = self.root/'linked'; linking.apply_plan(plan,output)
        self.assertEqual(read_rpk(output/'own.rpk')[0]['dependencies'],['TARGET.RPK'])
        self.assertIsNone(linking.import_update(output/'own.rpk',self.target))

    def test_pack_name_collisions_and_invalid_element_paths_are_refused(self):
        self.target.write_bytes(named_pack([obj(3)],'OWN.RPK'))
        with self.assertRaisesRegex(FormatError,'same pack name'):self.plan()
        self.target.write_bytes(named_pack([obj(3)],'TARGET.RPK'))
        for path in [[-1],[True],[2**32],{'7':1},[1]*65]:
            with self.subTest(path=path),self.assertRaises(FormatError):self.plan(element_link_path=path)


if __name__ == '__main__': unittest.main()
