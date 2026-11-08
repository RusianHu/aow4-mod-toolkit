from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from aow4mod import catalog, semantics
from aow4mod.core import FormatError, Resource, Node
from test_aow4_mod_audit import rpk, resource, typed, string
import struct
import io
import json
from contextlib import redirect_stdout,redirect_stderr


class CatalogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.game = self.root / "game"
        self.content = self.game / "Content"
        self.content.mkdir(parents=True)
        self.pack = self.content / "test.rpk"
        link = typed(0x40004A, {0x14: struct.pack("<Q", 2), 0x16: string("Other")})
        self.pack.write_bytes(rpk([resource(value=link, name="Some Spell"), resource(rid=2, name="Other")]))
        self.index = self.root / "index.sqlite"

    def test_search_pagination_and_reverse_references(self):
        result = catalog.build(self.index, self.game, with_links=True)
        self.assertEqual(result["summary"]["resources"], 2)
        self.assertEqual(catalog.search(self.index, "SPELL")["total"], 1)
        first = catalog.search(self.index, limit=1)
        self.assertEqual(first["next_offset"], 1)
        refs = catalog.reverse_references(self.index, "2")
        self.assertEqual(refs["total"], 1)
        self.assertEqual(refs["items"][0]["name"], "Some Spell")

    def test_incremental_update_and_deleted_files(self):
        catalog.build(self.index, self.game)
        self.assertEqual(catalog.build(self.index, self.game)["summary"]["unchanged_files"], 1)
        self.pack.write_bytes(rpk([resource(name="Changed")]))
        result = catalog.build(self.index, self.game)
        self.assertEqual(result["summary"]["updated_files"], 1)
        self.assertEqual(catalog.search(self.index, "Changed")["total"], 1)
        another = self.content / "another.rpk"
        another.write_bytes(rpk([resource(rid=3)]))
        catalog.build(self.index, self.game)
        another.unlink()
        self.assertEqual(catalog.build(self.index, self.game)["summary"]["removed_files"], 1)

    def test_stale_index_cannot_return_old_data(self):
        catalog.build(self.index, self.game)
        self.pack.write_bytes(rpk([resource(name="Changed")]))
        with self.assertRaisesRegex(FormatError, "stale"):
            catalog.load_resource(self.index, "1")

    def test_ambiguous_identity_requires_file_selection(self):
        other = self.content / "other.rpk"
        other.write_bytes(rpk([resource()]))
        catalog.build(self.index, self.game)
        with self.assertRaisesRegex(FormatError, "disambiguate"):
            catalog.load_resource(self.index, "1")
        r, row, _ = catalog.load_resource(self.index, "1", self.pack)
        self.assertEqual(r.name, "Some Spell")

    def test_unindexed_links_are_not_reported_as_none(self):
        catalog.build(self.index, self.game)
        with self.assertRaisesRegex(FormatError, "not indexed"):
            catalog.reverse_references(self.index, "2")

    def test_refuse_unrelated_database_and_input_location(self):
        with sqlite3.connect(self.index) as db:
            db.execute("CREATE TABLE user_notes(text TEXT)")
            db.execute("INSERT INTO user_notes VALUES('keep')")
        with self.assertRaises(FormatError):
            catalog.build(self.index, self.game)
        with sqlite3.connect(self.index) as db:
            self.assertEqual(db.execute("SELECT text FROM user_notes").fetchone()[0], "keep")
        with self.assertRaises(FormatError):
            catalog.build(self.content / "index.sqlite", self.game)

    def test_unknown_build_does_not_infer_default(self):
        r = Resource.parse(Node(resource(value=None), 0, len(resource(value=None))))
        report = semantics.inspect_resource(r, self.game)
        self.assertFalse(report["schema_status"]["verified"])
        self.assertFalse(any("effective_value" in p for p in report["properties"]))

    def test_direct_cli_inspection_is_paginated_without_an_index(self):
        from aow4mod import cli
        self.pack.write_bytes(rpk([resource(rid=i,name=f'Entry {i}') for i in range(1,26)]))
        stream=io.StringIO()
        with redirect_stdout(stream):code=cli.main(['inspect',str(self.pack),'--limit','2','--offset','3'])
        result=json.loads(stream.getvalue())
        self.assertEqual(code,0);self.assertEqual(result['total'],25);self.assertEqual(result['next_offset'],5)
        self.assertEqual(len(result['resources']),2);self.assertEqual(result['resources'][0]['id'],'0000000000000004')
        self.assertFalse(self.index.exists())

    def test_malformed_plan_produces_a_compact_machine_readable_error(self):
        from aow4mod import cli
        path=self.root/'bad-plan.json';path.write_text('{"format":"aow4mod-resource-plan/1"}', encoding='utf-8')
        stream=io.StringIO()
        with redirect_stderr(stream):code=cli.main(['resource-apply',str(path),'--output',str(self.root/'stage')])
        self.assertEqual(code,2);self.assertEqual(json.loads(stream.getvalue())['error_type'],'KeyError')
        self.assertFalse((self.root/'stage').exists())


if __name__ == "__main__":
    unittest.main()
