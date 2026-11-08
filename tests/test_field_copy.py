"""Synthetic authoring invariants; no proprietary files or live mutations."""
import copy
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from aow4mod import field_copy, resource_diff, editing, native_roundtrip, cli
from aow4mod.core import FormatError, Node, read_rpk
from aow4mod.filesystem import inventory, file_hash
from test_aow4_mod_audit import resource, rpk, acp, mapping, typed, array, u32, string


def obj(rid, value, *, extra=b'opaque-neighbor', fields=None, kind=0x471C71):
    return typed(kind, {0x18: struct.pack('<Q', rid), 0x15: string(f'Object {rid}'),
                        0x471C6D: mapping(fields if fields is not None else {1: value, 2: extra})})


def node(raw):
    return Node(raw, 0, len(raw))


class FieldCopyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.mod = self.root / 'mod'
        self.mod.mkdir()
        self.target = self.mod / 'target.rpk'
        self.target.write_bytes(rpk([obj(1, u32(9)), obj(2, u32(8))]))
        self.templates = self.root / 'templates'
        self.templates.mkdir()
        self.template = self.templates / 'original.rpk'
        self.template.write_bytes(rpk([obj(100, u32(3))]))
        (self.mod / 'keep.bin').write_bytes(b'not a resource')

    def request(self, rid='1', prop='0x471c6d/1', **kwargs):
        return {'file': 'target.rpk', 'id': rid, 'property': prop,
                'template': {'file': str(self.template), 'id': '64'}, **kwargs}

    def plan(self, *rows):
        return field_copy.make_plan(self.mod, {'format': field_copy.SPEC, 'operations': list(rows or [self.request()])}, allow_opaque=True)

    def test_batch_changes_two_resources_and_preserves_files_identity_and_neighbors(self):
        before = inventory(self.mod)
        p = self.plan(self.request(), self.request('2'))
        out = self.root / 'stage'
        receipt = field_copy.apply_plan(p, out)
        self.assertEqual(inventory(self.mod), before)
        self.assertEqual(receipt['summary']['changed_operations'], 2)
        actual = read_rpk(out / 'target.rpk')[1]
        self.assertEqual([r.rid for r in actual], [1, 2])
        for r in actual:
            self.assertEqual(r.fields[0x471C6D].mapping()[1].uint(4), 3)
            self.assertEqual(r.fields[0x471C6D].mapping()[2].raw(), b'opaque-neighbor')
        self.assertEqual((out / 'keep.bin').read_bytes(), b'not a resource')

    def test_multiple_files_include_existing_acp_override(self):
        (self.mod / 'mod.acp').write_bytes(acp(obj(1, u32(5))))
        row = {**self.request(), 'file': 'mod.acp'}
        p = self.plan(self.request(), row)
        field_copy.apply_plan(p, self.root / 'stage')
        self.assertEqual(p['summary']['files'], 2)

    def test_absence_is_explicit_and_is_not_zero(self):
        self.template.write_bytes(rpk([obj(100, None, fields={2: b'keep'})]))
        with self.assertRaisesRegex(FormatError, 'absent'):
            self.plan()
        p = self.plan(self.request(missing='remove'))
        field_copy.apply_plan(p, self.root / 'removed')
        r = read_rpk(self.root / 'removed/target.rpk')[1][0]
        self.assertNotIn(1, r.fields[0x471C6D].mapping())
        self.assertEqual(p['operations'][0]['value_source'], 'template_omission')
        self.template.write_bytes(rpk([obj(100, b'\0')]))
        p = self.plan()
        self.assertEqual(p['operations'][0]['replacement_hex'], '00')

    def test_missing_destination_leaf_can_be_added_but_missing_parent_is_refused(self):
        self.target.write_bytes(rpk([obj(1, None, fields={2: b'keep'})]))
        p = self.plan()
        self.assertEqual(p['operations'][0]['action'], 'add')
        with self.assertRaises(FormatError):
            self.plan(self.request(prop='0x471c6e/1'))

    def test_components_can_have_different_array_positions(self):
        def comp(v):
            return typed(0x473A2C, {0x13: b'\x25', 0x473A2C: mapping({0x12: u32(v), 0x13: b'preserve'})})
        def nest(rid, entries):
            return typed(0x471425, {0x18: struct.pack('<Q', rid), 7: array(entries)})
        self.target.write_bytes(rpk([nest(1, [comp(9)])]))
        self.template.write_bytes(rpk([nest(100, [typed(0x473A2A, {0x13: b'\x22'}), comp(3)])]))
        row = self.request(prop='meta/7/1/0/473a2c/12')
        row['template']['property'] = 'meta/7/1/1/473a2c/12'
        p = self.plan(row)
        field_copy.apply_plan(p, self.root / 'stage')
        r = read_rpk(self.root / 'stage/target.rpk')[1][0]
        self.assertEqual(resource_diff.select(r, row['property']).uint(4), 3)
        self.assertEqual(resource_diff.select(r, 'meta/7/1/0/473a2c/13').raw(), b'preserve')

    def test_identity_type_field_and_array_element_guards(self):
        for prop in ['meta/18', 'meta/15', 'meta/14', 'meta/12c', '0x471c6d']:
            with self.subTest(prop=prop), self.assertRaises(FormatError):
                self.plan(self.request(prop=prop))
        self.template.write_bytes(rpk([obj(100, u32(3), kind=0x473702)]))
        with self.assertRaisesRegex(FormatError, 'same resource type'):
            self.plan()
        self.template.write_bytes(rpk([obj(100, u32(3))]))
        row = self.request()
        row['template']['property'] = '0x471c6d/2'
        with self.assertRaises(FormatError):
            self.plan(row)
        self.target.write_bytes(rpk([obj(1, array([u32(2)]))]))
        self.template.write_bytes(rpk([obj(100, array([u32(3)]))]))
        for prop in ['0x471c6d/1/1', '0x471c6d/1/1/0']:
            with self.assertRaisesRegex(FormatError, 'array|Array'):
                self.plan(self.request(prop=prop))

    def test_overlap_and_duplicate_paths_are_rejected(self):
        with self.assertRaisesRegex(FormatError, 'Overlapping'):
            self.plan(self.request(), self.request(prop='00471C6D/01'))
        self.target.write_bytes(rpk([obj(1, mapping({7: u32(8)}))]))
        self.template.write_bytes(rpk([obj(100, mapping({7: u32(3)}))]))
        with self.assertRaisesRegex(FormatError, 'Overlapping'):
            self.plan(self.request(), self.request(prop='0x471c6d/1/7'))

    def test_changed_source_template_and_backend_refuse_output(self):
        p = self.plan()
        cases = []
        changed = copy.deepcopy(p);changed['backend_sha256']['core.py'] = 'fake';cases.append(changed)
        changed = copy.deepcopy(p);changed['operations'][0]['replacement_hex'] = '04000000';cases.append(changed)
        changed = copy.deepcopy(p);changed['output_sha256']['target.rpk'] = 'fake';cases.append(changed)
        for i, changed in enumerate(cases):
            out = self.root / f'bad{i}'
            with self.assertRaises(FormatError):field_copy.apply_plan(changed, out)
            self.assertFalse(out.exists())
        self.template.write_bytes(rpk([obj(100, u32(7))]))
        with self.assertRaisesRegex(FormatError, 'template changed'):field_copy.apply_plan(p, self.root / 'stale')
        self.template.write_bytes(rpk([obj(100, u32(3))]))
        (self.mod / 'keep.bin').write_bytes(b'changed')
        with self.assertRaisesRegex(FormatError, 'Source tree changed'):field_copy.apply_plan(p, self.root / 'changed')

    def test_failure_after_copy_leaves_no_partial_output(self):
        p = self.plan()
        original = field_copy.validate
        calls = 0
        def fail_final(plan):
            nonlocal calls
            calls += 1
            if calls == 2:raise FormatError('late validation failed')
            return original(plan)
        with patch.object(field_copy, 'validate', side_effect=fail_final), self.assertRaises(FormatError):
            field_copy.apply_plan(p, self.root / 'partial')
        self.assertFalse((self.root / 'partial').exists())

    def test_path_escape_existing_outputs_and_missing_acknowledgement(self):
        for path in ['../templates/original.rpk', str(self.template)]:
            with self.assertRaisesRegex(FormatError, 'outside'):
                self.plan({**self.request(), 'file': path})
        p = self.plan()
        for out in [self.mod / 'nested', self.templates / 'nested', self.mod]:
            with self.assertRaises(FormatError):field_copy.apply_plan(p, out)
        with self.assertRaisesRegex(FormatError, 'allow-opaque'):
            field_copy.make_plan(self.mod, {'format': field_copy.SPEC, 'operations': [self.request()]})

    def test_noop_preserves_pack_bytes_and_omission(self):
        self.template.write_bytes(rpk([obj(100, u32(9))]))
        p = self.plan()
        field_copy.apply_plan(p, self.root / 'noop')
        self.assertEqual(p['summary']['changed_operations'], 0)
        self.assertEqual(self.target.read_bytes(), (self.root / 'noop/target.rpk').read_bytes())

    def witness(self, p, *, mutate=False, unrelated=False):
        staged = self.root / 'raw-stage'
        field_copy.apply_plan(p, staged)
        source = staged / 'target.rpk'
        saved_dir = self.root / 'native'
        saved_dir.mkdir()
        saved = saved_dir / 'target.rpk'
        def transform(pack, n):
            r = editing.Resource.parse(n)
            if r.rid != 1:return n.raw()
            f = r.fields[0x471C6D].mapping()
            selected = mapping({2: u32(5), 1: u32(100 if mutate else 4)})
            changes = {1: selected}
            if unrelated:changes[2] = mapping({2: u32(2), 1: u32(1)})
            return editing.rewrite_typed(n, {0x471C6D: editing.rewrite_map(r.fields[0x471C6D], changes)})
        saved.write_bytes(editing.transform_records(source.read_bytes(), '.rpk', transform))
        report = {'format': 'aow4mod-native-roundtrip/1', 'source': str(source), 'output_file': str(saved),
                  'comparison': native_roundtrip.compare(source, saved), 'binding': {'synthetic': True},
                  'diagnostics': ['synthetic retained diagnostic']}
        path = saved_dir / 'roundtrip.json'
        path.write_text(json.dumps(report), encoding='utf-8')
        return path

    def test_canonicalization_adopts_selected_encoding_only_and_retains_witness(self):
        self.template.write_bytes(rpk([obj(100, mapping({1: u32(4), 2: u32(5)}))]))
        p = self.plan()
        witness = self.witness(p)
        result = field_copy.canonicalize_plan(p, [witness])
        field_copy.apply_plan(result, self.root / 'canonical')
        comparison = native_roundtrip.compare(self.root / 'canonical/target.rpk', self.root / 'native/target.rpk')
        self.assertTrue(comparison['all_serialized_data_equal'])
        self.assertEqual(file_hash(witness), result['canonicalization'][0]['report_sha256'])
        data = json.loads(witness.read_text());data['diagnostics'] = []
        witness.write_text(json.dumps(data))
        with self.assertRaisesRegex(FormatError, 'witness changed'):field_copy.validate(result)

    def test_canonicalization_refuses_value_loss(self):
        self.template.write_bytes(rpk([obj(100, mapping({1: u32(4), 2: u32(5)}))]))
        p = self.plan()
        with self.assertRaisesRegex(FormatError, 'changes values'):
            field_copy.canonicalize_plan(p, [self.witness(p, mutate=True)])

    def test_canonicalization_refuses_unselected_normalization(self):
        extra = mapping({1: u32(1), 2: u32(2)})
        self.target.write_bytes(rpk([obj(1, u32(9), extra=extra)]))
        self.template.write_bytes(rpk([obj(100, mapping({1: u32(4), 2: u32(5)}))]))
        p = self.plan()
        with self.assertRaisesRegex(FormatError, 'outside selected'):
            field_copy.canonicalize_plan(p, [self.witness(p, unrelated=True)])


