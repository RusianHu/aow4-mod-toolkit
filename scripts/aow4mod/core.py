#!/usr/bin/env python3
"""Read-only, dependency-free inspection of observed AoW4 ACP/RPK formats.

This is a structural auditor, not a game serializer or a gameplay validator.
Unknown formats fail explicitly; absent serialized fields are NOT decoded as zero.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import uuid

from . import __version__ as VERSION
FORMAT = "aow4-mod-audit/1"
DEFAULT_GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Age of Wonders 4")
CLASS_LABELS = {0x471C71: "Structure single targeter", 0x473702: "Research skill"}


class FormatError(ValueError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Node:
    data: bytes
    start: int
    end: int

    def __post_init__(self):
        if not 0 <= self.start <= self.end <= len(self.data):
            raise FormatError("node outside input bounds")

    def raw(self) -> bytes:
        return self.data[self.start:self.end]

    def uint(self, size: int) -> int:
        if self.end - self.start != size:
            raise FormatError(f"expected {size}-byte integer at 0x{self.start:X}")
        return int.from_bytes(self.raw(), "little")

    def string(self) -> str:
        if self.end - self.start < 4:
            raise FormatError("truncated string length")
        n = struct.unpack_from("<I", self.data, self.start)[0]
        if n != self.end - self.start - 4:
            raise FormatError("string length does not consume field")
        try:
            return self.data[self.start + 4:self.end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FormatError("invalid UTF-8 string") from exc

    def mapping(self) -> dict[int, Node]:
        """Decode a bounded offset map; never scan for a plausible next boundary."""
        pos = self.start
        if pos == self.end:
            raise FormatError("empty map input")
        tag = self.data[pos]
        pos += 1
        small, large = tag & 0x7F, 0
        if tag & 0x80:
            if pos + 4 > self.end:
                raise FormatError("truncated extended map count")
            large = struct.unpack_from("<I", self.data, pos)[0]
            pos += 4
        if small + large > 1_000_000 or pos + small * 2 + large * 8 > self.end:
            raise FormatError("offset table exceeds field")
        pairs = []
        for _ in range(small):
            pairs.append(struct.unpack_from("<BB", self.data, pos))
            pos += 2
        for _ in range(large):
            pairs.append(struct.unpack_from("<II", self.data, pos))
            pos += 8
        if len({k for k, _ in pairs}) != len(pairs):
            raise FormatError("duplicate offset-map keys")
        pairs.sort(key=lambda kv: kv[1])
        if not pairs:
            if pos != self.end:
                raise FormatError("empty map has unconsumed bytes")
            return {}
        if pairs[0][1] != 0:
            raise FormatError("offset map does not begin at payload start")
        out = {}
        for i, (key, offset) in enumerate(pairs):
            start = pos + offset
            end = pos + pairs[i + 1][1] if i + 1 < len(pairs) else self.end
            if not pos <= start <= end <= self.end:
                raise FormatError("offset outside map payload")
            out[key] = Node(self.data, start, end)
        return out

    def typed(self) -> tuple[int, dict[int, Node]]:
        if self.end - self.start < 5:
            raise FormatError("truncated typed object")
        type_id = struct.unpack_from("<I", self.data, self.start)[0]
        return type_id, Node(self.data, self.start + 4, self.end).mapping()

    def array(self) -> dict[int, Node]:
        outer = self.mapping()
        if set(outer) != {1}:
            raise FormatError("unsupported array envelope")
        items = outer[1].mapping()
        if sorted(items) != list(range(len(items))):
            raise FormatError("non-contiguous array indices")
        return {key:items[key] for key in sorted(items)}


def required(mapping: dict, key: int, context: str) -> Node:
    if key not in mapping:
        raise FormatError(f"{context}: missing field 0x{key:X}")
    return mapping[key]


def string_list(node: Node) -> list[str]:
    if node.end - node.start < 4:
        raise FormatError("truncated string list")
    count = struct.unpack_from("<I", node.data, node.start)[0]
    pos, result = node.start + 4, []
    if count > (node.end-pos)//4:
        raise FormatError("string list count exceeds field")
    for _ in range(count):
        if pos + 4 > node.end:
            raise FormatError("truncated string list element")
        size = struct.unpack_from("<I", node.data, pos)[0]
        end = pos + 4 + size
        if end > node.end:
            raise FormatError("string list element exceeds field")
        result.append(Node(node.data, pos, end).string())
        pos = end
    if pos != node.end:
        raise FormatError("unconsumed string list bytes")
    return result


@dataclass
class Resource:
    type_id: int
    rid: int
    name: str | None
    node: Node
    fields: dict[int, Node]

    @classmethod
    def parse(cls, node: Node) -> Resource:
        type_id, fields = node.typed()
        # Empty/default names are omitted in two shipped deprecated PFX objects.
        name = fields[0x15].string() if 0x15 in fields else None
        rid = required(fields, 0x18, "resource").uint(8)
        return cls(type_id, rid, name, node, fields)

    def identity(self) -> dict:
        return {"id": f"{self.rid:016X}", "name": self.name,
                "type_id": f"0x{self.type_id:08X}",
                "type_hint": CLASS_LABELS.get(self.type_id),
                "offset": self.node.start, "size": self.node.end - self.node.start}

    def leaves(self) -> dict[str, bytes]:
        """Keep opaque property values, splitting only known class-field maps."""
        out = {}
        for key, value in self.fields.items():
            if key >= 0x10000:
                for field_id, leaf in value.mapping().items():
                    out[f"0x{key:08X}/0x{field_id:02X}"] = leaf.raw()
            else:
                out[f"meta/0x{key:02X}"] = value.raw()
        return out


def mod_file(path: Path) -> Path:
    if path.is_file():
        return path.resolve()
    preferred = path / (path.name + ".acp")
    if preferred.is_file():
        return preferred.resolve()
    candidates = list(path.glob("*.acp"))
    if len(candidates) != 1:
        raise FormatError(f"need one explicit ACP file; found {len(candidates)} in {path}")
    return candidates[0].resolve()


def read_acp(path: Path) -> tuple[dict, list[dict]]:
    data = path.read_bytes()
    if len(data) < 24 or data[:4] != b"CPR\0" or data[16:19] != b"\x01\x00\x00":
        raise FormatError("unsupported ACP header")
    type_id, outer = Node(data, 19, len(data)).typed()
    if type_id != 0x40009D or set(outer) != {type_id}:
        raise FormatError("unsupported ACP root class")
    fields = outer[type_id].mapping()
    if 0x25 in fields and fields[0x25].uint(4)!=(len(fields[0x26].raw()) if 0x26 in fields else 0):
        raise FormatError('ACP change-blob length does not match its serialized data')
    # These fields are omitted in real, valid Workshop packages. Preserve
    # absence instead of assigning undocumented defaults or rejecting the Mod.
    meta = {"path": str(path), "sha256": sha(data), "size": len(data),
            "name": fields[0x15].string() if 0x15 in fields else path.stem,
            "name_source": "acp" if 0x15 in fields else "filename",
            "author": fields[0x17].string() if 0x17 in fields else None,
            "version_major": fields[0x19].uint(4) if 0x19 in fields else None,
            "version_minor": fields[0x18].uint(4) if 0x18 in fields else None,
            "omitted_metadata_fields": [f"0x{k:02X}" for k in (0x15, 0x17, 0x18, 0x19) if k not in fields],
            "change_list_serialized": 0x26 in fields}
    meta["registered_resource_packs"] = string_list(fields[0x1A]) if 0x1A in fields else []
    meta["tags"] = string_list(fields[0x1C]) if 0x1C in fields else []
    if 0x2A in fields and len(fields[0x2A].raw()) == 16:
        meta["package_id"] = str(uuid.UUID(bytes_le=fields[0x2A].raw()))
    metadata_keys = (0x14,0x15,0x16,0x17,0x18,0x19,0x1C,0x1D,0x1E,0x28,0x2A)
    meta["metadata_sha256"] = sha(json.dumps([(k,sha(fields[k].raw())) for k in metadata_keys if k in fields],separators=(",",":")).encode())
    changes = []
    pack_changes = fields[0x26].array() if 0x26 in fields else {}
    for pack_node in pack_changes.values():
        t, p = pack_node.typed()
        if t != 0x401204 or set(p) != {t}:
            raise FormatError("unsupported pack change record")
        p = p[t].mapping()
        pack = required(p, 1, "pack change").string()
        for change_node in required(p, 2, "pack change").array().values():
            t, c = change_node.typed()
            if t != 0x401206 or set(c) != {t}:
                raise FormatError("unsupported resource change record")
            c = c[t].mapping()
            resource = Resource.parse(required(c, 1, "change"))
            locator = required(c, 3, "change").string()
            match = re.fullmatch(r"\[#([0-9A-Fa-f]{16})\](\d+)\\(\d+)", locator)
            if not match or int(match[1], 16) != resource.rid:
                raise FormatError("change locator disagrees with resource ID")
            changes.append({"pack": pack, "resource": resource,
                            "operation_raw": required(c, 2, "change").uint(4),
                            "locator": locator})
    fingerprint = []
    for change in changes:
        r = change["resource"]
        fingerprint.append((change["pack"].upper(), r.rid, r.type_id,
                            change["operation_raw"], sha(r.node.raw())))
    meta["overrides_sha256"] = sha(json.dumps(sorted(fingerprint), separators=(",", ":")).encode())
    return meta, changes


def read_rpk(path: Path) -> tuple[dict, list[Resource]]:
    data = path.read_bytes()
    if data.lstrip().startswith((b'msgid ', b'msgstr ')):
        raise FormatError("File has .rpk extension but contains gettext PO text")
    if len(data) < 0xB1 or data[:4] != b"RPK\0":
        raise FormatError("unsupported RPK signature")
    # Version 26 and the observed uncompressed header. Do not guess other layouts.
    if data[4:12] != b"\x1a\0\0\x04\0\0\0\0":
        raise FormatError("unsupported RPK header/version/compression")
    if struct.unpack_from("<I", data, 12)[0] != len(data) - 0xB0:
        raise FormatError("RPK payload length mismatch")
    fields = Node(data, 0xB0, len(data)).mapping()
    name = required(fields, 0x16, "RPK").string()
    resources = []
    for container in required(fields, 0x0D, "RPK").mapping().values():
        kind, wrapper = container.typed()
        if kind not in (0x400017F, 0x4000172, 0x4000173, 0x4000174, 0x4013404):
            raise FormatError(f"unsupported RPK container 0x{kind:X}")
        pack = required(wrapper, 0x33, "RPK container").mapping()
        for obj in required(pack, 0x15, "RPK objects").array().values():
            resources.append(Resource.parse(obj))
    return {"path": str(path), "name": name, "sha256": sha(data),
            "size": len(data), "resource_count": len(resources),
            "dependencies": string_list(fields[0x1E]) if 0x1E in fields else []}, resources


def decode_link(node: Node) -> dict | None:
    """Only two observed link encodings; IDs must agree with locators."""
    try:
        fields = node.mapping()
        if set(fields) == {0x14, 0x16, 0x17}:
            locator = fields[0x14].string()
            m = re.fullmatch(r"\[#([0-9A-Fa-f]{16})\]\d+\\\d+", locator)
            rid = fields[0x16].uint(8)
            if m and int(m[1], 16) == rid:
                return {"id": f"{rid:016X}", "name": fields[0x17].string(),
                        "locator": locator}
    except FormatError:
        pass
    try:
        kind, fields = node.typed()
        if kind == 0x40004A and set(fields) == {0x14, 0x16}:
            return {"id": f"{fields[0x14].uint(8):016X}",
                    "name": fields[0x16].string()}
    except FormatError:
        pass
    return None


def references(resource: Resource) -> list[dict]:
    links = []
    visited = set()

    def visit(node: Node, path: str, depth: int = 0):
        if depth > 24 or (node.start, node.end) in visited:
            return
        visited.add((node.start, node.end))
        link = decode_link(node)
        if link:
            if int(link["id"], 16):
                links.append({**link, "property": path, "offset": node.start})
            return
        # Length-prefixed strings can also resemble offset maps. They are
        # scalar data, never containers in which to discover resource links.
        try:
            node.string()
            return
        except FormatError:
            pass
        children = None
        try:
            kind, candidate = node.typed()
            if 0x10000 <= kind < 0x10000000:
                children = candidate
        except FormatError:
            pass
        if children is None:
            try:
                children = node.mapping()
            except FormatError:
                return  # opaque values remain opaque, not presumed reference-free
        for key, child in children.items():
            visit(child, path + f"/0x{key:X}", depth + 1)

    for key, node in resource.fields.items():
        if key >= 0x10000:
            visit(node, f"0x{key:08X}")
        elif key not in (0x13, 0x14, 0x15, 0x17, 0x18, 0x12C):
            # Some native resource classes serialize their gameplay members
            # flat (e.g. TSideUnitUpgradeProperty field 2). "meta" is the
            # existing show/leaves address syntax, not a semantic exclusion.
            visit(node, f"meta/0x{key:02X}")
    return links


def describe(raw: bytes | None) -> dict:
    if raw is None:
        return {"serialized": False, "meaning": "omitted; default semantics not inferred"}
    node = Node(raw, 0, len(raw))
    out = {"serialized": True, "size": len(raw), "sha256": sha(raw),
           "hex": raw.hex() if len(raw) <= 128 else raw[:64].hex() + "..."}
    link = decode_link(node)
    if link:
        out["resource_link"] = link
        return out
    if len(raw) in (1, 2, 4, 8):
        out["unsigned_integer_candidate"] = int.from_bytes(raw, "little")
    else:
        try:
            out["utf8"] = node.string()
        except FormatError:
            pass
    return out


def diff_fields(base: dict[str, bytes], modified: dict[str, bytes]) -> list[dict]:
    return [{"property": key, "base": describe(base.get(key)),
             "mod": describe(modified.get(key))}
            for key in sorted(base.keys() | modified.keys())
            if base.get(key) != modified.get(key)]


def game_build(game: Path) -> dict:
    manifest = game.parent.parent / "appmanifest_1669000.acf"
    result = {"path": str(game)}
    if manifest.is_file():
        m = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text(encoding="utf-8"), re.I)
        if m:
            result["steam_build_id"] = m[1]
    return result


def audit(acp: Path, game: Path, baseline: dict | None = None) -> tuple[dict, dict]:
    meta, changes = read_acp(acp)
    wanted_packs = {c["pack"].upper() for c in changes}
    wanted_ids = {c["resource"].rid for c in changes}
    base_by_pack = defaultdict(list)
    locations = defaultdict(list)
    candidate_bases = defaultdict(list)
    failures, touched_packs, local_resources = [], [], []
    counts = Counter()
    content = game / "Content"
    if not content.is_dir():
        raise FormatError(f"game Content directory not found: {content}")
    paths = [(p, "game") for p in sorted(content.rglob("*.rpk"))]
    paths += [(p, "mod") for p in sorted(acp.parent.rglob("*.rpk"))]
    for path, origin in paths:
        counts["pack_files"] += 1
        try:
            info, resources = read_rpk(path)
        except (FormatError, OSError) as exc:
            failures.append({"path": str(path), "error": str(exc)})
            continue
        counts["parsed_packs"] += 1
        counts["resources"] += len(resources)
        if origin == "game" and info["name"].upper() in wanted_packs:
            touched_packs.append(info)
        for resource in resources:
            location = {"pack": info["name"], "path": str(path), "origin": origin,
                        "name": resource.name, "type_id": f"0x{resource.type_id:08X}"}
            locations[resource.rid].append(location)
            if origin == "mod":
                local_resources.append((resource, location))
            if origin == "game" and resource.rid in wanted_ids:
                candidate_bases[resource.rid].append((resource, location))
                base_by_pack[(info["name"].upper(), resource.rid)].append((resource, location))
    if not counts["parsed_packs"]:
        raise FormatError("no supported RPK files parsed; cannot audit")
    baseline_entries = {}
    if baseline is not None:
        if baseline.get("format") != FORMAT or baseline.get("kind") != "baseline":
            raise FormatError("unsupported baseline format")
        if baseline.get("mod_sha256") != meta["sha256"]:
            raise FormatError("baseline belongs to different Mod bytes; capture a new baseline")
        baseline_entries = baseline.get("resources", {})
    records, snapshots = [], {}
    issues = []
    for change in changes:
        mod = change["resource"]
        key = change["pack"].upper() + ":" + f"{mod.rid:016X}"
        entry = {"pack": change["pack"], **mod.identity(),
                 "operation_raw": f"0x{change['operation_raw']:X}",
                 "stored_locator": change["locator"]}
        matches = base_by_pack.get((change["pack"].upper(), mod.rid), [])
        if len(matches) != 1:
            entry["status"] = "ambiguous_target" if matches else "target_not_found_in_pack"
            entry["other_locations"] = [loc for _, loc in candidate_bases.get(mod.rid, [])]
            issues.append({"resource": mod.name, "issue": entry["status"]})
        else:
            base, location = matches[0]
            entry["base_location"] = location
            entry["base_type_id"] = f"0x{base.type_id:08X}"
            entry["status"] = "id_and_type_match" if base.type_id == mod.type_id else "type_changed"
            if base.type_id != mod.type_id:
                issues.append({"resource": mod.name, "issue": "type_changed"})
            try:
                original = base.leaves()
                changed = mod.leaves()
                entry["differences"] = diff_fields(original, changed)
                snapshots[key] = {"identity": base.identity(), "location": location,
                                  "fields": {k: v.hex() for k, v in original.items()}}
                if baseline is not None:
                    old = baseline_entries.get(key)
                    if old is None:
                        entry["since_baseline"] = {"status": "not_in_baseline"}
                        issues.append({"resource": mod.name, "issue": "not_in_baseline"})
                    else:
                        old_fields = {k: bytes.fromhex(v) for k, v in old["fields"].items()}
                        upstream = diff_fields(old_fields, original)
                        overlaps = [d["property"] for d in upstream
                                    if changed.get(d["property"]) != original.get(d["property"])]
                        entry["since_baseline"] = {"changes_in_original": upstream,
                                                   "overridden_changed_fields": overlaps,
                                                   "original_type_changed": old["identity"]["type_id"] != f"0x{base.type_id:08X}"}
                        if overlaps:
                            issues.append({"resource": mod.name, "issue": "upstream_changes_overridden",
                                           "properties": overlaps})
            except FormatError as exc:
                entry["property_parse_error"] = str(exc)
                issues.append({"resource": mod.name, "issue": "unsupported_property_layout"})
        if change["operation_raw"] != 0x40:
            issues.append({"resource": mod.name, "issue": "unverified_change_operation",
                           "value": change["operation_raw"]})
        refs = references(mod)
        for ref in refs:
            hits = locations.get(int(ref["id"], 16), [])
            ref["status"] = "found" if hits else "not_found_in_scanned_packs"
            ref["locations"] = hits
            if not hits:
                issues.append({"resource": mod.name, "issue": "reference_not_found",
                               "reference_id": ref["id"], "name": ref["name"]})
        entry["references"] = refs
        records.append(entry)
    local_records = []
    for resource, location in local_resources:
        entry = {**resource.identity(), "location": location, "references": references(resource)}
        try:
            resource.leaves()
        except FormatError as exc:
            entry["property_parse_error"] = str(exc)
            issues.append({"resource": resource.name, "issue": "unsupported_local_property_layout"})
        for ref in entry["references"]:
            hits = locations.get(int(ref["id"], 16), [])
            ref["status"] = "found" if hits else "not_found_in_scanned_packs"
            ref["locations"] = hits
            if not hits:
                issues.append({"resource": resource.name, "issue": "reference_not_found",
                               "reference_id": ref["id"], "name": ref["name"]})
        local_records.append(entry)
    if not records and not local_records:
        issues.append({"issue": "no_resource_objects_checked",
                       "detail": "This may be a localization/asset-only package; those formats are not evaluated by this command."})
    limitations = [
        "Static structure only: gameplay, DLC ownership, load order and save compatibility are untested.",
        "Absent fields retain unknown default semantics; absence is not zero or a proven deletion.",
        "Reference checks cover two recognized encodings only; opaque fields may contain further links.",
        "Reference presence in installed packs does not prove it is loaded; other Mod dependencies are not applied.",
        "Differences against current original include intended changes; an old original baseline is needed to attribute update regressions.",
    ]
    if failures:
        issues.append({"issue": "incomplete_pack_index", "count": len(failures)})
    report = {"format": FORMAT, "tool_version": VERSION, "kind": "audit",
              "created_utc": datetime.now(timezone.utc).isoformat(),
              "mod": meta, "game": game_build(game),
              "summary": {"modified_packs": len(wanted_packs), "modified_resources": len(records),
                          "local_rpk_resources_checked": len(local_records),
                          "id_and_type_matches": sum(r["status"] == "id_and_type_match" for r in records),
                          "field_differences": sum(len(r.get("differences", [])) for r in records),
                          "recognized_references": sum(len(r["references"]) for r in records + local_records),
                          "issues": len(issues),
                          "conclusion": "not_evaluated" if not records and not local_records else
                                        ("review_required" if issues else "no_structural_issue_detected_gameplay_unverified")},
              "scan": {**counts, "failures": failures}, "touched_original_packs": touched_packs,
              "resources": records, "local_resources": local_records,
              "coverage": {"container_and_identity": "evaluated", "recognized_references_only": True,
                           "all_property_semantics": False, "active_load_order": False,
                           "localization": False, "gameplay": False},
              "issues": issues, "limitations": limitations}
    snapshot = {"format": FORMAT, "kind": "baseline", "created_utc": report["created_utc"],
                "mod_sha256": meta["sha256"], "game": report["game"], "resources": snapshots,
                "note": "Original fields for this exact Mod only, not a complete game or reference baseline."}
    # File writes are never part of parsing; verify the input remained the same.
    if sha(acp.read_bytes()) != meta["sha256"]:
        raise FormatError("Mod changed during audit; retry with a stable input")
    for pack in touched_packs:
        if sha(Path(pack["path"]).read_bytes()) != pack["sha256"]:
            raise FormatError("original pack changed during audit; retry after update completes")
    return report, snapshot


def inspect_file(path: Path) -> dict:
    if path.suffix.lower() == ".rpk":
        meta, resources = read_rpk(path)
        return {"file": meta, "resources": [r.identity() for r in resources]}
    meta, changes = read_acp(mod_file(path))
    return {"mod": meta, "changes": [{"pack": c["pack"], **c["resource"].identity(),
            "operation_raw": c["operation_raw"], "locator": c["locator"]} for c in changes]}


def scan_log(path: Path, limit: int) -> dict:
    # CLG is binary. These are string candidates, not decoded severity records.
    items = []
    seen = set()
    for m in re.finditer(rb"[\x20-\x7e]{8,}", path.read_bytes()):
        line = m.group().decode("ascii")
        if re.search(r"error|warning|assertion failed|cannot resolve|missing resource|broken|failed", line, re.I):
            if line not in seen:
                seen.add(line)
                items.append({"offset": m.start(), "text": line})
    return {"path": str(path), "matching_candidates": len(items), "items": items[-limit:],
            "limitation": "ASCII candidates only; not a complete CLG decoder or proof of Mod causality."}


def text_report(report: dict) -> str:
    s = report["summary"]
    lines = [f"{report['mod']['name']} | {s['modified_packs']} packs, {s['modified_resources']} resources",
             f"Original ID/type matches: {s['id_and_type_matches']}/{s['modified_resources']}; "
             f"field differences: {s['field_differences']}; issues: {s['issues']}",
             f"Index: {report['scan']['parsed_packs']}/{report['scan']['pack_files']} packs; "
             f"recognized references: {s['recognized_references']}"]
    if s.get("local_rpk_resources_checked"):
        lines.append(f"Local RPK resources checked: {s['local_rpk_resources_checked']}")
    if s["conclusion"] == "not_evaluated":
        lines.append("NOT EVALUATED: no supported resource objects were inspected.")
    for r in report["resources"]:
        lines.append(f"\n[{r['status']}] {r['name']} ({r['id']})")
        for d in r.get("differences", []):
            def brief(v):
                if not v["serialized"]:
                    return "<not serialized; default unknown>"
                if "resource_link" in v:
                    return v["resource_link"]["name"]
                return v.get("utf8", v["hex"])
            lines.append(f"  {d['property']}: {brief(d['base'])} -> {brief(d['mod'])}")
        if "since_baseline" in r:
            lines.append("  baseline: " + json.dumps(r["since_baseline"], ensure_ascii=False))
    for issue in report["issues"][:15]:
        lines.append("ISSUE " + json.dumps(issue, ensure_ascii=False))
    lines.append("\nStatic inspection only; gameplay and omitted-field defaults remain unverified.")
    return "\n".join(lines)


def write_json(path: Path, value: dict, protected: list[Path]):
    dest = path.resolve()
    if dest.suffix.lower() != ".json":
        raise FormatError("output must have .json extension")
    for root in protected:
        if dest == root.resolve() or root.resolve() in dest.parents:
            raise FormatError("output must be outside Mod and game directories")
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental replacement of an old baseline/report.
    with dest.open("x", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write("\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("inspect", help="read ACP changes or RPK resource identities")
    p.add_argument("path", type=Path)
    p = commands.add_parser("audit", help="compare Mod resources with installed original; no UI")
    p.add_argument("path", type=Path, help="Mod folder or ACP file")
    p.add_argument("--game", type=Path, default=DEFAULT_GAME)
    p.add_argument("--json", action="store_true", help="full JSON instead of compact text")
    p.add_argument("--output", type=Path, help="create a new JSON report outside inputs")
    p.add_argument("--snapshot", type=Path, help="create a new original baseline for future updates")
    p.add_argument("--baseline", type=Path, help="compare original against a prior snapshot")
    p = commands.add_parser("log", help="extract diagnostic string candidates from one CLG")
    p.add_argument("path", type=Path)
    p.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            print(json.dumps(inspect_file(args.path), ensure_ascii=False, indent=2))
        elif args.command == "log":
            if args.limit < 1:
                raise FormatError("limit must be positive")
            print(json.dumps(scan_log(args.path, args.limit), ensure_ascii=False, indent=2))
        else:
            acp = mod_file(args.path)
            baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
            report, snapshot = audit(acp, args.game.resolve(), baseline)
            protected = [acp.parent, args.game]
            if args.baseline:
                protected.append(args.baseline)
            if args.output:
                write_json(args.output, report, protected)
            if args.snapshot:
                write_json(args.snapshot, snapshot, protected)
            print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else text_report(report))
            return 1 if report["issues"] else 0
        return 0
    except (FormatError, OSError, ValueError, KeyError, struct.error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
