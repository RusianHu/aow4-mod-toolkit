import ctypes
import os
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from aow4mod import pack_imports
from aow4mod.core import read_rpk
from test_aow4_mod_audit import rpk, resource


class WindowsAliasPathsTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows 8.3 alias paths')
    def test_short_path_input_produces_a_canonical_plan_and_preserves_source(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        source = root / 'Long Authoring Source Directory'; source.mkdir()
        path = source / 'fixture.rpk'; path.write_bytes(rpk([resource()]))
        before = path.read_bytes()
        api = ctypes.WinDLL('kernel32', use_last_error=True).GetShortPathNameW
        api.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        api.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        count = api(str(source), buffer, len(buffer))
        if not count or count >= len(buffer) or Path(buffer.value) == source:
            self.skipTest('This volume did not expose an alternate short path')
        alias = Path(buffer.value)
        plan = pack_imports.make_plan(alias / path.name, add=['MORE.RPK'], source_root=alias)
        self.assertEqual(Path(plan['source_root']), source)
        self.assertEqual(Path(plan['source']), path)
        output = root / 'new stage'; pack_imports.apply_plan(plan, output)
        self.assertEqual(read_rpk(output / path.name)[0]['dependencies'], ['MORE.RPK'])
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__': unittest.main()
