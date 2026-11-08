"""Synthetic fixtures only: no proprietary game assets in tests."""
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/aow4_mod_audit.py"
spec = importlib.util.spec_from_file_location("aow4_audit", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def u32(x):
    return struct.pack("<I", x)


def string(x):
    b = x.encode("utf-8")
    return u32(len(b)) + b


def mapping(fields):
    # Fixture encoder always uses the extended layout; compact decoding is
    # independently tested with hand-written bytes below.
    payload = b""
    entries = []
    for key, value in fields.items():
        entries.append(struct.pack("<II", key, len(payload)))
        payload += value
    return b"\x80" + u32(len(entries)) + b"".join(entries) + payload


def typed(kind, fields):
    return u32(kind) + mapping(fields)


def array(items):
    return mapping({1: mapping(dict(enumerate(items)))})


def resource(rid=1, value=b"\0\0\0\0", name="Example", kind=0x471C71):
    fields = {0x18: struct.pack("<Q", rid),
              0x471C6D: mapping({1: value} if value is not None else {})}
    if name is not None:
        fields[0x15] = string(name)
    return typed(kind, fields)


def acp(obj, operation=0x40):
    change = typed(0x401206, {0x401206: mapping({1: obj, 2: u32(operation),
                                               3: string("[#0000000000000001]0\\0")})})
    pack = typed(0x401204, {0x401204: mapping({1: string("TEST.RPK"), 2: array([change])})})
    fields = {0x15: string("Fixture"), 0x17: string("Test"), 0x18: u32(0),
              0x19: u32(1), 0x26: array([pack])}
    return b"CPR\0" + bytes(12) + b"\x01\x00\x00" + typed(0x40009D, {0x40009D: mapping(fields)})


def rpk(objects, container=0x400017F):
    body = mapping({13: mapping({0: typed(container, {0x32: u32(64),
                    0x33: mapping({0x15: array(objects)})})}), 0x16: string("TEST.RPK")})
    return b"RPK\0\x1a\0\0\x04" + bytes(4) + u32(len(body)) + bytes(160) + body


class ParserTests(unittest.TestCase):
    def node(self, b):
        return mod.Node(b, 0, len(b))

    def test_compact_offsets_are_relative_to_end_of_table(self):
        fields = self.node(bytes.fromhex("02 03 00 07 04 01020304 0506")).mapping()
        self.assertEqual(fields[3].raw(), b"\1\2\3\4")
        self.assertEqual(fields[7].raw(), b"\5\6")

    def test_reject_corrupt_offset_tables(self):
        for raw in ("80", "80 ffffffff", "02 01 00 01 01 0000", "01 01 ff 00",
                    "01 01 01 0000", "00 112233"):
            with self.subTest(raw=raw), self.assertRaises(mod.FormatError):
                self.node(bytes.fromhex(raw)).mapping()

    def test_strings_require_exact_bounds_and_valid_encoding(self):
        self.assertEqual(self.node(string("中文")).string(), "中文")
        for raw in (u32(10) + b"abc", u32(1) + b"\xff", string("abc") + b"x"):
            with self.assertRaises(mod.FormatError):
                self.node(raw).string()

    def test_zero_and_absent_are_distinct_and_not_empty_text(self):
        a, b = mod.describe(bytes(4)), mod.describe(None)
        self.assertTrue(a["serialized"])
        self.assertNotIn("utf8", a)
        self.assertFalse(b["serialized"])
        self.assertEqual(len(mod.diff_fields({"field": bytes(4)}, {})), 1)

    def test_id_links_require_agreement(self):
        def link(rid):
            return mapping({0x14: string("[#0000000000000012]0\\1"),
                            0x16: struct.pack("<Q", rid), 0x17: string("Target")})
        self.assertEqual(mod.decode_link(self.node(link(0x12)))["id"], "0000000000000012")
        self.assertIsNone(mod.decode_link(self.node(link(0x13))))

    def test_supported_link_is_found_inside_nested_container(self):
        link = typed(0x40004A, {0x14: struct.pack("<Q", 42), 0x16: string("Linked")})
        r = mod.Resource.parse(self.node(resource(value=array([link]))))
        self.assertEqual([v["id"] for v in mod.references(r)], ["000000000000002A"])

    def test_unnamed_resource_retains_identity(self):
        r = mod.Resource.parse(self.node(resource(name=None)))
        self.assertIsNone(r.name)
        self.assertEqual(r.rid, 1)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.game = self.root / "game"
        (self.game / "Content").mkdir(parents=True)
        self.pack = self.game / "Content/test.rpk"
        self.pack.write_bytes(rpk([resource()]))
        self.project = self.root / "mod"
        self.project.mkdir()
        self.acp = self.project / "mod.acp"
        self.acp.write_bytes(acp(resource(value=None)))

    def test_audit_preserves_inputs_and_reports_actual_field_difference(self):
        before = (self.acp.read_bytes(), self.pack.read_bytes())
        report, baseline = mod.audit(self.acp, self.game)
        self.assertEqual(report["summary"]["id_and_type_matches"], 1)
        self.assertEqual(report["summary"]["field_differences"], 1)
        self.assertFalse(report["resources"][0]["differences"][0]["mod"]["serialized"])
        self.assertEqual(before, (self.acp.read_bytes(), self.pack.read_bytes()))
        followup, _ = mod.audit(self.acp, self.game, baseline)
        self.assertEqual(followup["resources"][0]["since_baseline"]["changes_in_original"], [])

    def minimal_acp(self, fields):
        return b"CPR\0" + bytes(12) + b"\x01\x00\x00" + typed(0x40009D, {0x40009D: mapping(fields)})

    def test_omitted_optional_metadata_is_reported_without_guessing(self):
        self.acp.write_bytes(self.minimal_acp({0x15: string("Minimal")}))
        metadata, changes = mod.read_acp(self.acp)
        self.assertEqual(changes, [])
        self.assertIsNone(metadata["version_major"])
        self.assertIsNone(metadata["author"])
        self.assertFalse(metadata["change_list_serialized"])

    def test_no_override_or_local_resource_cannot_pass_validation(self):
        self.acp.write_bytes(self.minimal_acp({0x15: string("Localization only")}))
        report, _ = mod.audit(self.acp, self.game)
        self.assertEqual(report["summary"]["conclusion"], "not_evaluated")
        self.assertIn("no_resource_objects_checked", [i["issue"] for i in report["issues"]])

    def test_additive_mod_checks_its_own_rpk(self):
        self.acp.write_bytes(self.minimal_acp({0x15: string("New content")}))
        (self.project / "added.rpk").write_bytes(rpk([resource(rid=99)]))
        report, _ = mod.audit(self.acp, self.game)
        self.assertEqual(report["summary"]["modified_resources"], 0)
        self.assertEqual(report["summary"]["local_rpk_resources_checked"], 1)
        self.assertNotEqual(report["summary"]["conclusion"], "not_evaluated")

    def test_new_original_change_is_flagged_against_baseline(self):
        _, baseline = mod.audit(self.acp, self.game)
        self.pack.write_bytes(rpk([resource(value=u32(2))]))
        report, _ = mod.audit(self.acp, self.game, baseline)
        self.assertIn("upstream_changes_overridden", [i["issue"] for i in report["issues"]])

    def test_baseline_rejects_different_mod(self):
        _, baseline = mod.audit(self.acp, self.game)
        self.acp.write_bytes(acp(resource(value=u32(7))))
        with self.assertRaises(mod.FormatError):
            mod.audit(self.acp, self.game, baseline)

    def test_missing_and_ambiguous_ids_do_not_pass(self):
        for objects, status in (([resource(rid=2)], "target_not_found_in_pack"),
                                ([resource(), resource()], "ambiguous_target")):
            self.pack.write_bytes(rpk(objects))
            report, _ = mod.audit(self.acp, self.game)
            self.assertEqual(report["resources"][0]["status"], status)
            self.assertGreater(report["summary"]["issues"], 0)

    def test_type_change_and_unsupported_operation_are_flagged(self):
        self.acp.write_bytes(acp(resource(kind=0x473702), operation=123))
        report, _ = mod.audit(self.acp, self.game)
        self.assertEqual(report["resources"][0]["status"], "type_changed")
        self.assertIn("unverified_change_operation", [i["issue"] for i in report["issues"]])

    def test_unknown_pack_does_not_silently_disappear(self):
        (self.game / "Content/unknown.rpk").write_bytes(b"not an rpk")
        report, _ = mod.audit(self.acp, self.game)
        self.assertEqual(len(report["scan"]["failures"]), 1)
        self.assertIn("incomplete_pack_index", [i["issue"] for i in report["issues"]])

    def test_rpk_length_mismatch_is_rejected(self):
        self.pack.write_bytes(rpk([resource()]) + b"tail")
        with self.assertRaises(mod.FormatError):
            mod.read_rpk(self.pack)

    def test_outputs_cannot_overwrite_inputs_or_baseline(self):
        for target in (self.project / "bad.json", self.game / "bad.json", self.root / "file.acp"):
            with self.assertRaises(mod.FormatError):
                mod.write_json(target, {}, [self.project, self.game])
        output = self.root / "report.json"
        mod.write_json(output, {"first": True}, [self.project, self.game])
        with self.assertRaises(FileExistsError):
            mod.write_json(output, {}, [self.project, self.game])
        self.assertEqual(json.loads(output.read_text(encoding='utf-8')), {"first": True})


if __name__ == "__main__":
    unittest.main()
