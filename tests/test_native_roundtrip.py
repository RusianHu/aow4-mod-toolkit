from pathlib import Path
import sys
import struct
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import native_roundtrip
from aow4mod.core import FormatError, Node
from test_aow4_mod_audit import rpk, resource, mapping, typed


class NativeRoundtripTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.before, self.after = self.root/'input/before.rpk', self.root/'after.rpk'
        self.before.parent.mkdir()
        self.before.write_bytes(rpk([resource(rid=1), resource(rid=2)]))

    def compare(self, data):
        self.after.write_bytes(data)
        return native_roundtrip.compare(self.before, self.after)

    def test_identical_native_copy_is_verified(self):
        result = self.compare(self.before.read_bytes())
        self.assertTrue(result['all_serialized_data_equal'])
        self.assertTrue(result['byte_identical'])

    def test_resource_loss_is_not_hidden_by_successful_writer_return(self):
        result = self.compare(rpk([resource(rid=1)]))
        self.assertEqual(result['missing_ids'], ['0000000000000002'])
        self.assertFalse(result['all_serialized_data_equal'])

    def test_changed_field_and_unknown_bytes_are_reported(self):
        result = self.compare(rpk([resource(rid=1, value=b'opaque changed bytes'), resource(rid=2)]))
        self.assertEqual(result['changed_resources'][0]['changed_fields'], ['0x00471C6D/0x01'])
        self.assertFalse(result['resource_data_equal'])

    def test_reordered_resource_array_is_a_real_layout_change(self):
        result = self.compare(rpk([resource(rid=2), resource(rid=1)]))
        self.assertTrue(result['resource_data_equal'])
        self.assertFalse(result['category_metadata_and_resource_order_equal'])
        self.assertFalse(result['all_serialized_data_equal'])

    def test_map_encoding_order_is_not_resource_data_loss(self):
        original = resource(rid=1)
        kind, fields = Node(original, 0, len(original)).typed()
        reordered = typed(kind, {k: fields[k].raw() for k in reversed(list(fields))})
        result = self.compare(rpk([reordered, resource(rid=2)]))
        self.assertFalse(result['byte_identical'])
        self.assertTrue(result['all_serialized_data_equal'])

    def test_duplicate_ids_cannot_be_collapsed_away(self):
        self.after.write_bytes(rpk([resource(rid=1), resource(rid=1)]))
        with self.assertRaisesRegex(FormatError, 'Duplicate resource IDs'):
            native_roundtrip.compare(self.before, self.after)

    def test_shorter_offset_table_only_changes_derived_payload_length(self):
        data = resource(rid=1)
        kind, fields = Node(data, 0, len(data)).typed()
        prop = fields[0x471c6d].raw()
        payload = fields[0x18].raw()+prop+fields[0x15].raw()
        compact = (struct.pack('<I', kind)+b'\x82'+struct.pack('<I', 1)
                   +bytes([0x18, 0, 0x15, 8+len(prop)])+struct.pack('<II', 0x471c6d, 8)+payload)
        self.assertNotEqual(len(data), len(compact))
        result = self.compare(rpk([compact, resource(rid=2)]))
        self.assertFalse(result['header_equal'])
        self.assertTrue(result['header_metadata_equal'])
        self.assertTrue(result['all_serialized_data_equal'])

    def test_unknown_header_changes_require_review(self):
        data = bytearray(self.before.read_bytes())
        data[100] = 1
        result = self.compare(bytes(data))
        self.assertTrue(result['resource_data_equal'])
        self.assertFalse(result['header_equal'])
        self.assertFalse(result['all_serialized_data_equal'])

    def test_existing_output_is_refused_before_native_execution(self):
        destination = self.root/'existing'
        destination.mkdir()
        with patch('aow4mod.native.cached') as call:
            with self.assertRaisesRegex(FormatError, 'already exists'):
                native_roundtrip.run(self.before, self.root/'game', destination)
        call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
