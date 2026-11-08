import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import catalog, native, native_objects
from aow4mod.core import FormatError, sha
from test_aow4_mod_audit import mapping, resource, rpk, string


class NativeObjectTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.profile = copy.deepcopy(next(iter(native.PROFILES.values())))
        self.binding = {'profile': self.profile}
        self.fields = [{'name': 'Count', 'category': '', 'codec': 'i32', 'editor_cid': '00401418', 'editor_parameter': 0},
                       {'name': 'Enabled', 'category': '', 'codec': 'bool', 'editor_cid': '00401607', 'editor_parameter': 1},
                       {'name': 'Target', 'category': '', 'codec': None, 'editor_cid': '0040008B', 'editor_parameter': 0}]

    def test_scalar_assignment_is_typed_and_float32_is_explicit(self):
        values = native_objects.normalize_assignments({'Count': 7, 'Enabled': False}, self.fields, self.binding)
        self.assertEqual([v['value'] for v in values], [7, False])
        self.assertEqual(native_objects.normalized_value('f32', 1.1), struct.unpack('<f', struct.pack('<f', 1.1))[0])
        for codec, value in [('i32', True), ('i32', 2**31), ('bool', 1), ('f32', float('nan')),
                             ('f32', 1e100), ('string', 'a\0b'), ('string', 'a'*65537)]:
            with self.assertRaises(FormatError):
                native_objects.normalized_value(codec, value)

    def test_object_reference_is_not_written_as_an_integer(self):
        with self.assertRaisesRegex(FormatError, 'object/resource/list'):
            native_objects.normalize_assignments({'Target': 123}, self.fields, self.binding)

    def test_unknown_ambiguous_and_duplicate_property_names_are_refused(self):
        for values, fields in [({'Unknown': 1}, self.fields), ({'Count': 1}, self.fields+[self.fields[0]]),
                               ([{'name': 'Count', 'value': 1}, {'name': 'Count', 'value': 2}], self.fields),
                               (0, self.fields), ('Count', self.fields)]:
            with self.assertRaises(FormatError):
                native_objects.normalize_assignments(values, fields, self.binding)

    def test_enum_assignment_requires_an_exact_current_enum_table(self):
        field = {'name': 'Mode', 'category': '', 'codec': 'enum_i32', 'editor_cid': '0040160D', 'editor_parameter': 123}
        with self.assertRaisesRegex(FormatError, 'matching offline schema'):
            native_objects.normalize_assignments({'Mode': 1}, [field], self.binding)
        enriched = [{**field, 'enum': {'values': [{'value': 1, 'label': 'Allowed'}]}}]
        with patch.object(native, 'enrich_enums', return_value=enriched):
            self.assertEqual(native_objects.normalize_assignments({'Mode': 1}, [field], self.binding, self.root/'schema')[0]['value'], 1)
            with self.assertRaises(FormatError):
                native_objects.normalize_assignments({'Mode': 2}, [field], self.binding, self.root/'schema')

    def test_package_default_omission_is_narrow_and_unknown_fields_remain_significant(self):
        before = mapping({0x40009d: mapping({0x18: struct.pack('<I', 1), 0x19: bytes(4), 0x99: b'opaque'})})
        after = mapping({0x40009d: mapping({0x99: b'opaque'})})
        self.assertFalse(native_objects.compare_bodies(before, after)['data_equal'])
        self.assertTrue(native_objects.compare_bodies(before, after, self.profile['package_omitted_fields'])['data_equal'])
        changed = mapping({0x40009d: mapping({0x99: b'changed'})})
        self.assertFalse(native_objects.compare_bodies(before, changed, self.profile['package_omitted_fields'])['data_equal'])

    def test_zero_guid_replacement_is_not_silently_normalized(self):
        a = mapping({0x40009d: mapping({0x2a: bytes(16)})})
        b = mapping({0x40009d: mapping({0x2a: b'X'*16})})
        self.assertFalse(native_objects.compare_bodies(a, b, self.profile['package_omitted_fields'])['data_equal'])

    def test_hotfix_package_defaults_preserve_nondefaults_identity_and_other_classes(self):
        profile=native.PROFILES['9f7ff162d817073eb04a1b514f8e51ec0233a7ce48c586f2433940130de0036e']
        rules=profile['package_omitted_fields']
        defaults={0x16:bytes(4),0x17:bytes(4),0x18:struct.pack('<I',1),0x19:bytes(4),0x1A:bytes(4)}
        identity={0x2A:b'G'*16,0x99:b'unknown'}
        def body(fields,cid=0x40009d):return mapping({cid:mapping(fields)})
        empty=body(identity)
        self.assertTrue(native_objects.compare_bodies(body({**defaults,**identity}),empty,rules)['data_equal'])
        # In particular, minor=0 is an actual distinct version, not the omitted 1.
        for key,value in {0x16:string('description'),0x17:string('author'),0x18:bytes(4),
                          0x19:struct.pack('<I',2),0x1A:b'nonempty list'}.items():
            with self.subTest(field=key):
                self.assertFalse(native_objects.compare_bodies(body({**identity,key:value}),empty,rules)['data_equal'])
        for changed in ({0x2A:b'H'*16,0x99:b'unknown'},{0x2A:b'G'*16,0x99:b'changed'}):
            self.assertFalse(native_objects.compare_bodies(body(changed),empty,rules)['data_equal'])
        self.assertFalse(native_objects.compare_bodies(body(defaults,0x471c71),body({},0x471c71),rules)['data_equal'])

    def test_native_resource_identity_requires_matching_nonzero_locator(self):
        good = {'id': 'ABCDEF1234567890', 'name': 'New', 'locator': '[#ABCDEF1234567890]0\\0'}
        native_objects.validate_identity(good)
        for change in [{'id': '0000000000000000'}, {'locator': '[#ABCDEF1234567891]0\\0'},
                       {'name': ''}, {'name': 'x\0y'}, {'locator': 4}]:
            with self.assertRaises(FormatError):
                native_objects.validate_identity({**good, **change})

    def test_constructor_role_requires_current_original_resource_evidence(self):
        game = self.root/'game'
        (game/'Content').mkdir(parents=True)
        source = game/'Content/original.rpk'
        source.write_bytes(rpk([resource()]))
        index = self.root/'index.sqlite'
        catalog.build(index, game)
        selected = {'cid': '00471C71', 'name': 'TStructureSingleTargeter'}
        evidence = native_objects.type_evidence(game, selected, index)
        self.assertEqual(evidence['role'], 'resource')
        with self.assertRaises(FormatError):
            native_objects.type_evidence(game, {'cid': '12345678', 'name': 'RuntimeObject'}, index)
        source.write_bytes(rpk([resource(name='changed')]))
        with self.assertRaises(FormatError):
            native_objects.type_evidence(game, selected, index)

    def make_result(self, before, after, assignments=None, readbacks=None, cid='00471C71'):
        a, b = self.root/'source.bin', self.root/'saved.bin'
        a.write_bytes(before);b.write_bytes(after)
        return {'binding': self.binding, 'params': {'assignments': assignments or []}, 'payload': {
            'cid': cid, 'source_file': str(a), 'source_sha256': sha(before), 'output_file': str(b),
            'output_sha256': sha(after), 'readbacks': readbacks or []}}

    def test_field_loss_is_detected_even_if_getter_reports_requested_value(self):
        before = mapping({0x471c6d: mapping({1: struct.pack('<I', 1)})})
        after = mapping({0x471c6d: mapping({})})
        result = self.make_result(before, after)
        with self.assertRaisesRegex(FormatError, 'did not preserve'):
            native_objects.verify_result(result)

    def test_wrong_readback_type_or_missing_readback_is_not_success(self):
        data = mapping({0x471c6d: mapping({})})
        assignments = [{'name': 'Enabled', 'category': '', 'value': False}]
        for readbacks in [[], [{'name': 'Enabled', 'category': '', 'default_status': 'read', 'getter_status': 0, 'default': 0}]]:
            with self.assertRaises(FormatError):
                native_objects.verify_result(self.make_result(data, data, assignments, readbacks))

    def test_package_version_checks_actual_native_members_not_only_serialized_bytes(self):
        data = mapping({0x40009d: mapping({})})
        result = self.make_result(data, data, cid='0040009D')
        result['payload']['package_version'] = {'minor': 0, 'major': 0}
        with self.assertRaisesRegex(FormatError, 'package version'):
            native_objects.verify_result(result)
        result['payload']['package_version'] = {'minor': 1, 'major': 0}
        native_objects.verify_result(result)


if __name__ == '__main__':
    unittest.main()
