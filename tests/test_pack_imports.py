import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import pack_imports, editing
from aow4mod.core import FormatError, Node, read_rpk
from test_aow4_mod_audit import rpk, resource, string


def named_pack(objects, name):
    data = rpk(objects)
    body = editing.rewrite_map(Node(data, 0xb0, len(data)), {0x16: string(name)})
    import struct
    header = bytearray(data[:0xb0]); struct.pack_into('<I', header, 12, len(body))
    return bytes(header)+body


class ImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve(); self.mod = self.root/'mod'; self.mod.mkdir()
        self.source = self.mod/'own.rpk'; self.source.write_bytes(named_pack([resource()], 'OWN.RPK'))
        (self.mod/'other.txt').write_bytes(b'unchanged')

    def test_add_remove_casefold_and_resource_preservation(self):
        original = self.source.read_bytes()
        plan = pack_imports.make_plan(self.source, ['OTHER.RPK', 'other.rpk', 'LIB.clb'])
        self.assertEqual(plan['after'], ['OTHER.RPK', 'LIB.clb'])
        output = self.root/'added'; pack_imports.apply_plan(plan, output)
        info, resources = read_rpk(output/'own.rpk')
        self.assertEqual(info['dependencies'], plan['after'])
        self.assertEqual(resources[0].node.raw(), read_rpk(self.source)[1][0].node.raw())
        self.assertEqual((output/'other.txt').read_bytes(), b'unchanged')
        self.assertEqual(self.source.read_bytes(), original)
        remove = pack_imports.make_plan(output/'own.rpk', remove=['other.rpk', 'LIB.clb'])
        pack_imports.apply_plan(remove, self.root/'removed')
        self.assertEqual(read_rpk(self.root/'removed/own.rpk')[0]['dependencies'], [])

    def test_invalid_imports_and_noops_are_rejected(self):
        for value in ['../a.rpk', 'C:\\a.rpk', '/a.rpk', 'x\ny.rpk', 'x\0.rpk', 'no.txt', '']:
            with self.subTest(value=value), self.assertRaises(FormatError):
                pack_imports.make_plan(self.source, [value])
        for kwargs in [dict(add=['own.rpk']), dict(add=['a.rpk'], remove=['A.RPK']),
                       dict(remove=['missing.rpk']), dict()]:
            with self.subTest(kwargs=kwargs), self.assertRaises(FormatError):
                pack_imports.make_plan(self.source, **kwargs)

    def test_tamper_changed_sibling_and_existing_output_are_rejected(self):
        plan = pack_imports.make_plan(self.source, ['OTHER.RPK'])
        changed = copy.deepcopy(plan); changed['after'].append('EXTRA.RPK')
        with self.assertRaises(FormatError): pack_imports.apply_plan(changed, self.root/'tampered')
        self.assertFalse((self.root/'tampered').exists())
        with self.assertRaises(FormatError): pack_imports.apply_plan(plan, self.mod)
        (self.mod/'other.txt').write_bytes(b'changed')
        with self.assertRaises(FormatError): pack_imports.apply_plan(plan, self.root/'stale')


if __name__ == '__main__': unittest.main()
