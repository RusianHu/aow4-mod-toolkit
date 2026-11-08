from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import content_libraries as clb
from aow4mod.core import FormatError
from test_aow4_mod_audit import mapping, typed, array, u32


def library_bytes(value=1, pixel=b'pixels', duplicate=False):
    strings = bytearray()
    def text(value):
        raw = value.encode('utf-8');offset = len(strings);strings.extend(raw+b'\0')
        return struct.pack('<II', len(raw), offset)
    name = text('FIXTURE')
    locator = text('[FIXTURE]MESH\\0')
    label = text('测试 member')
    member = typed(0x410035, {0x14: locator, 0x15: label, 0x410035: mapping({1: u32(value)})})
    metadata = mapping({0x13: u32(8), 0x14: name, 0x15: array([member, member] if duplicate else [member]),
                        0x18: array([]), 0x1c: array([])})
    align = lambda x: bytes(x)+bytes((-len(x)) % 16)
    strings, metadata, pixel = map(align, [strings, metadata, pixel])
    header = clb.SIGNATURE+struct.pack('<IIII', 8, len(strings), len(metadata), len(pixel))+bytes(8)
    return header+strings+metadata+pixel


class ContentLibraryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve();self.path = self.root/'fixture.clb'
        self.path.write_bytes(library_bytes())

    def test_member_identity_and_unicode_string_table(self):
        result = clb.inspect(self.path)
        self.assertEqual(result['name'], 'FIXTURE')
        self.assertEqual(result['total_members'], 1)
        self.assertEqual(result['items'][0]['name'], '测试 member')
        self.assertEqual(clb.member(self.path, '[fixture]mesh\\0')['status'], 'found_top_level_member')
        self.assertEqual(clb.member(self.path, '[FIXTURE]MESH\\99')['status'], 'not_found_in_top_level_members')

    def test_header_bounds_version_reserved_and_truncation(self):
        original = self.path.read_bytes()
        cases = [original[:16], original[:-1]]
        for offset, value in [(0, 1), (8, 9), (24, 1), (12, 1)]:
            data = bytearray(original);data[offset] = value;cases.append(bytes(data))
        for data in cases:
            self.path.write_bytes(data)
            with self.assertRaises(FormatError):clb.read(self.path)

    def test_invalid_string_reference_and_duplicate_locator_are_refused(self):
        data = bytearray(self.path.read_bytes());data[32+len('FIXTURE')] = 65
        self.path.write_bytes(data)
        with self.assertRaises(FormatError):clb.read(self.path)
        self.path.write_bytes(library_bytes(duplicate=True))
        with self.assertRaises(FormatError):clb.read(self.path)

    def test_metadata_and_opaque_pixel_changes_are_reported_separately(self):
        other = self.root/'other.clb'
        other.write_bytes(library_bytes(value=2))
        result = clb.compare(self.path, other)
        self.assertTrue(result['opaque_data_equal'])
        self.assertEqual(result['changed_members'][0]['changed_fields'], ['0x00410035/0x1'])
        other.write_bytes(library_bytes(pixel=b'changed pixels'))
        result = clb.compare(self.path, other)
        self.assertFalse(result['opaque_data_equal'])
        self.assertEqual(result['changed_members'], [])

    def test_exact_copy_is_verified_and_queries_are_paged(self):
        other = self.root/'other.clb';other.write_bytes(self.path.read_bytes())
        self.assertTrue(clb.compare(self.path, other)['all_compared_data_equal'])
        self.assertEqual(clb.inspect(self.path, '测试', limit=1)['total'], 1)
        self.assertEqual(clb.inspect(self.path, category='TXA')['total'], 0)


if __name__ == '__main__':unittest.main()