class ResourceDiffTests(unittest.TestCase):
    def test_selected_ancestor_encoding_allows_header_changes_but_not_other_fields(self):
        a = node(mapping({9: b'keep', 1: mapping({2: u32(3), 4: b'fixed'})}))
        b = node(mapping({1: mapping({4: b'fixed', 2: u32(3)}), 9: b'keep'}))
        self.assertTrue(field_copy.selected_encoding_only(a, b, [[1, 2]]))
        changed = node(mapping({1: mapping({4: b'changed', 2: u32(3)}), 9: b'keep'}))
        self.assertFalse(field_copy.selected_encoding_only(a, changed, [[1, 2]]))

    def test_native_roundtrip_cli_forwards_explicit_language_context(self):
        with patch('aow4mod.cli.find_game', return_value=Path('fixture-game')), \
             patch('aow4mod.cli.native_roundtrip.run', return_value={'status': 'equivalent_data', 'diagnostics': []}) as run, \
             redirect_stdout(io.StringIO()):
            code = cli.main(['native-roundtrip', 'fixture.rpk', '--output', 'readback', '--language', 'one.mo', '--language', 'two.mo'])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs['languages'], [Path('one.mo'), Path('two.mo')])

    def test_map_encoding_changes_are_distinct_from_data_and_array_order(self):
        a = node(mapping({1: u32(4), 2: b'opaque'}))
        b = node(mapping({2: b'opaque', 1: u32(4)}))
        result = resource_diff.compare_nodes(a, b)
        self.assertFalse(result['byte_equal'])
        self.assertTrue(result['recognized_data_equal'])
        self.assertEqual(result['differences'], [])
        self.assertFalse(resource_diff.compare_nodes(node(array([u32(4), u32(5)])), node(array([u32(5), u32(4)])))['recognized_data_equal'])

    def test_types_absence_unknown_and_depth_are_not_normalized(self):
        pairs = [(None, node(b'\0')), (node(b'opaque'), node(b'changed')),
                 (node(typed(0x471111, {1: u32(4)})), node(typed(0x471112, {1: u32(4)})))]
        for a, b in pairs:self.assertFalse(resource_diff.compare_nodes(a, b)['recognized_data_equal'])
        a = node(mapping({1: mapping({2: u32(4)})}))
        b = node(mapping({1: mapping({2: u32(5)})}))
        result = resource_diff.compare_nodes(a, b, max_depth=0)
        self.assertFalse(result['traversal_complete'])
        self.assertFalse(result['recognized_data_equal'])

    def test_false_byte_is_not_reinterpreted_as_an_empty_map(self):
        result = resource_diff.compare_nodes(node(b'\0'), node(mapping({})))
        self.assertFalse(result['recognized_data_equal'])
        self.assertEqual(len(result['differences']), 1)

    def test_direct_cli_show_and_diff_need_no_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / 'a.rpk', root / 'b.rpk'
            a.write_bytes(rpk([resource(value=u32(4))]))
            b.write_bytes(rpk([resource(value=u32(5))]))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()), patch('aow4mod.cli.find_game', side_effect=FormatError('not installed')):
                status = cli.main(['show', '1', '--file', str(a), '--index', str(root / 'absent.sqlite')])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(out.getvalue())['resource']['id'], '0000000000000001')
            result = resource_diff.inspect(a, b, '1', property_path='0x471c6d/1', limit=1)
            self.assertEqual(result['total'], 1)
            self.assertFalse(result['recognized_data_equal'])
            self.assertFalse((root / 'absent.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
